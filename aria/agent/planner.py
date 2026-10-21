"""
ARIA delegated-task planner (Merger Phase 3 — blueprint SS2.1, SS3.3).

Pattern reimplemented from Mark-XXXIX-OR's agent/planner.py
(FatihMakes, CC BY-NC 4.0) in ARIA's idiom: a plan is JSON —
{"goal", "steps": [{"step", "tool", "description", "parameters",
"critical"}]} — produced in one call, validated, clamped to
PLAN_MAX_STEPS, with a deterministic single-step fallback when
planning fails. Differences from his planner:

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
        "without it.\n\n"
        "AVAILABLE TOOLS:\n"
        f"{_tool_catalogue()}\n\n"
        "OUTPUT — return ONLY valid JSON, no markdown, no explanation:\n"
        "{\n"
        '  "goal": "...",\n'
        '  "steps": [\n'
        '    {"step": 1, "tool": "tool_name", "description": "what this step does",\n'
        '     "parameters": {}, "critical": true}\n'
        "  ]\n"
        "}")


def _default_llm(prompt: str, system: str) -> str:
    from aria.agent.orpool import get_pool
    return get_pool().chat(prompt, system=system, max_tokens=2048,
                           temperature=0.2)


def _fallback_plan(goal: str) -> dict:
    """His deterministic fallback: one critical web_search step."""
    return {
        "goal": goal,
        "steps": [{
            "step": 1,
            "tool": "web_search",
            "description": f"Search for: {goal}",
            "parameters": {"query": goal},
            "critical": True,
        }],
        "warnings": ["planner fell back to a single search step"],
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


def _sanitize(plan: dict, goal: str) -> Optional[dict]:
    """Validate + clamp a parsed plan. Returns None when nothing
    usable remains (caller falls back)."""
    known = known_tools()
    warnings: List[str] = list(plan.get("warnings") or [])
    steps: List[dict] = []
    for raw_step in plan.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        tool = str(raw_step.get("tool") or "").strip()
        if known and tool not in known:
            warnings.append(f"dropped step with unknown tool '{tool}'")
            continue
        steps.append({
            "step": len(steps) + 1,
            "tool": tool,
            "description": str(raw_step.get("description") or ""),
            "parameters": raw_step.get("parameters")
            if isinstance(raw_step.get("parameters"), dict) else {},
            "critical": bool(raw_step.get("critical", False)),
        })
    cap = _plan_max_steps()
    if len(steps) > cap:
        warnings.append(
            f"plan clamped from {len(steps)} to {cap} steps "
            f"(PLAN_MAX_STEPS, blueprint SS3.3)")
        steps = steps[:cap]
    if not steps:
        return None
    return {"goal": plan.get("goal") or goal, "steps": steps,
            "warnings": warnings}


def create_plan(goal: str, context: str = "",
                llm: Optional[Callable] = None) -> dict:
    """Plan a delegated task. Never raises: any failure (pool down,
    bad JSON, empty plan) yields the deterministic fallback plan."""
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
    except Exception as e:
        add_log(f"planner: planning failed ({e}), using fallback plan")
        return _fallback_plan(goal)


def replan(goal: str, completed_steps: list, failed_step: dict,
           error: str, llm: Optional[Callable] = None) -> dict:
    """Revised plan for the REMAINING work after a step failure.
    Same validation/clamps/fallback as create_plan."""
    llm = llm or _default_llm
    completed_summary = "\n".join(
        f"  - Step {s.get('step')} ({s.get('tool')}): DONE"
        for s in completed_steps)
    prompt = (
        f"Goal: {goal}\n\n"
        f"Already completed:\n{completed_summary if completed_summary else '  (none)'}\n\n"
        f"Failed step: [{failed_step.get('tool')}] "
        f"{failed_step.get('description')}\n"
        f"Error: {str(error)[:400]}\n\n"
        "Create a REVISED plan for the remaining work only. "
        "Do not repeat completed steps.")
    try:
        raw = llm(prompt, _system_prompt())
        plan = _parse_plan(raw, goal)
        if plan is not None:
            plan = _sanitize(plan, goal)
        if plan is None:
            add_log("planner: replan unusable, using fallback plan")
            return _fallback_plan(goal)
        add_log(f"planner: revised plan — {len(plan['steps'])} step(s)")
        return plan
    except Exception as e:
        add_log(f"planner: replan failed ({e}), using fallback plan")
        return _fallback_plan(goal)
