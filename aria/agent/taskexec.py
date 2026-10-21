"""
ARIA delegated-task executor (Merger Phase 3 — blueprint SS2.1, SS3.3).

Pattern reimplemented from Mark-XXXIX-OR's agent/executor.py
(FatihMakes, CC BY-NC 4.0): plan -> per-step attempts -> classify
the failure -> retry / skip / replan / abort -> summarize. Rebuilt
in ARIA's idiom on her real dispatch (aria.tools.dispatch.
execute_tool), her failure surfacing (Phase 1), her planner
(aria.agent.planner), and her background pool (Phase 2, summaries).

DELIBERATELY NOT PORTED — banned in this codebase (blueprint SS2.1):
his executor falls back to _run_generated_code (an LLM writes
Python, executed via subprocess) for unknown tools, and his
error_handler.generate_fix manufactures LLM-written replacement
steps. No such path exists here: an unknown tool is a HARD ABORT,
a failed step is retried / skipped / replanned / aborted by
classification, and nothing an LLM writes is ever executed as code.
His _call_tool also imports a nonexistent actions/cmd_control;
ARIA steps call only tools in her real registry, through her real
dispatcher — including her approval gating: a step that returns
[AWAITING_APPROVAL ...] aborts the task for the user's decision
rather than being treated as success or retried blind.

Caps are the contract (blueprint SS3.3; constants in aria.config,
read dynamically): steps <= PLAN_MAX_STEPS (planner-enforced),
attempts per step <= STEP_MAX_ATTEMPTS, replans <=
MAX_REPLAN_ATTEMPTS, whole-task wall-clock budget
DELEGATED_TASK_BUDGET_S.

Delegation is EXPLICIT only: this executor runs when the user (or
an existing approved routine) asked, via the delegate_task tool.
Nothing self-delegates. Voice loudness (blueprint SS3.2): only task
OUTCOMES are spoken (completion summary, abort, budget stop);
mid-task step failures go to the failure record, never the speaker.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from aria import config as _config
from aria.config import add_log
from aria.agent import failures, planner

STEP_RETRY_DELAY_S = 1.0
_SUBST_CAP = 1500  # chars of a prior result substituted into a parameter


def _cap(name: str, default: int) -> int:
    return int(getattr(_config, name, default))


def _default_tool_runner(name: str, args: dict) -> str:
    from aria.tools.dispatch import execute_tool, reset_turn_state
    # Each attempt gets a fresh dispatch turn context: the
    # dispatcher's per-turn duplicate suppression and repair budgets
    # are per-turn by design, and a delegated task's legitimate retry
    # of the same call must not be eaten by them.
    reset_turn_state()
    return execute_tool(name, args or {})[0]


def _default_summarizer(goal: str, completed: List[dict]) -> str:
    fallback = (f"Done — completed {len(completed)} step(s) for: "
                f"{goal[:60]}.")
    try:
        from aria.agent.orpool import get_pool
        steps_str = "\n".join(
            f"- {s.get('description', '')}" for s in completed)
        summary = get_pool().chat(
            f'User goal: "{goal}"\nCompleted steps:\n{steps_str}\n\n'
            "Write a single natural sentence summarizing what was "
            "accomplished. Be direct and positive.",
            system="You summarize completed delegated tasks in one sentence.",
            max_tokens=120, temperature=0.3)
        return summary.strip() or fallback
    except Exception:
        return fallback


def _is_failure(out: Any) -> bool:
    """ARIA's tool-result failure convention (same markers dispatch
    itself watches): bracketed error strings and raw tracebacks.
    Approval-gate and safe-mode results are failures too — they are
    never a step's success (the executor special-cases them to an
    abort before classification; see execute())."""
    if not isinstance(out, str):
        return False
    return (out.startswith("[Tool Error")
            or out.startswith("[Argument error")
            or out.startswith("Unknown tool:")
            or out.startswith("[AWAITING_APPROVAL")
            or out.startswith("[Safe mode:")
            or "Traceback (most recent call last)" in out)


def _substitute(value: Any, results: Dict[int, str]) -> Any:
    """Deterministic {prev} / {stepN} substitution in step
    parameters (string values only). NOT code evaluation — plain
    text replacement of recorded step results."""
    if isinstance(value, str):
        def _repl(m):
            token = m.group(1)
            if token == "prev":
                if not results:
                    return ""
                return str(results[max(results)])[:_SUBST_CAP]
            try:
                return str(results.get(int(token[4:]), ""))[:_SUBST_CAP]
            except Exception:
                return ""
        return re.sub(r"\{(prev|step\d+)\}", _repl, value)
    if isinstance(value, dict):
        return {k: _substitute(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, results) for v in value]
    return value


class AgentExecutor:
    """Runs one delegated goal end-to-end under the SS3.3 caps."""

    def __init__(self, *, llm: Optional[Callable] = None,
                 tool_runner: Optional[Callable] = None,
                 summarizer: Optional[Callable] = None,
                 sleep_fn: Optional[Callable] = None):
        self._llm = llm
        self._tool_runner = tool_runner or _default_tool_runner
        self._summarizer = summarizer or _default_summarizer
        self._sleep = sleep_fn or time.sleep

    # -- failure classification via Phase 1 -----------------------------
    def _classify_and_surface(self, tool: str, out: str, *,
                              critical: bool, attempt: int) -> Dict[str, Any]:
        source = f"taskexec.step:{tool}"
        rec = failures.surface_failure(
            source, out, critical=critical, attempts=attempt,
            max_attempts=_cap("STEP_MAX_ATTEMPTS", 3))
        if rec is None:
            # Surfacing is disabled (rollback flag) — the executor
            # still needs the decision; classify purely.
            rec = failures.classify_failure(
                source, out, critical=critical, attempts=attempt,
                max_attempts=_cap("STEP_MAX_ATTEMPTS", 3))
        return rec

    def execute(self, goal: str, speak: Optional[Callable] = None,
                cancel_flag=None) -> str:
        budget_s = _cap("DELEGATED_TASK_BUDGET_S", 600)
        max_attempts = _cap("STEP_MAX_ATTEMPTS", 3)
        max_replans = _cap("MAX_REPLAN_ATTEMPTS", 2)
        t0 = time.time()

        def _cancelled() -> bool:
            return bool(cancel_flag and cancel_flag.is_set())

        def _over_budget() -> bool:
            return (time.time() - t0) > budget_s

        def _finish_outcome(text: str) -> str:
            if speak:
                try:
                    speak(text)
                except Exception:
                    pass
            return text

        add_log(f"taskexec: starting delegated task '{goal[:60]}'")
        plan = planner.create_plan(goal, llm=self._llm)
        completed: List[dict] = []
        results: Dict[int, str] = {}
        replans = 0

        while True:
            steps = plan.get("steps") or []
            if not steps:
                return _finish_outcome(
                    "I couldn't create a valid plan for that task.")
            failed_step: Optional[dict] = None
            failed_error = ""

            for step in steps:
                if _cancelled():
                    add_log("taskexec: task cancelled")
                    return "Task cancelled."
                if _over_budget():
                    rec = failures.surface_failure(
                        "taskexec", f"task exceeded its {budget_s}s budget",
                        critical=True, decision=failures.ABORT,
                        reason=f"The task ran past its {budget_s}-second budget.",
                        user_message="That task ran too long, so I stopped it.")
                    msg = (rec or {}).get("user_message") or \
                        "That task ran too long, so I stopped it."
                    return _finish_outcome(msg)

                tool = step.get("tool", "")
                if tool not in planner.known_tools():
                    # HARD ABORT (blueprint SS2.1). His executor fell
                    # through to generated code here; that path is
                    # banned. There is no fallback execution.
                    rec = failures.surface_failure(
                        f"taskexec.step:{tool}",
                        f"Unknown tool in plan: {tool}",
                        critical=True, unknown_tool=True,
                        decision=failures.ABORT,
                        reason=f"Plan names tool '{tool}', which does not exist.",
                        user_message="That plan named a tool I don't have, so I stopped.")
                    msg = (rec or {}).get("user_message") or \
                        "That plan named a tool I don't have, so I stopped."
                    return _finish_outcome(msg)

                params = _substitute(step.get("parameters") or {}, results)
                attempt = 0
                step_done = False
                while attempt < max_attempts:
                    if _cancelled():
                        add_log("taskexec: task cancelled")
                        return "Task cancelled."
                    attempt += 1
                    try:
                        out = self._tool_runner(tool, params)
                    except Exception as e:
                        out = f"[Tool Error: {type(e).__name__}: {e}]"
                    # Approval gate / safe mode FIRST: those strings
                    # are never a success — they need the user, not
                    # a retry — abort the task deterministically.
                    if isinstance(out, str) and (
                            out.startswith("[AWAITING_APPROVAL")
                            or out.startswith("[Safe mode:")):
                        rec = failures.surface_failure(
                            f"taskexec.step:{tool}", out, critical=True,
                            decision=failures.ABORT,
                            reason="A step is gated on user approval or safe mode.",
                            user_message="A step needs your approval, so I stopped the task.")
                        msg = (rec or {}).get("user_message") or \
                            "A step needs your approval, so I stopped the task."
                        return _finish_outcome(msg)
                    if not _is_failure(out):
                        results[step.get("step")] = out
                        completed.append(step)
                        step_done = True
                        break
                    rec = self._classify_and_surface(
                        tool, out, critical=bool(step.get("critical")),
                        attempt=attempt)
                    decision = rec.get("decision", failures.REPLAN)
                    if decision == failures.RETRY and attempt < max_attempts:
                        self._sleep(STEP_RETRY_DELAY_S)
                        continue
                    if decision == failures.SKIP:
                        results[step.get("step")] = "[skipped]"
                        completed.append(step)
                        step_done = True
                        break
                    if decision == failures.ABORT:
                        return _finish_outcome(
                            rec.get("user_message")
                            or "That task failed, so I stopped it.")
                    # REPLAN (or RETRY with the budget out): hand the
                    # failure to the replan loop.
                    failed_step = step
                    failed_error = out
                    break
                if not step_done and failed_step is None:
                    # Attempts exhausted while the classifier kept
                    # saying retry — that IS a replan trigger.
                    failed_step = step
                    failed_error = "Max attempts exceeded"
                if failed_step is not None:
                    break

            if failed_step is None:
                summary = self._summarizer(goal, completed)
                add_log(f"taskexec: task complete — {summary[:80]}")
                return _finish_outcome(summary)

            if replans >= max_replans:
                rec = failures.surface_failure(
                    "taskexec", failed_error, critical=True,
                    decision=failures.ABORT,
                    reason=f"Task failed after {replans} replan attempts.",
                    user_message="That task failed despite new approaches, so I stopped.")
                msg = (rec or {}).get("user_message") or \
                    "That task failed despite new approaches, so I stopped."
                return _finish_outcome(msg)
            replans += 1
            add_log(f"taskexec: replanning ({replans}/{max_replans}) "
                    f"after step failure")
            plan = planner.replan(goal, completed, failed_step,
                                  failed_error, llm=self._llm)


def run_delegated(goal: str, speak: Optional[Callable] = None,
                  cancel_flag=None, **executor_kwargs) -> str:
    """Headless entry for a delegated task (blueprint SS3.3): runs the
    executor synchronously and returns the outcome text. The queue
    (aria.agent.taskqueue) is the normal caller; brain.run_headless
    serves callers that need the full conversation loop instead."""
    return AgentExecutor(**executor_kwargs).execute(
        goal, speak=speak, cancel_flag=cancel_flag)
