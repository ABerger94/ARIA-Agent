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

STORE_DIR = WORKSPACE_DIR
_SOURCES = ("email", "folder", "presence_return")
_DEFAULT_COOLDOWN_S = 300
_POLL_S = 45


# ---------------- store ----------------

def _triggers_path() -> str:
    return os.path.join(STORE_DIR, "triggers.json")


def _state_path() -> str:
    return os.path.join(STORE_DIR, "triggers_state.json")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _save_json(path, data) -> None:
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


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
        self._away_since = None

    # -- state helpers --
    def _state(self) -> dict:
        return _load_json(_state_path(),
                          {"seen_mail": [], "folder_snaps": {},
                           "last_fired": {}})

    # -- source checks: each returns a detail string or None --
    def _check_email(self, trigger, state):
        rule = trigger["rule"]
        seen = set(state.get("seen_mail", []))
        fresh_matches = []
        for msg in self._mail():
            key = (f"{msg.get('sender', '')}|{msg.get('subject', '')}|"
                   f"{msg.get('date', '')}")
            if key in seen:
                continue
            seen.add(key)
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
        state["seen_mail"] = list(seen)[-300:]
        if fresh_matches:
            first = fresh_matches[0] or "(no subject)"
            extra = (f" (+{len(fresh_matches) - 1} more)"
                     if len(fresh_matches) > 1 else "")
            return f"email: {first}{extra}"
        return None

    def _check_folder(self, trigger, state):
        from aria.inbox import INBOX_DIR
        rule = trigger["rule"]
        path = str(rule.get("path") or INBOX_DIR)
        pattern = str(rule.get("pattern") or "*")
        current = self._folder(path)
        snaps = state.setdefault("folder_snaps", {})
        key = str(trigger["id"])
        previous = snaps.get(key)
        snaps[key] = current
        if previous is None:
            return None  # first sight: baseline, never fire
        new_names = [n for n in current
                     if n not in previous
                     and fnmatch.fnmatch(n, pattern)]
        if new_names:
            return f"new file in {path}: {', '.join(sorted(new_names)[:3])}"
        return None

    def _check_presence(self, trigger, state):
        min_away = float(trigger["rule"].get("min_away_s") or 1800)
        present = bool(self._presence())
        now = self._now()
        if not present:
            if self._away_since is None:
                self._away_since = now
            return None
        if self._away_since is not None:
            away_for = now - self._away_since
            self._away_since = None
            if away_for >= min_away:
                return f"your return after {int(away_for // 60)} min away"
        return None

    def tick(self) -> list:
        """One pass over all enabled triggers. Returns firing records."""
        state = self._state()
        fired = []
        checks = {"email": self._check_email,
                  "folder": self._check_folder,
                  "presence_return": self._check_presence}
        for trigger in load_triggers():
            if not trigger.get("enabled", True):
                continue
            try:
                detail = checks[trigger["source"]](trigger, state)
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
