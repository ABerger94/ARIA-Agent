"""
ARIA delegated-task planner (Merger Phase 3 — blueprint SS2.1, SS3.3).

Pattern reimplemented from Mark-XXXIX-OR's agent/planner.py
(FatihMakes, CC BY-NC 4.0) in ARIA's idiom: a plan is JSON —
{"goal", "task_type", "steps": [{"step", "tool", "description",
"parameters", "critical"}]} — produced in one call, validated,
clamped to PLAN_MAX_STEPS, with a deterministic single-step
fallback when planning fails. task_type is optional, validated
against the rubric registry (aria.agent.rubrics); an absent or
invalid type stays absent (None) so the grader infers from the
executed steps instead of defaulting to "general". On a
research-shaped goal the fallback is a single web_search scoped
to the research intent; any other goal raises a typed
PlanningFailed — a private goal is never sent to a search
backend.
Differences from his planner:

  * the model is ARIA's background pool (orpool, Phase 2), never
    the live conversation chain and never a direct Gemini call;
  * the prompt's tool catalogue is ARIA's REAL registry (names +
    descriptions from aria.tools.schemas) — his prompt hardcodes
    his action modules, including cmd_control, which does not
    exist even in his own tree;
  * a step naming an unknown tool is DROPPED and the drop recorded
    in plan["warnings"] (the executor independently hard-aborts
    an unknown tool that somehow reaches it — defence in depth);
  * there is NO generated-code step type and no rewrite-to-search
    trick for one. Nothing an LLM writes is ever executed as code
    in this codebase (blueprint SS2.1 ban).

The LLM callable is injectable: llm(prompt, system) -> raw text.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Callable, List, Optional

from aria import config as _config
from aria.config import add_log

_FENCE_RE = re.compile(r"```(?:json)?")


def _plan_max_steps() -> int:
    return int(getattr(_config, "PLAN_MAX_STEPS", 5))


def _tool_catalogue() -> str:
    """ARIA's real tools, one line each, for the planner prompt."""
    try:
        from aria.tools.schemas import get_tool_decls_by_name
        decls = get_tool_decls_by_name()
        lines = []
        for name in sorted(decls):
            desc = (decls[name].get("description") or "").strip()
            desc = " ".join(desc.split())[:140]
            lines.append(f"{name} — {desc}" if desc else name)
        if lines:
            return "\n".join(lines)
    except Exception:
        pass
    try:
        from aria.tools.dispatch import tool_names
        return "\n".join(tool_names())
    except Exception:
        return "web_search"


def known_tools() -> set:
    try:
        from aria.tools.dispatch import tool_names
        return set(tool_names())
    except Exception:
        return set()


def _system_prompt() -> str:
    return (
        "You are the planning module of ARIA, a personal AI assistant.\n"
        "Break the user's goal into a sequence of steps using ONLY "
        "the tools listed below.\n\n"
        "ABSOLUTE RULES:\n"
        f"- Max {_plan_max_steps()} steps. Use the minimum steps needed.\n"
        "- Use ONLY tool names from the list. A step naming any other "
        "tool will be dropped.\n"
        "- There is no code-generation tool. Never invent one.\n"
        "- Steps are independent, EXCEPT that a parameter value may "
        "contain {prev} (the previous step's result) or {stepN} "
        "(step N's result), which the executor substitutes.\n"
        "- Mark a step critical=true only when the task fails "
        "without it.\n"
        "- When later steps depend on what an earlier step returns "
        "(which files exist, what a search found), make that step "
        "the LAST step of the plan and mark it checkpoint=true: "
        "after it runs you will be shown its result and asked to "
        "plan the remaining work. Never plan past a checkpoint "
        "step.\n"
        "- Set task_type by the goal's dominant work: "
        "\"research\" (gathering and answering from sources), "
        "\"code_change\" (editing code or files), or \"general\" "
        "(anything else). The outcome grader selects its rubric "
        "by it.\n\n"
        "AVAILABLE TOOLS:\n"
        f"{_tool_catalogue()}\n\n"
        "OUTPUT — return ONLY valid JSON, no markdown, no explanation:\n"
        "{\n"
        '  "goal": "...",\n'
        '  "task_type": "general",\n'
        '  "steps": [\n'
        '    {"step": 1, "tool": "tool_name", "description": "what this step does",\n'
        '     "parameters": {}, "critical": true, "checkpoint": false}\n'
        "  ]\n"
        "}")


def _local_only() -> bool:
    """Live PROVIDER_MODE, same read as the pool gate (pass 36
    #01). Never raises."""
    try:
        from aria.agent.providers import provider_mode as _pm
        return _pm() == "local_only"
    except Exception:
        pass
    try:
        return str(getattr(_config, "PROVIDER_MODE", "auto")
                   or "auto").strip().lower() == "local_only"
    except Exception:
        return False


_LAST_WORKER_MODEL: Optional[str] = None


def last_worker_model() -> Optional[str]:
    """Model that answered the most recent default planning
    call (via chat_traced), or None when the last call used
    the local fallback or never ran. The taskexec verifier
    passes this to the grader as the worker model; None means
    unknown provenance, never a presumed roster head (pass 36
    #29)."""
    return _LAST_WORKER_MODEL


def _default_llm(prompt: str, system: str) -> str:
    """Default planning LLM: the background pool, traced so the
    answering model is recorded as the worker model. In Local
    only mode the pool is unavailable by design (pass 36 #01),
    so planning routes to the local leg through provider_text
    instead of failing open to a web search — the worker model
    is then unknown (provider_text reports no model identity)
    and last_worker_model() returns None."""
    global _LAST_WORKER_MODEL
    from aria.agent.orpool import get_pool, OrPoolUnavailable
    try:
        text, model = get_pool().chat_traced(
            prompt, system=system, max_tokens=2048, temperature=0.2)
    except OrPoolUnavailable:
        if not _local_only():
            raise
        from aria.agent.providers import provider_text
        _LAST_WORKER_MODEL = None
        return provider_text(system, prompt)
    _LAST_WORKER_MODEL = model or None
    return text


def _valid_task_type(raw) -> Optional[str]:
    """A plan's task_type, validated against the rubric
    registry (Outcomes/Grader upgrade). Pass 36 #32: an absent
    or invalid type is preserved as NO classification (None) —
    the grader then infers from the goal and the executed
    steps — instead of silently becoming "general", which
    suppressed inference. Never raises."""
    try:
        from aria.agent import rubrics
        value = str(raw or "").strip()
        if value in rubrics.known_task_types():
            return value
    except Exception:
        pass
    return None


class PlanningFailed(Exception):
    """Typed planning failure: no usable plan exists and the goal
    does not authorize a search fallback. Carries the reason so
    the executor can park/abort with a clear message instead of
    sending the goal to a search backend (pass 36 #33)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _new_plan_rev() -> str:
    """A fresh plan-revision id. Every plan (and every replan)
    gets its own revision; steps carry stable ids under it, so a
    resume can never confuse a replacement step with the step it
    replaced (pass 36 #26)."""
    return uuid.uuid4().hex[:12]


def _new_step_id() -> str:
    return uuid.uuid4().hex


# Explicit research phrasing that authorizes a search fallback
# (pass 36 #33). A goal that merely happens to mention one of
# these words in passing is not research-shaped; the markers are
# leading intent phrases, matched case-insensitively.
_RESEARCH_PREFIXES = (
    "research ", "research:", "look up ", "look into ",
    "find out about ", "find out ", "search for ",
    "search the web for ", "investigate ",
    "gather information about ", "gather information on ",
    "find information about ", "find information on ",
    "learn about ",
)


def _research_query(goal: str) -> Optional[str]:
    """The scoped search query for an explicitly research-shaped
    goal, or None when the goal does not authorize a search.
    The query is the research intent with its framing verb
    stripped — never the raw goal, so private framing around
    the question is not sent to a search backend."""
    text = (goal or "").strip()
    lowered = text.lower()
    for prefix in _RESEARCH_PREFIXES:
        if lowered.startswith(prefix):
            query = text[len(prefix):].strip(" :")
            return query or None
    return None


def _fallback_plan(goal: str) -> dict:
    """Research-only deterministic fallback: one critical
    web_search step scoped to the research intent. Raises
    PlanningFailed for any goal that is not explicitly
    research-shaped — a private goal must never be sent to a
    search backend as a stand-in plan (pass 36 #33)."""
    query = _research_query(goal)
    if query is None:
        raise PlanningFailed(
            "no usable plan could be made and the goal is not "
            "phrased as research, so there is no safe fallback")
    return {
        "goal": goal,
        "task_type": "research",
        "plan_rev": _new_plan_rev(),
        "steps": [{
            "id": _new_step_id(),
            "step": 1,
            "tool": "web_search",
            "description": f"Search for: {query}",
            "parameters": {"query": query},
            "critical": True,
        }],
        "warnings": ["planner fell back to a single scoped "
                     "search step (research-shaped goal)"],
    }


def _parse_plan(raw: str, goal: str) -> Optional[dict]:
    try:
        text = _FENCE_RE.sub("", str(raw or "")).strip().rstrip("`").strip()
        plan = json.loads(text)
    except Exception:
        return None
    if not isinstance(plan, dict):
        return None
    if "steps" not in plan or not isinstance(plan["steps"], list):
        return None
    return plan


def _sanitize(plan: dict, goal: str,
              start: int = 1) -> Optional[dict]:
    """Validate + clamp a parsed plan. Returns None when nothing
    usable remains (caller falls back).

    start is the first display number: a replan or continuation
    numbers its steps AFTER the steps already completed, so a
    display number ({stepN}, the verifier view, the gates) names
    exactly one step across plan revisions. A checkpoint step
    ends the plan — anything planned past it is dropped, because
    the remaining work is planned from its result."""
    known = known_tools()
    warnings: List[str] = list(plan.get("warnings") or [])
    steps: List[dict] = []
    raw_steps = plan.get("steps") or []
    for pos, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            continue
        tool = str(raw_step.get("tool") or "").strip()
        if known and tool not in known:
            warnings.append(f"dropped step with unknown tool '{tool}'")
            continue
        # Pass 36 #26: each step carries a stable id independent
        # of its display number ("step"), which is rendering
        # only. A replan renumbers from 1; the id is what resume
        # skips, results, receipts, and substitution key by, so
        # a replacement step is never confused with the step it
        # replaced. A step that already carries an id (a plan
        # re-sanitized, or a hand-built plan) keeps it.
        steps.append({
            "id": str(raw_step.get("id") or _new_step_id()),
            "step": start + len(steps),
            "tool": tool,
            "description": str(raw_step.get("description") or ""),
            "parameters": raw_step.get("parameters")
            if isinstance(raw_step.get("parameters"), dict) else {},
            "critical": bool(raw_step.get("critical", False)),
            "checkpoint": bool(raw_step.get("checkpoint", False)),
        })
        if steps[-1]["checkpoint"]:
            if pos + 1 < len(raw_steps):
                warnings.append(
                    "dropped steps planned past checkpoint step "
                    f"{steps[-1]['step']} — the rest is planned "
                    "from its result")
            break
    cap = _plan_max_steps()
    if len(steps) > cap:
        warnings.append(
            f"plan clamped from {len(steps)} to {cap} steps "
            f"(PLAN_MAX_STEPS, blueprint SS3.3)")
        steps = steps[:cap]
    if not steps:
        return None
    # A plan revision id, fresh per sanitized plan: the replan
    # path re-sanitizes, so each revision is distinguishable
    # even when its steps renumber from 1.
    return {"goal": plan.get("goal") or goal, "steps": steps,
            "task_type": _valid_task_type(plan.get("task_type")),
            "plan_rev": str(plan.get("plan_rev") or _new_plan_rev()),
            "warnings": warnings}


def create_plan(goal: str, context: str = "",
                llm: Optional[Callable] = None) -> dict:
    """Plan a delegated task. A planning failure (pool down, bad
    JSON, empty plan) yields the research-only fallback plan
    when the goal is explicitly research-shaped; otherwise it
    raises PlanningFailed — a private goal is never sent to a
    search backend as a stand-in plan (pass 36 #33)."""
    llm = llm or _default_llm
    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"
    try:
        raw = llm(user_input, _system_prompt())
        plan = _parse_plan(raw, goal)
        if plan is not None:
            plan = _sanitize(plan, goal)
        if plan is None:
            add_log("planner: model plan unusable, using fallback plan")
            return _fallback_plan(goal)
        for w in plan["warnings"]:
            add_log(f"planner: {w}")
        add_log(f"planner: plan ready — {len(plan['steps'])} step(s) "
                f"for '{goal[:50]}'")
        return plan
    except PlanningFailed:
        raise
    except Exception as e:
        add_log(f"planner: planning failed ({e}), using fallback plan")
        return _fallback_plan(goal)


_RESULT_PREVIEW = 300  # chars of each completed step's result shown to the replanner


def _next_display_number(completed_steps: list) -> int:
    """First display number for a follow-on plan revision: one
    past the highest number already used by a completed step, so
    numbers never collide across revisions."""
    used = [s.get("step") for s in completed_steps or []
            if isinstance(s, dict) and isinstance(s.get("step"), int)]
    return (max(used) + 1) if used else 1


def _completed_summary(completed_steps: list,
                       results: Optional[dict]) -> str:
    """The completed steps as the replanner sees them: number,
    tool, and a bounded preview of what each one returned (keyed
    by stable step id, display number as the legacy fallback)."""
    lines = []
    for s in completed_steps or []:
        out = None
        if results:
            out = results.get(s.get("id"))
            if out is None:
                out = results.get(str(s.get("step")))
        line = f"  - Step {s.get('step')} ({s.get('tool')}): DONE"
        if out is not None:
            preview = " ".join(str(out).split())[:_RESULT_PREVIEW]
            line += f" — result: {preview}"
        lines.append(line)
    return "\n".join(lines) if lines else "  (none)"


def _numbering_note(start: int) -> str:
    return (f"Number the new steps from {start}. A parameter may "
            "use {stepN} for any completed step listed above or any "
            "new step before it, and {prev} for the step just "
            "before.")


def replan(goal: str, completed_steps: list, failed_step: dict,
           error: str, llm: Optional[Callable] = None,
           results: Optional[dict] = None) -> dict:
    """Revised plan for the REMAINING work after a step failure.
    Same validation/clamps/fallback as create_plan: a failed
    replan raises PlanningFailed unless the goal is explicitly
    research-shaped (pass 36 #33).

    The new steps are numbered after the completed ones, so
    {stepN} and every display-number view stay unambiguous
    across revisions, and the prompt carries each completed
    step's result so the revision can build on it."""
    llm = llm or _default_llm
    start = _next_display_number(completed_steps)
    prompt = (
        f"Goal: {goal}\n\n"
        f"Already completed:\n"
        f"{_completed_summary(completed_steps, results)}\n\n"
        f"Failed step: [{failed_step.get('tool')}] "
        f"{failed_step.get('description')}\n"
        f"Error: {str(error)[:400]}\n\n"
        "Create a REVISED plan for the remaining work only. "
        "Do not repeat completed steps. "
        f"{_numbering_note(start)}")
    try:
        raw = llm(prompt, _system_prompt())
        plan = _parse_plan(raw, goal)
        if plan is not None:
            plan = _sanitize(plan, goal, start=start)
        if plan is None:
            add_log("planner: replan unusable, using fallback plan")
            return _renumber(_fallback_plan(goal), start)
        add_log(f"planner: revised plan — {len(plan['steps'])} step(s)")
        return plan
    except PlanningFailed:
        raise
    except Exception as e:
        add_log(f"planner: replan failed ({e}), using fallback plan")
        return _renumber(_fallback_plan(goal), start)


def _renumber(plan: dict, start: int) -> dict:
    for i, step in enumerate(plan.get("steps") or []):
        step["step"] = start + i
    return plan


def continue_plan(goal: str, completed_steps: list,
                  results: Optional[dict] = None,
                  llm: Optional[Callable] = None) -> Optional[dict]:
    """Plan the work that follows a checkpoint step, from what the
    completed steps actually returned. Returns a plan whose
    "steps" may be EMPTY — the model saying the goal is already
    met — or None when no usable continuation could be made (the
    caller then verifies the work done so far instead of
    guessing). Never sends anything to a search backend as a
    stand-in: a continuation has no fallback plan."""
    llm = llm or _default_llm
    start = _next_display_number(completed_steps)
    prompt = (
        f"Goal: {goal}\n\n"
        f"Completed so far:\n"
        f"{_completed_summary(completed_steps, results)}\n\n"
        "Using these results, plan the REMAINING work only. Do "
        "not repeat completed steps. If the goal is already "
        'accomplished, return "steps": []. '
        f"{_numbering_note(start)}")
    try:
        raw = llm(prompt, _system_prompt())
        plan = _parse_plan(raw, goal)
        if plan is None:
            add_log("planner: continuation unusable")
            return None
        if not plan.get("steps"):
            add_log("planner: continuation — goal already met")
            return {"goal": plan.get("goal") or goal, "steps": [],
                    "task_type": _valid_task_type(plan.get("task_type")),
                    "plan_rev": _new_plan_rev(), "warnings": []}
        plan = _sanitize(plan, goal, start=start)
        if plan is None:
            add_log("planner: continuation had no usable steps")
            return None
        for w in plan["warnings"]:
            add_log(f"planner: {w}")
        add_log(f"planner: continuation — {len(plan['steps'])} step(s)")
        return plan
    except Exception as e:
        add_log(f"planner: continuation failed ({e})")
        return None
