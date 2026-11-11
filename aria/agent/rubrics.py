"""
ARIA outcome rubrics (Outcomes/Grader upgrade, design
2026-11-08 — aria-audit/outcomes-grader-design-2026-11-08.md).

A rubric is the definition of "done" for one class of task:
named criteria the grader (aria.agent.grader) checks the recorded
evidence against, plus named failure cases — anti-outcomes the
grader must explicitly check for. `must` criteria gate the
verdict; `advisory` criteria are reported but never block.

Criterion scope decides which steps a failed criterion maps to
when an engine picks repair targets:
  "all"          — every step
  "last"         — the final step
  ["tool", ...]  — steps whose tool is in the list
(a bare tool-name string is also accepted and normalized to a
one-item list).

Rubrics are versioned data: a grade records rubric id + version,
so a past verdict stays reproducible after a rubric changes.
Validation is total — a malformed rubric NEVER raises and never
reaches a verify: it is dropped at load, selection falls back to
`general`, and if even `general` were malformed a minimal built-in
would serve instead (the constants below are validated by the
test suite, so that path is defence in depth, not expectation).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from aria.config import add_log

_KINDS = ("must", "advisory")


def _criterion(cid: str, text: str, kind: str, scope) -> dict:
    return {"id": cid, "text": text, "kind": kind, "scope": scope}


def _case(cid: str, text: str, scope="all") -> dict:
    return {"id": cid, "text": text, "scope": scope}


_RAW_RUBRICS: List[dict] = [
    {
        "id": "general",
        "version": 1,
        "task_types": ["general"],
        "criteria": [
            _criterion(
                "goal_accomplished",
                "The recorded evidence shows the goal itself was "
                "accomplished, not merely attempted or started.",
                "must", "all"),
            _criterion(
                "no_unresolved_failure",
                "No step's recorded output is a failure, a refusal, "
                "or an unresolved approval/input park.",
                "must", "all"),
            _criterion(
                "evidence_complete",
                "Every step has a recorded output the verdict can "
                "cite; nothing material rests on a missing or "
                "truncated result.",
                "advisory", "all"),
        ],
        "failure_cases": [
            _case(
                "claimed_not_done",
                "A step claims success but its output or a "
                "destination probe contradicts it (e.g. a write "
                "whose file probe shows missing or stale content)."),
            _case(
                "silent_partial",
                "Only part of the goal was done and the partial "
                "state is nowhere named in the results."),
        ],
    },
    {
        "id": "research",
        "version": 1,
        "task_types": ["research"],
        "criteria": [
            _criterion(
                "question_answered",
                "The final result answers the actual question "
                "asked, with a conclusion — not just collected "
                "material.",
                "must", "last"),
            _criterion(
                "sourced_claims",
                "Material claims rest on retrieved sources visible "
                "in the evidence (search / fetch outputs), not on "
                "bare assertion.",
                "must", "all"),
            _criterion(
                "uncertainty_named",
                "Gaps, conflicts between sources, or low-confidence "
                "points are named rather than smoothed over.",
                "advisory", "all"),
        ],
        "failure_cases": [
            _case(
                "unsourced_assertion",
                "Findings are stated with no source output anywhere "
                "in the evidence."),
            _case(
                "material_without_answer",
                "Sources were gathered but no synthesized answer "
                "was produced.", "last"),
        ],
    },
    {
        "id": "code_change",
        "version": 1,
        "task_types": ["code_change"],
        "criteria": [
            _criterion(
                "change_landed",
                "The intended change exists at the destination — "
                "the file probe shows the target file present with "
                "the expected content — not only in a tool's claim.",
                "must", ["write_file", "code_helper", "dev_agent"]),
            _criterion(
                "checked_after_edit",
                "After the edit, the recorded outputs show the "
                "change was exercised or checked (test run, import, "
                "compile, or read-back) when the plan included "
                "such a step.",
                "must", "all"),
            _criterion(
                "scope_discipline",
                "Only the intended files were changed; no unrelated "
                "edits appear in the evidence.",
                "advisory", "all"),
        ],
        "failure_cases": [
            _case(
                "edit_claimed_not_landed",
                "An edit tool claimed success but the file probe "
                "shows the old content or a missing file.",
                ["write_file", "code_helper", "dev_agent"]),
            _case(
                "unverified_breakage",
                "The change was left in a state the evidence shows "
                "failing (error output after the edit) with no "
                "later fix recorded."),
        ],
    },
]

# The last-resort rubric if every registered rubric failed
# validation: one honest must criterion, nothing else.
_MINIMAL_GENERAL: dict = {
    "id": "general",
    "version": 0,
    "task_types": ["general"],
    "criteria": [
        _criterion(
            "goal_accomplished",
            "The recorded evidence shows the goal itself was "
            "accomplished, not merely attempted or started.",
            "must", "all"),
    ],
    "failure_cases": [],
}


def _valid_scope(scope) -> Optional[Any]:
    if scope in ("all", "last"):
        return scope
    if isinstance(scope, str) and scope:
        return [scope]
    if isinstance(scope, list) and scope and \
            all(isinstance(t, str) and t for t in scope):
        return list(scope)
    return None


def validate_rubric(raw: Any) -> Optional[dict]:
    """Clean one rubric dict, or None when it is malformed in
    any way that could confuse a verify. Never raises."""
    try:
        if not isinstance(raw, dict):
            return None
        rid = str(raw.get("id") or "").strip()
        version = raw.get("version")
        if not rid or not isinstance(version, int) or version < 0:
            return None
        task_types = raw.get("task_types")
        if not isinstance(task_types, list) or not task_types or \
                not all(isinstance(t, str) and t for t in task_types):
            return None
        criteria = []
        seen = set()
        for c in raw.get("criteria") or []:
            if not isinstance(c, dict):
                return None
            cid = str(c.get("id") or "").strip()
            scope = _valid_scope(c.get("scope", "all"))
            if not cid or cid in seen or \
                    c.get("kind") not in _KINDS or scope is None or \
                    not str(c.get("text") or "").strip():
                return None
            seen.add(cid)
            criteria.append({
                "id": cid, "text": str(c["text"]).strip(),
                "kind": c["kind"], "scope": scope})
        if not criteria or \
                not any(c["kind"] == "must" for c in criteria):
            return None
        cases = []
        for fc in raw.get("failure_cases") or []:
            if not isinstance(fc, dict):
                return None
            fid = str(fc.get("id") or "").strip()
            scope = _valid_scope(fc.get("scope", "all"))
            if not fid or fid in seen or scope is None or \
                    not str(fc.get("text") or "").strip():
                return None
            seen.add(fid)
            cases.append({"id": fid,
                          "text": str(fc["text"]).strip(),
                          "scope": scope})
        return {"id": rid, "version": version,
                "task_types": list(task_types),
                "criteria": criteria, "failure_cases": cases}
    except Exception:
        return None


def _load_registry() -> Dict[str, dict]:
    by_type: Dict[str, dict] = {}
    for raw in _RAW_RUBRICS:
        clean = validate_rubric(raw)
        if clean is None:
            add_log(f"rubrics: dropped malformed rubric "
                    f"{(raw or {}).get('id')!r} at load")
            continue
        for t in clean["task_types"]:
            by_type[t] = clean
    if "general" not in by_type:
        add_log("rubrics: registry has no valid 'general' rubric; "
                "serving the minimal built-in")
        by_type["general"] = _MINIMAL_GENERAL
    return by_type


_REGISTRY: Dict[str, dict] = _load_registry()


def known_task_types() -> set:
    return set(_REGISTRY)


def get_rubric(task_type: Optional[str]) -> dict:
    """The rubric for a task type; unknown / missing types get
    `general`. Never raises, never returns a malformed rubric."""
    if task_type:
        rubric = _REGISTRY.get(str(task_type).strip())
        if rubric is not None:
            return rubric
    return _REGISTRY["general"]


# Tools whose presence marks a task as a code/file change, and
# the read-only research tools, for type inference when no
# planner task_type is available (missions carry none).
# Pass 36 #32: inference runs on EXECUTED actions, not tool
# names alone. A code change needs an executed write
# (write_file, dev_agent, run_python_code, or a code_helper
# write — or a write that demonstrably landed); a code_helper
# explain/fix is not one. Research needs an executed research
# tool; an all-read-only run of ordinary lookups (time, status,
# lists) is general, even when the goal is phrased as a
# question. The goal only speaks when there are no executed
# actions to judge from.
_CODE_WRITE_TOOLS = frozenset(
    {"write_file", "dev_agent", "run_python_code"})
_RESEARCH_TOOLS = frozenset({"web_search", "research", "fetch_url"})
_QUESTION_LIKE_RE = re.compile(
    r"\?\s*$|^(what|why|how|when|where|which|who|find|compare|"
    r"research|look\s+up|search)\b", re.IGNORECASE)
_WRITE_LANDED_RE = re.compile(
    r"\bsaved\b[^\n]{0,80}\.(py|js|ts|tsx|jsx|html|css|json|md|"
    r"txt|sh|rs|go|java|c|cpp)\b", re.IGNORECASE)


def _write_landed(step: dict) -> bool:
    """Did this step's recorded output show a file actually
    saved? The fallback signal for a code_helper write whose
    parameters no longer carry the action (older checkpoints).
    Claims only ever feed type inference, never verification."""
    return bool(_WRITE_LANDED_RE.search(
        str(step.get("output") or "")))


def infer_task_type(tools: List[str], goal: Optional[str] = None,
                    steps: Optional[List[dict]] = None) -> str:
    """Deterministic rubric selection from the executed steps.
    Explicit task_type (select_rubric) always wins over this;
    when no explicit type exists, the executed actions decide:
    a write makes it a code_change, a research tool makes it
    research, anything else is general — and an absent/invalid
    planner type is preserved as 'no classification' by the
    caller, never defaulted here. The goal only speaks when
    there are no executed actions to judge from."""
    names = [str(t or "") for t in tools or []]
    step_list = [s for s in (steps or []) if isinstance(s, dict)]
    if any(t in _CODE_WRITE_TOOLS for t in names):
        return "code_change"
    for s in step_list:
        if str(s.get("tool") or "") != "code_helper":
            continue
        params = s.get("parameters") or {}
        if isinstance(params, dict) and \
                str(params.get("action") or "").strip().lower() \
                == "write":
            return "code_change"
        if _write_landed(s):
            return "code_change"
    if any(t in _RESEARCH_TOOLS for t in names):
        return "research"
    if not names and goal and \
            _QUESTION_LIKE_RE.search(str(goal).strip()):
        # No executed actions to judge from — a question-like
        # goal is the only signal left. A question answered by
        # plain lookups (get_time, status) stays general: the
        # tools ran, and they were not research.
        return "research"
    return "general"


def select_rubric(task_type: Optional[str] = None,
                  tools: Optional[List[str]] = None,
                  goal: Optional[str] = None,
                  steps: Optional[List[dict]] = None) -> dict:
    """Explicit task_type wins; otherwise infer from the goal
    and the executed steps; otherwise general."""
    if task_type and str(task_type).strip() in _REGISTRY:
        return get_rubric(str(task_type).strip())
    if tools or steps:
        return get_rubric(infer_task_type(tools, goal=goal,
                                          steps=steps))
    return get_rubric("general")


def steps_in_scope(rubric: dict, entry_id: str,
                   steps: List[dict]) -> List[dict]:
    """The steps a failed criterion / failure case maps to, for
    repair targeting. `steps` are dicts with a "tool" key; the
    LAST step is the final element. Unknown ids map to nothing
    (callers fall back to their legacy targeting)."""
    entry = None
    for group in (rubric.get("criteria") or [],
                  rubric.get("failure_cases") or []):
        for item in group:
            if item.get("id") == entry_id:
                entry = item
                break
        if entry is not None:
            break
    if entry is None or not steps:
        return []
    scope = entry.get("scope", "all")
    if scope == "all":
        return list(steps)
    if scope == "last":
        return [steps[-1]]
    wanted = set(scope)
    return [s for s in steps if s.get("tool") in wanted]
