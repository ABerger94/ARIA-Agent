"""ARIA event triggers (Alek, approved 2026-10-28).

The task engine used to act only when asked or on a clock. Triggers
add the third door: *when X happens, run task Y* — as journaled
delegated tasks, through the same gates as everything else.

Sources (v1):
  * email           — a new inbox message matching a rule
                      (from_contains / subject_contains / any_contains,
                      case-insensitive; every provided field must match)
  * folder          — a new file lands in a watched folder
                      (rule.path, default her inbox; rule.pattern glob)
  * presence_return — you come back after an absence of at least
                      rule.min_away_s seconds (default 1800)

Modes:
  * auto — the task is delegated immediately (journaled, announced).
  * ask  — the firing is stashed as a pending approval for a
           delegate_task call; nothing runs until you approve it.

Safety shape: a per-trigger cooldown (default 300s) so a burst can
never storm the queue; every firing writes an Activity log line;
a broken source fails quiet and is retried next tick; presence
fails open (no monitor -> never away -> presence triggers simply
never fire). Destructive steps inside a triggered task still stop
at the confirm banner — triggers change WHEN tasks start, never
what they're allowed to do.

Store: WORKSPACE_DIR/triggers.json (definitions) and
triggers_state.json (seen mail, folder snapshots, cooldowns).
"""

import fnmatch
import json
import os
import threading
import time

from aria.config import WORKSPACE_DIR, add_log
from aria import workspaces, data_dir

STORE_DIR = WORKSPACE_DIR
_SOURCES = ("email", "folder", "presence_return")
_DEFAULT_COOLDOWN_S = 300
_POLL_S = 45


# ---------------- store ----------------

def _triggers_path() -> str:
    return workspaces.path('triggers.json', os.path.join(STORE_DIR, 'triggers.json'))


def _state_path() -> str:
    return workspaces.path('triggers_state.json', os.path.join(STORE_DIR, 'triggers_state.json'))


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _save_json(path, data) -> None:
    data_dir.write_json_atomic(path, data)


def load_triggers() -> list:
    return _load_json(_triggers_path(), [])


def _save_triggers(triggers: list) -> None:
    _save_json(_triggers_path(), triggers)


def create_trigger(name: str, source: str, task: str, rule=None,
                   mode: str = "ask") -> dict:
    """Validate + store one trigger. Returns {"ok", ...}."""
    name = (name or "").strip()
    source = (source or "").strip().lower()
    task = (task or "").strip()
    mode = (mode or "ask").strip().lower()
    rule = rule if isinstance(rule, dict) else {}
    if not name:
        return {"ok": False, "error": "a trigger needs a name."}
    if source not in _SOURCES:
        return {"ok": False,
                "error": f"source must be one of {', '.join(_SOURCES)}."}
    if not task:
        return {"ok": False, "error": "a trigger needs a task to run."}
    if mode not in ("auto", "ask"):
        return {"ok": False, "error": "mode must be 'auto' or 'ask'."}
    if source == "email" and not any(
            str(rule.get(k) or "").strip()
            for k in ("from_contains", "subject_contains", "any_contains")):
        return {"ok": False,
                "error": "an email trigger needs at least one matcher "
                         "(from_contains, subject_contains, any_contains)."}
    triggers = load_triggers()
    if any(t["name"].lower() == name.lower() for t in triggers):
        return {"ok": False, "error": f"a trigger named '{name}' exists."}
    tid = (triggers[-1]["id"] + 1) if triggers else 1
    entry = {"id": tid, "name": name, "source": source, "rule": rule,
             "task": task, "mode": mode, "enabled": True}
    triggers.append(entry)
    _save_triggers(triggers)
    add_log(f"[trigger] created #{tid} '{name}' ({source}, {mode}): {task}")
    return {"ok": True, "trigger": entry}


def delete_trigger(tid) -> bool:
    triggers = load_triggers()
    kept = [t for t in triggers if t["id"] != tid]
    if len(kept) == len(triggers):
        return False
    _save_triggers(kept)
    add_log(f"[trigger] deleted #{tid}")
    return True


def set_trigger_enabled(tid, enabled: bool) -> bool:
    triggers = load_triggers()
    for t in triggers:
        if t["id"] == tid:
            t["enabled"] = bool(enabled)
            _save_triggers(triggers)
            return True
    return False


# ---------------- default seams ----------------

def _default_mail_fetcher() -> list:
    from aria.mailview import fetch_messages
    result = fetch_messages(limit=20)
    if not result.get("ok"):
        return []
    return result.get("messages", [])


def _default_folder_lister(path: str) -> dict:
    out = {}
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                out[name] = os.path.getmtime(full)
    except OSError:
        pass
    return out


def _default_presence() -> bool:
    from aria import presence
    return presence.is_present()


def _announce(text: str) -> None:
    try:
        import aria.speech as _speech_mod
        _speech_mod.speak(text)
    except Exception:
        pass


def _default_fire(trigger: dict, detail: str) -> str:
    """Carry out one firing; returns a short outcome string."""
    goal = (f"[Trigger: {trigger['name']}] {trigger['task']} "
            f"(fired by {detail})")
    if trigger["mode"] == "ask":
        from aria import approval
        approval.request_approval(
            "delegate_task", {"goal": goal},
            desc=f"Trigger '{trigger['name']}' fired ({detail}). "
                 f"Run the task: {trigger['task']}")
        return "pending your approval"
    from aria.agent import taskqueue
    tid = taskqueue.get_queue().submit(
        goal, priority=taskqueue.TaskPriority.NORMAL, speak=_announce)
    return f"delegated as task {tid}"


# ---------------- the engine ----------------

class TriggerEngine:
    """One poll pass per tick(); all seams injectable for tests."""

    def __init__(self, *, mail_fetcher=None, folder_lister=None,
                 presence_fn=None, fire_fn=None, now_fn=None):
        self._mail = mail_fetcher or _default_mail_fetcher
        self._folder = folder_lister or _default_folder_lister
        self._presence = presence_fn or _default_presence
        self._fire = fire_fn or _default_fire
        self._now = now_fn or time.time
        self._away_since = {}
        self._tick_mail = None
        self._tick_present = None

    # -- state helpers --
    def _state(self) -> dict:
        return _load_json(_state_path(),
                          {"seen_mail": [], "folder_snaps": {},
                           "last_fired": {}})

    # -- source checks: each returns (detail, commit) --
    # detail is a string or None; commit is None or a callable
    # taking the state dict. B5#4: a check must NOT advance the
    # seen-cursor / folder snapshot itself — tick() applies the
    # commit only after the cooldown passed AND _fire returned,
    # so a match suppressed by the cooldown (or whose firing
    # raises) stays unseen / still-new and fires on a later tick
    # instead of being silently consumed.
    def _check_email(self, trigger, state):
        rule = trigger["rule"]
        # A rule may not consume another rule's messages. Seed old installs
        # from their legacy shared history, then retain independent histories.
        histories = state.setdefault('seen_mail_by_trigger', {})
        key = str(trigger['id'])
        ordered = list(dict.fromkeys(histories.get(key, state.get('seen_mail', []))))
        seen = set(ordered)
        fresh_matches = []
        if self._tick_mail is None:
            self._tick_mail = list(self._mail())
        for msg in self._tick_mail:
            message_key = (f"{msg.get('sender', '')}|{msg.get('subject', '')}|"
                   f"{msg.get('date', '')}")
            if message_key in seen:
                continue
            seen.add(message_key)
            ordered.append(message_key)
            sender = str(msg.get("sender", "")).lower()
            subject = str(msg.get("subject", "")).lower()
            ok = True
            if rule.get("from_contains"):
                ok = ok and str(rule["from_contains"]).lower() in sender
            if rule.get("subject_contains"):
                ok = ok and str(rule["subject_contains"]).lower() in subject
            if rule.get("any_contains"):
                needle = str(rule["any_contains"]).lower()
                ok = ok and (needle in sender or needle in subject)
            if ok:
                fresh_matches.append(str(msg.get("subject", "")))
        if fresh_matches:
            first = fresh_matches[0] or "(no subject)"
            extra = (f" (+{len(fresh_matches) - 1} more)"
                     if len(fresh_matches) > 1 else "")

            def _commit(st, _key=key, _ordered=ordered):
                st.setdefault('seen_mail_by_trigger', {})[_key] = \
                    _ordered[-300:]

            return f"email: {first}{extra}", _commit
        return None, None

    def _check_folder(self, trigger, state):
        from aria.inbox import INBOX_DIR
        rule = trigger["rule"]
        path = str(rule.get("path") or INBOX_DIR)
        pattern = str(rule.get("pattern") or "*")
        current = self._folder(path)
        snaps = state.setdefault("folder_snaps", {})
        key = str(trigger["id"])
        previous = snaps.get(key)
        if previous is None:
            # First sight: baseline, never fire. Recording the
            # baseline itself is safe — those files never fire by
            # design — so it commits here, not on a firing.
            snaps[key] = current
            return None, None
        new_names = [n for n in current
                     if n not in previous
                     and fnmatch.fnmatch(n, pattern)]
        if new_names:

            def _commit(st, _key=key, _current=current):
                st.setdefault("folder_snaps", {})[_key] = _current

            return (f"new file in {path}: "
                    f"{', '.join(sorted(new_names)[:3])}", _commit)
        return None, None

    def _check_presence(self, trigger, state):
        min_away = float(trigger["rule"].get("min_away_s") or 1800)
        if self._tick_present is None:
            self._tick_present = bool(self._presence())
        present = self._tick_present
        now = self._now()
        key = (workspaces.current(), trigger['id'])
        if not present:
            self._away_since.setdefault(key, now)
            return None, None
        since = self._away_since.pop(key, None)
        if since is not None:
            away_for = now - since
            if away_for >= min_away:
                return (f"your return after {int(away_for // 60)} min away",
                        None)
        return None, None

    def tick(self) -> list:
        """One pass over all enabled triggers. Returns firing records."""
        self._tick_mail = self._tick_present = None
        state = self._state()
        fired = []
        checks = {"email": self._check_email,
                  "folder": self._check_folder,
                  "presence_return": self._check_presence}
        for trigger in load_triggers():
            if not trigger.get("enabled", True):
                continue
            try:
                detail, commit = checks[trigger["source"]](trigger, state)
            except Exception as exc:  # a broken source fails quiet
                add_log(f"[trigger] '{trigger['name']}' check failed: {exc}")
                continue
            if not detail:
                continue
            last = float(state.get("last_fired", {}).get(
                str(trigger["id"]), 0))
            if self._now() - last < _DEFAULT_COOLDOWN_S:
                continue
            try:
                outcome = self._fire(trigger, detail)
            except Exception as exc:
                add_log(f"[trigger] '{trigger['name']}' firing failed: {exc}")
                continue
            # B5#4: only now — cooldown passed and _fire returned —
            # does the match get consumed (seen-cursor / snapshot).
            if commit is not None:
                commit(state)
            state.setdefault("last_fired", {})[str(trigger["id"])] = \
                self._now()
            add_log(f"[trigger] '{trigger['name']}' fired ({detail}) "
                    f"-> {outcome}")
            fired.append({"trigger": trigger["name"], "detail": detail,
                          "outcome": outcome})
        _save_json(_state_path(), state)
        return fired


# ---------------- lifecycle ----------------

_ENGINE_THREAD = None


def start_triggers() -> None:
    """Start the poll loop on a daemon thread (idempotent)."""
    global _ENGINE_THREAD
    if _ENGINE_THREAD is not None:
        return
    engine = TriggerEngine()

    def _loop():
        while True:
            try:
                for project in workspaces.list_projects():
                    with workspaces.use(project['id']):
                        engine.tick()
            except Exception as exc:
                add_log(f"[trigger] engine tick failed: {exc}")
            time.sleep(_POLL_S)

    _ENGINE_THREAD = threading.Thread(target=_loop, daemon=True,
                                      name="aria-triggers")
    _ENGINE_THREAD.start()
    add_log("[trigger] engine started")


# ---------------- the tool ----------------

def tool_event_trigger(args) -> str:
    args = args or {}
    action = str(args.get("action") or "list").strip().lower()
    if action == "create":
        rule = args.get("rule")
        if isinstance(rule, str):
            try:
                rule = json.loads(rule)
            except Exception:
                return ("event_trigger: 'rule' must be an object "
                        "(or a JSON string of one).")
        result = create_trigger(str(args.get("name") or ""),
                                str(args.get("source") or ""),
                                str(args.get("task") or ""),
                                rule=rule,
                                mode=str(args.get("mode") or "ask"))
        if not result["ok"]:
            return f"event_trigger: {result['error']}"
        t = result["trigger"]
        return (f"Trigger #{t['id']} created: '{t['name']}' — when "
                f"{t['source']} matches, run: {t['task']} "
                f"(mode: {t['mode']}).")
    if action == "list":
        triggers = load_triggers()
        if not triggers:
            return "No event triggers defined."
        lines = ["Event triggers:"]
        for t in triggers:
            state = "on" if t.get("enabled", True) else "OFF"
            lines.append(f"#{t['id']} [{state}] '{t['name']}' — "
                         f"{t['source']} {t['rule']} -> {t['task']} "
                         f"({t['mode']})")
        return "\n".join(lines)
    if action in ("delete", "enable", "disable"):
        try:
            tid = int(args.get("id"))
        except (TypeError, ValueError):
            return "event_trigger: that action needs a numeric 'id'."
        if action == "delete":
            return (f"Trigger #{tid} deleted."
                    if delete_trigger(tid)
                    else f"No trigger with id {tid}.")
        ok = set_trigger_enabled(tid, action == "enable")
        return (f"Trigger #{tid} {'enabled' if action == 'enable' else 'disabled'}."
                if ok else f"No trigger with id {tid}.")
    return (f"event_trigger: unknown action '{action}' — "
            "use create, list, delete, enable, or disable.")
