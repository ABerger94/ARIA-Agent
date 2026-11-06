"""Skills v1 — the skill tools and autonomous authoring.

Tools (registered in dispatch, declared in schemas):
    skill_propose   draft a skill from instructions, run the gate
    skill_list      every skill with status + scan state
    skill_show      full instructions + resources + scan state;
                    for an ACTIVE skill this also arms its per-turn
                    tool-call budget (the runtime load)
    skill_activate  internal activation — enforces the gate's
                    passing-scan invariant (approval replays land
                    here in confirm modes)
    skill_retire    move a skill to _retired/, effective instantly

Autonomous authoring (Alek's explicit addition, 2026-11-04):
    During idle consolidation ONLY, derive_patterns() looks at the
    chat rows and spine events the pass just consumed and surfaces
    CANDIDATE patterns with real counts (a tool used >=4 times in
    the recent spine window; the same user instruction given >=2
    times). draft_from_pattern() is the bounded entry point: it
    REFUSES without evidence (pattern text + count >= 2), refuses
    past the durable daily cap (2/day, skills/_drafts/.autolog.json),
    dedupes by name/intent, stamps the evidence into the draft
    frontmatter, logs an Activity line stating the reason, and runs
    the same scan gate as a conversational draft. Authoring creates
    DRAFTS only — activation follows the normal approval routing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from aria.config import add_log
from aria.tools import skill_gate as gate
from aria.tools import skill_store as store


# --- shared guards ------------------------------------------------------

def _disabled_msg() -> Optional[str]:
    if store.skills_enabled():
        return None
    return ("File-based skills are disabled in settings "
            "(skills.enabled=False); only the built-in run_skill "
            "workflows are available.")


def _scan_state(skill: Dict[str, Any]) -> str:
    scan = store.read_scan(skill["folder"])
    if not scan:
        return "not scanned"
    state = scan.get("status", "?")
    if skill["status"] == "active":
        # The recorded status alone is not the state: a
        # post-activation edit leaves "pass" on record while the
        # pass no longer matches the live content (pass 25).
        if not store.scan_is_current(skill):
            return f"scan {state} (activated, stale)"
        return f"scan {state} (activated)"
    return f"scan {state}"


# --- tools ----------------------------------------------------------------

def tool_skill_propose(args: Dict[str, Any]) -> str:
    disabled = _disabled_msg()
    if disabled:
        return disabled
    name = store.normalize_name((args or {}).get("name"))
    if not name:
        return ("skill_propose needs a usable name (letters, digits, "
                "hyphens; it is slugified).")
    description = str((args or {}).get("description") or "").strip()
    instructions = str((args or {}).get("instructions") or "")
    if not description:
        return "skill_propose needs a one-line description (the catalog line)."
    if not instructions.strip():
        return "skill_propose needs the skill's instruction text."
    resources: Optional[Dict[str, str]] = None
    raw_resources = (args or {}).get("resources_json")
    if raw_resources:
        try:
            parsed = json.loads(raw_resources)
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
            resources = {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            return ("skill_propose: resources_json must be a JSON "
                    "object mapping filename to text content.")
    existing = store.find_skill(name)
    if existing is not None and existing["status"] == "active":
        return (f"A skill named '{name}' is already active. Retire "
                "it first if it should be replaced.")
    note = ""
    if existing is not None and existing["status"] == "draft":
        note = (f"Draft '{name}' already exists; re-running the "
                "scan gate on it unchanged. ")
    else:
        ok, msg = store.write_draft(
            name, description, instructions,
            budget=(args or {}).get("budget"),
            resources=resources, origin="conversation")
        if not ok:
            return msg
        add_log(f"Skill draft proposed: '{name}' — {description}")
    return note + gate.gate_and_route(name)


def tool_skill_list(args: Dict[str, Any]) -> str:
    disabled = _disabled_msg()
    if disabled:
        return disabled
    skills = store.list_skills()
    if not skills:
        return ("No file-based skills yet. skill_propose drafts one; "
                "the built-in run_skill workflows are unaffected.")
    lines = ["File-based skills:"]
    for s in skills:
        lines.append(f"• {s['name']} [{s['status']}] "
                     f"({_scan_state(s)}): {s['description']}")
    return "\n".join(lines)


def tool_skill_show(args: Dict[str, Any]) -> str:
    disabled = _disabled_msg()
    if disabled:
        return disabled
    name = store.normalize_name((args or {}).get("name"))
    skill = store.find_skill(name) if name else None
    if skill is None:
        return f"No skill named '{(args or {}).get('name')}'."
    lines = [f"Skill '{skill['name']}' [{skill['status']}] "
             f"— {_scan_state(skill)}",
             f"Purpose: {skill['description']}",
             f"Tool-call budget when in use: {skill['budget']}"]
    if skill["origin"]:
        lines.append(f"Origin: {skill['origin']}")
    if skill["evidence_pattern"]:
        lines.append(f"Evidence: {skill['evidence_pattern']} "
                     f"(x{skill['evidence_count']})")
    lines.append("")
    lines.append("Instructions:")
    lines.append(skill["instructions"])
    for fname in sorted(skill["resources"]):
        lines.append(f"\nResource {fname}:")
        lines.append(skill["resources"][fname])
    extra = [f for f in skill["resource_files"]
             if f not in skill["resources"]]
    if extra:
        lines.append(f"\n(Unreadable/non-text resources: "
                     f"{', '.join(extra)})")
    if skill["status"] == "active" and store.scan_is_current(skill):
        armed = store.arm_skill_run(skill["name"], skill["budget"])
        lines.append(f"\n[Loaded for this turn: follow these "
                     f"instructions with your normal tools. Budget: "
                     f"{armed} tool calls.]")
    elif skill["status"] == "active":
        # Post-activation edit: the catalog already dropped it
        # (pass 21); skill_show must not be a back door that arms
        # the changed instructions. Shown for review only, like a
        # draft — retire + re-propose is the recovery path.
        lines.append("\n[Not loaded — this skill changed after its "
                     "scan, so the pass is stale; these instructions "
                     "are shown for review only. Retire it and "
                     "re-propose to re-scan.]")
    else:
        lines.append("\n[Not active — these instructions are shown "
                     "for review only.]")
    return "\n".join(lines)


def tool_skill_activate(args: Dict[str, Any]) -> str:
    name = store.normalize_name((args or {}).get("name"))
    if not name:
        return "skill_activate needs a skill name."
    # Pass the parked revision through: an approval replay
    # carries the revision frozen at park time, and activate_skill
    # refuses a draft that no longer matches it (pass 25).
    return gate.activate_skill(name,
                               revision=(args or {}).get("revision"),
                               description=(args or {}).get("description"))


def tool_skill_retire(args: Dict[str, Any]) -> str:
    disabled = _disabled_msg()
    if disabled:
        return disabled
    name = store.normalize_name((args or {}).get("name"))
    if not name:
        return "skill_retire needs a skill name."
    ok, msg = store.retire_skill(name)
    return msg


# --- autonomous authoring ---------------------------------------------------

MIN_TOOL_REPEATS = 4
MIN_INSTRUCTION_REPEATS = 2


def _slug_from_text(text: str, fallback: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())[:4]
    slug = store.normalize_name("-".join(words)) if words else None
    return slug or store.normalize_name(fallback) or "learned-workflow"


def derive_patterns(chat_rows: List[Tuple],
                    spine_events: List[Dict[str, Any]],
                    ) -> List[Dict[str, Any]]:
    """Surface candidate patterns WITH counts from data a
    consolidation pass already holds. Pure and honest: every
    candidate carries the exact pattern text and occurrence count
    that evidenced it; nothing is inferred beyond repetition."""
    patterns: List[Dict[str, Any]] = []
    tool_counts: Dict[str, int] = {}
    for evt in spine_events or []:
        if isinstance(evt, dict) and evt.get("type") == "tool":
            tname = str(evt.get("name") or "").strip()
            if tname:
                tool_counts[tname] = tool_counts.get(tname, 0) + 1
    for tname, count in sorted(tool_counts.items(),
                               key=lambda kv: -kv[1]):
        if count >= MIN_TOOL_REPEATS:
            patterns.append({
                "kind": "repeated_tool",
                "pattern": (f"tool '{tname}' used {count} times in "
                            "recent activity"),
                "count": count,
                "suggested_name": _slug_from_text(tname + " routine",
                                                  "tool-routine"),
                "subject": tname,
            })
    instruction_counts: Dict[str, int] = {}
    examples: Dict[str, str] = {}
    for row in chat_rows or []:
        try:
            _id, _sender, message = row[0], row[1], row[2]
        except (TypeError, IndexError):
            continue
        norm = re.sub(r"\s+", " ", str(message or "")).strip().lower()
        if len(norm) < 12:
            continue
        instruction_counts[norm] = instruction_counts.get(norm, 0) + 1
        examples.setdefault(norm, str(message).strip()[:200])
    for norm, count in sorted(instruction_counts.items(),
                              key=lambda kv: -kv[1]):
        if count >= MIN_INSTRUCTION_REPEATS:
            patterns.append({
                "kind": "repeated_instruction",
                "pattern": f"instruction given {count} times: "
                           f"{examples[norm][:120]}",
                "count": count,
                "suggested_name": _slug_from_text(norm,
                                                  "repeated-request"),
                "subject": examples[norm],
            })
    return patterns


_INTENT_STOPWORDS = {
    "instruction", "given", "times", "used", "recent", "activity",
    "repeated", "tool", "workflow", "learned", "from", "with",
    "that", "this", "please",
}


def _intent_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower())
            if w not in _INTENT_STOPWORDS}


def _intent_taken(name: str, intent_text: str) -> bool:
    """Dedupe by name and by intent: an existing draft/active skill
    with the same name, or whose description already covers the
    pattern's subject words, blocks a duplicate draft. Evidence
    boilerplate ('instruction given 3 times:') is stopworded out so
    two genuinely different chores never dedupe against each other
    on the framing alone."""
    subject_words = _intent_words(intent_text)
    for skill in store.list_skills():
        if skill["status"] == "retired":
            continue
        if skill["name"] == name:
            return True
        desc_words = _intent_words(skill["description"] or "")
        if subject_words and len(subject_words & desc_words) >= 2:
            return True
    return False


def draft_from_pattern(pattern: Dict[str, Any]) -> Tuple[bool, str]:
    """The bounded autonomous-drafting entry point. Refuses —
    loudly, in the return string — when the pattern carries no
    evidence, the kill switches are off, the daily cap is reached,
    the intent is already covered, or no instructions were drafted.
    On success the draft is written with its evidence stamped in
    the frontmatter, counted in the durable autolog, announced in
    the Activity log, and run through the normal scan gate."""
    if not store.autonomous_authoring_enabled():
        return False, ("autonomous authoring is disabled "
                       "(skills.autonomous_authoring / enabled).")
    pattern_text = str((pattern or {}).get("pattern") or "").strip()
    try:
        count = int((pattern or {}).get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if not pattern_text or count < MIN_INSTRUCTION_REPEATS:
        return False, ("refused: an autonomous draft requires "
                       "evidence — a pattern description and an "
                       "occurrence count of at least "
                       f"{MIN_INSTRUCTION_REPEATS}.")
    instructions = str((pattern or {}).get("instructions") or "").strip()
    if not instructions:
        return False, ("refused: no instructions were drafted for "
                       "this pattern.")
    if store.autonomous_count_today() >= store.AUTONOMOUS_DAILY_CAP:
        return False, (f"refused: daily autonomous-draft cap "
                       f"({store.AUTONOMOUS_DAILY_CAP}) reached.")
    name = store.normalize_name((pattern or {}).get("suggested_name")) \
        or _slug_from_text(pattern_text, "learned-workflow")
    intent_text = str((pattern or {}).get("subject") or pattern_text)
    if _intent_taken(name, intent_text):
        return False, (f"skipped: a draft or active skill already "
                       f"covers this pattern ('{name}').")
    description = (str((pattern or {}).get("description") or "").strip()
                   or f"Learned workflow: {pattern_text[:90]}")
    ok, msg = store.write_draft(
        name, description, instructions, origin="autonomous",
        evidence={"pattern": pattern_text, "count": count})
    if not ok:
        return False, msg
    total = store.record_autonomous_draft(
        name, f"autonomous draft from consolidation: {pattern_text}")
    add_log(f"Autonomous skill draft '{name}' ({total} today): "
            f"{pattern_text}")
    routed = gate.gate_and_route(name)
    return True, f"autonomous draft '{name}' created. {routed}"


def maybe_autonomous_drafts(chat_rows: List[Tuple],
                            spine_events: List[Dict[str, Any]],
                            ) -> List[str]:
    """Consolidation-facing hook (called by proactive's idle loop
    after reconcile). Derives patterns, drafts instructions for at
    most ONE pattern per pass via a light provider call, and hands
    it to draft_from_pattern. Returns outcome strings for the log.
    Bounded: one drafting call per pass; the entry point enforces
    evidence, dedupe, and the daily cap."""
    outcomes: List[str] = []
    if not store.autonomous_authoring_enabled():
        return outcomes
    patterns = derive_patterns(chat_rows, spine_events)
    for pattern in patterns:
        drafted = _draft_instructions(pattern)
        if not drafted:
            continue
        pattern["instructions"] = drafted["instructions"]
        if drafted.get("description"):
            pattern["description"] = drafted["description"]
        ok, msg = draft_from_pattern(pattern)
        outcomes.append(msg)
        break  # one drafting attempt per consolidation pass
    return outcomes


def _draft_instructions(pattern: Dict[str, Any],
                        ) -> Optional[Dict[str, str]]:
    """Ask the default provider chain to turn an evidenced pattern
    into skill instructions. None when no provider answers (the
    pattern is simply not drafted this pass — never faked)."""
    try:
        from aria.agent.providers import provider_text
    except Exception:
        return None
    system = (
        "You write a short reusable skill for a personal assistant. "
        "Given an observed repeated pattern, reply with two blocks: "
        "a line 'DESCRIPTION: <one line>' then 'INSTRUCTIONS:' and "
        "numbered steps the assistant can follow with its normal "
        "tools. Plain text only, under 30 lines. Never include "
        "shell commands, code execution, credentials, or anything "
        "outside everyday assistant work.")
    prompt = (f"Observed pattern (evidence): {pattern.get('pattern')}\n"
              f"Subject: {pattern.get('subject', '')}\n"
              "Write the skill.")
    try:
        text = provider_text(system, [{"role": "user",
                                       "parts": [{"text": prompt}]}])
    except Exception:
        return None
    if not text or "INSTRUCTIONS:" not in text:
        return None
    description = ""
    m = re.search(r"DESCRIPTION:\s*(.+)", text)
    if m:
        description = m.group(1).strip()[:160]
    instructions = text.split("INSTRUCTIONS:", 1)[1].strip()
    if not instructions:
        return None
    return {"instructions": instructions, "description": description}
