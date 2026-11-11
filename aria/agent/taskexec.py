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

HARDENING (Gap fill 3, 2026-10-27 — semantics adapted from
Mark-LVI core/delegation.py, CC BY-NC 4.0):
  * checkpoints — an on_checkpoint callback fires after planning
    and after every finished step with {plan, completed, results};
    the queue persists them (aria.agent.taskstore), so a task can
    resume instead of restarting;
  * resume — execute(resume=state) re-enters a checkpoint: the
    stored plan is reused and finished steps are skipped, their
    recorded results still feeding {prev}/{stepN} substitution;
  * blocked, not failed — a step result starting 'NEED_INPUT:'
    parks the task by raising TaskBlocked with the checkpoint
    attached; the queue marks it blocked and task_resume re-runs
    the current step later;
  * a skeptical verifier — with verify=True, one extra model turn
    judges the recorded step results against the goal before the
    summary is spoken; a FAIL parks the task the same way. An
    unparseable or erroring verdict never blocks (fail-open,
    logged) — a dead verifier makes a result unverified, not a
    task immortal.
  * an outcome grader (2026-11-08, setting
    outcomes_grader_enabled, default OFF) — when on, the
    one-line verifier is superseded by aria.agent.grader: a
    rubric grade over the evidence bundle plus read-only
    destination probes (journal receipts, file probes). A FAIL
    parks with the failed criteria and cited evidence; a PASS
    is decisive; cannot_grade falls back to the one-line
    verifier above, unchanged.
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
    from aria.tools.dispatch import (
        delegated_scope, execute_tool, reset_turn_state)
    # Each attempt gets a fresh dispatch turn context: the
    # dispatcher's per-turn duplicate suppression and repair budgets
    # are per-turn by design, and a delegated task's legitimate retry
    # of the same call must not be eaten by them. The delegated_scope
    # wrap is what lets host-lifecycle tools (restart_program,
    # run_shell_command) refuse to run for a background worker.
    reset_turn_state()
    with delegated_scope():
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
    from aria.outcome import normalize, FAILED
    return normalize(out).status == FAILED


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


def _resolved_artifact_path(tool: str, params: dict
                            ) -> Optional[str]:
    """The artifact a file-writing step actually wrote, resolved
    against the current workspace (pass 36 #25): write_file /
    code_helper write / dev_agent (with its default destination
    when output_path is absent). code_helper explain/fix read
    an input file and write nothing → no artifact. Returns
    None when there is no artifact or the path does not resolve
    under current policy. Never raises."""
    params = params or {}
    raw = None
    if tool == "write_file":
        raw = params.get("filename") or params.get("path")
    elif tool == "code_helper":
        if str(params.get("action") or "explain").strip().lower() \
                != "write":
            return None
        raw = params.get("output_path")
    elif tool == "dev_agent":
        raw = params.get("output_path")
        if not raw:
            try:
                from aria.tools.gaptools import (
                    dev_agent_default_output)
                raw = dev_agent_default_output(
                    params.get("language"))
            except Exception:
                raw = "dev_agent_output.py"
    else:
        return None
    if not raw:
        return None
    try:
        from aria import workspaces
        from aria.tools import builtins
        return workspaces.resolve_workspace_file(
            str(raw), builtins.WORKSPACE_DIR)
    except Exception:
        return None


class TaskBlocked(Exception):
    """The task parked instead of failing: it needs something
    (user input, or a verifier concern resolved) before it can
    continue. Carries the checkpoint state so the caller can
    persist it for a later resume."""

    def __init__(self, reason: str, state: Dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.state = state


from aria import runtime
from aria.outcome import normalize, ResultText, UNVERIFIED, STARTED, ASK


class AgentExecutor:
    """Runs one delegated goal end-to-end under the SS3.3 caps."""

    def __init__(self, *, llm: Optional[Callable] = None,
                 tool_runner: Optional[Callable] = None,
                 summarizer: Optional[Callable] = None,
                 sleep_fn: Optional[Callable] = None,
                 verifier: Optional[Callable] = None):
        self._llm = llm
        self._tool_runner = tool_runner or _default_tool_runner
        self._summarizer = summarizer or _default_summarizer
        self._sleep = sleep_fn or time.sleep
        self._verifier = verifier
        # Outcomes grader state (design 2026-11-08): the action
        # journal operation ids of the steps this execute() ran
        # (destination probes look receipts up by them) and the
        # last grade, for the summary note. Reset per execute().
        # _step_params (pass 36 #25) holds the SUBSTITUTED
        # parameters each step actually executed, as computed by
        # _journal_ids — probes are driven from these, never
        # from the raw template parameters.
        self._op_ids: Dict[int, str] = {}
        self._last_grade: Optional[dict] = None
        self._step_params: Dict[int, dict] = {}

    # -- skeptical verifier (Gap fill 3) --------------------------
    def _grader_chat(self):
        """The grader's chat callable for this executor: None
        lets the grader use its pool-backed default (which
        reports the grading model); an injected executor LLM
        grades through the same callable the work was planned
        with, labelled as such."""
        if self._llm is None:
            return None
        llm = self._llm

        def _chat(prompt, system, model=None):
            return llm(prompt, system), "injected-llm"
        return _chat

    def _journal_ids(self, completed: List[dict],
                     results: Dict[int, str],
                     resume: Optional[Dict[str, Any]] = None
                     ) -> Dict[int, str]:
        """Step number → action-journal operation id, for the
        grader's receipt probes. Ids recorded during this run
        win; steps a resume skipped come from the PERSISTED
        per-step evidence in the task journal when it exists
        (pass 36 #25 — the substituted params + op id + resolved
        artifact path recorded at execution time), and are
        otherwise recomputed from the stable execution scope
        exactly as the attempt loop built them (pass 26 B1#5).
        Substitution is computed against the results of the
        steps completed BEFORE each step, in execution order —
        never against the final result map, where {prev} would
        mean the last step's result instead of the previous
        one. Also records the substituted params per step in
        self._step_params for the probes."""
        ids = dict(self._op_ids)
        params_by_step: Dict[int, dict] = {}
        try:
            from aria.action_journal import signature
            scope = (resume or {}).get("execution_scope") \
                or runtime.current().id
            persisted: Dict[Any, dict] = {}
            try:
                from aria.agent import taskstore as _ts
                orig = ""
                if str(scope or "").startswith("task:"):
                    orig = str(scope)[len("task:"):]
                if orig:
                    rec = _ts.get(orig)
                    persisted = (rec or {}).get("step_evidence") \
                        or {}
            except Exception:
                persisted = {}
            seen: Dict[int, str] = {}
            for s in completed:
                n = s.get("step")
                saved = persisted.get(n)
                if saved is None:
                    saved = persisted.get(str(n))
                if isinstance(saved, dict) and saved.get("op_id"):
                    ids[n] = str(saved["op_id"])
                    if isinstance(saved.get("params"), dict):
                        params_by_step[n] = saved["params"]
                else:
                    params = _substitute(
                        s.get("parameters") or {}, seen)
                    params_by_step[n] = params
                    if n not in ids:
                        ids[n] = (f"{scope}:step:{n}:"
                                  f"{signature(s.get('tool', ''), params)}")
                if n in results:
                    seen[n] = results[n]
        except Exception as e:
            add_log(f"taskexec: journal id rebuild failed ({e}); "
                    f"receipt probes partial")
        self._step_params = params_by_step
        return ids

    def _persist_step_evidence(
            self, completed: List[dict],
            journal_ids: Optional[Dict[int, str]]) -> None:
        """Persist the per-step evidence for this run into the
        task journal (pass 36 #25): substituted params + op id
        + resolved artifact path per completed step, merged into
        the record's step_evidence without touching the
        checkpoint. Best-effort and never raising: without a
        task-scoped execution (a direct _verify call in tests,
        a non-queue context) there is no journal record to
        persist into, and grading proceeds without it."""
        if not journal_ids:
            return
        try:
            from aria.agent import taskstore as _ts
            scope = str(runtime.current().id or "")
            if not scope.startswith("task:"):
                return
            task_id = scope[len("task:"):]
            params = getattr(self, "_step_params", {}) or {}
            evidence: Dict[int, dict] = {}
            for s in completed:
                n = s.get("step")
                rec = {"op_id": journal_ids.get(n),
                       "params": params.get(n) or {}}
                artifact = _resolved_artifact_path(
                    s.get("tool") or "", rec["params"])
                if artifact:
                    rec["artifact_path"] = artifact
                evidence[n] = rec
            _ts.merge_step_evidence(task_id, evidence)
        except Exception as e:
            add_log(f"taskexec: step evidence persist failed ({e})")

    def _settle_delegates(
            self, completed: List[dict],
            results: Dict[int, str],
            journal_ids: Optional[Dict[int, str]]) -> None:
        """Pass 36 #28: before grading, write a settlement record
        on each delegate step's parent op id from the child's
        terminal task-journal status, so the grader sees the
        completed child instead of a STARTED receipt. Only
        terminal child states settle (completed / failed /
        cancelled); a still-running or blocked child leaves the
        receipt as submitted. Best-effort; never raises."""
        if not journal_ids:
            return
        try:
            from aria import action_journal
            from aria.agent import taskstore as _ts
            for s in completed:
                tool = s.get("tool") or ""
                if tool not in ("delegate_task", "task_resume"):
                    continue
                n = s.get("step")
                op_id = journal_ids.get(n)
                if op_id is None:
                    op_id = journal_ids.get(str(n))
                if not op_id:
                    continue
                m = re.search(r"task_id=([A-Za-z0-9_-]+)",
                              str(results.get(n, "")))
                if m is None:
                    continue
                child = _ts.get(m.group(1))
                status = (child or {}).get("status") or ""
                if status in ("completed", "failed", "cancelled"):
                    action_journal.settle(
                        op_id, m.group(1), status,
                        (child or {}).get("result") or
                        (child or {}).get("error") or "")
        except Exception as e:
            add_log(f"taskexec: delegate settle failed ({e})")

    def _persist_grade(self, grade_result: dict) -> None:
        """Persist the structured verdict on the task journal
        record (pass 36 #35): rubric id + version, per-criterion
        verdicts, evidence refs — never raw excerpts. Best
        effort; a direct _verify call outside the queue has no
        record to persist into."""
        try:
            from aria.agent import taskstore as _ts
            scope = str(runtime.current().id or "")
            if not scope.startswith("task:"):
                return
            _ts.set_grade(scope[len("task:"):], grade_result)
        except Exception as e:
            add_log(f"taskexec: grade persist failed ({e})")

    def _verify(self, goal: str, completed: List[dict],
                results: Dict[int, str], *,
                plan: Optional[Dict[str, Any]] = None,
                journal_ids: Optional[Dict[int, str]] = None,
                deadline: Optional[float] = None
                ) -> Optional[str]:
        """Return a rejection reason, or None when the work
        passes (or cannot be judged — fail-open). The verifier
        sees ONLY the goal and the recorded step results: it
        judges evidence, not intentions. deadline is the
        monotonic-seconds wall-clock bound for the grader,
        derived from the task's remaining budget by execute()
        (pass 36 #31); the grader's cancel propagates (pass 36
        #30 owns the cancel path — this method does not swallow
        runtime.Cancelled)."""
        if self._verifier is not None:
            try:
                return self._verifier(goal, completed, results)
            except Exception as e:
                add_log(f"taskexec: verifier errored ({e}); outcome unverified")
                return "Verification unavailable; recorded work needs review"
        # Outcomes grader (design 2026-11-08): with the setting
        # on, a rubric grade over the evidence bundle plus
        # destination probes replaces the one-line verdict — a
        # FAIL parks with the failed criteria and the grader's
        # cited evidence; a PASS is decisive; cannot_grade falls
        # through to the legacy path below, unchanged. With the
        # setting off this block never runs.
        try:
            from aria.agent import grader as _grader
            if _grader.outcomes_enabled():
                steps = [dict(s) for s in completed]
                # Pass 36 #25: the per-step evidence (substituted
                # params + op id + resolved artifact path) is
                # persisted before grading, so a resume probes
                # from what actually ran.
                self._persist_step_evidence(completed, journal_ids)
                # Pass 36 #28: settle delegate receipts from
                # their children's terminal outcomes first, so
                # the probes below carry the settlement.
                self._settle_delegates(completed, results,
                                       journal_ids)
                probes = _grader.build_probes(
                    _grader.normalize_steps(steps, results),
                    journal_ids=journal_ids,
                    step_params=getattr(self, "_step_params",
                                        None))
                # Pass 36 #29: the worker model is the model
                # that planned this task (planner records it via
                # chat_traced); an injected executor LLM grades
                # through the same callable the work was planned
                # with, so its worker identity is "injected-llm"
                # and a self-grade is labelled, not hidden. Pass
                # 36 #31: the grade runs inside the deadline
                # execute() derived from the remaining budget.
                worker_model = (
                    "injected-llm" if self._llm is not None
                    else planner.last_worker_model())
                g = _grader.grade(
                    goal, steps, results,
                    task_type=(plan or {}).get("task_type"),
                    worker_model=worker_model,
                    deadline=deadline,
                    chat=self._grader_chat(), probes=probes,
                    # Pass 36 #32: the plan's check-step list
                    # rides into the bundle; without a plan the
                    # grader derives it from the executed steps.
                    check_steps=_grader.identify_check_steps(
                        (plan or {}).get("steps") or [])
                    if (plan or {}).get("steps") else None)
                self._last_grade = g
                # Pass 36 #35: the structured verdict is
                # persisted on the task journal record.
                self._persist_grade(g)
                if g["verdict"] == "fail":
                    return _grader.format_failure_reason(g)
                if g["verdict"] == "pass":
                    return None
                add_log("taskexec: outcome grader could not "
                        "grade; legacy verifier path")
        except runtime.Cancelled:
            # Pass 36 #31 (with #30): a cancelled grade
            # propagates — it must not fall through to the legacy
            # verifier and park-or-pass a task whose run was
            # cancelled.
            raise
        except Exception as e:
            add_log(f"taskexec: outcome grader errored ({e}); "
                    f"legacy verifier path")
        llm = self._llm or planner._default_llm
        evidence = "\n".join(
            f"- step {s.get('step')} ({s.get('tool', '?')}): "
            f"{str(results.get(s.get('step'), ''))[:400]}"
            for s in completed) or "(no completed steps)"
        try:
            # Pass 28 #11: the LLM contract is llm(prompt, system)
            # (planner.py:24) — _default_llm requires `system`
            # positionally. The old one-argument call raised
            # TypeError on every verification, so the except path
            # parked every verified task as blocked and the
            # default verifier could never pass anything.
            out = llm(
                f'Goal: "{goal}"\n\nRecorded step results:\n{evidence}',
                "You are a skeptical reviewer. Did the recorded "
                "results actually accomplish the goal? Answer with "
                "exactly one line: 'VERDICT: PASS' or "
                "'VERDICT: FAIL — <one short reason>'. Judge only "
                "the evidence above; missing evidence is a FAIL.")
        except Exception as e:
            add_log(f"taskexec: verifier unavailable ({e}); outcome unverified")
            return "Verification unavailable; recorded work needs review"
        text = (out or "").strip()
        if text.startswith("VERDICT: PASS"):
            return None
        if text.startswith("VERDICT: FAIL"):
            return text.split("—", 1)[-1].strip() or "verifier rejected the result"
        add_log("taskexec: verifier verdict unparseable; outcome unverified")
        return "Verification returned no usable verdict"

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
                cancel_flag=None, *,
                on_checkpoint: Optional[Callable] = None,
                resume: Optional[Dict[str, Any]] = None,
                verify: bool = False) -> str:
        budget_s = _cap("DELEGATED_TASK_BUDGET_S", 600)
        max_attempts = _cap("STEP_MAX_ATTEMPTS", 3)
        max_replans = _cap("MAX_REPLAN_ATTEMPTS", 2)
        t0 = time.time()

        def _cancelled() -> bool:
            return runtime.current().cancel.is_set() or bool(cancel_flag and cancel_flag.is_set())

        def _over_budget() -> bool:
            return (time.time() - t0) > budget_s

        def _finish_outcome(text: str, status="failed") -> str:
            if speak:
                try:
                    speak(text)
                except Exception:
                    pass
            return ResultText(text, status)

        def _state() -> Dict[str, Any]:
            return {"goal": goal, "plan": plan,
                    "completed": list(completed),
                    "results": dict(results),
                    # Pass 28 #20: a step that spawned a delegated
                    # child parks with the child's id in the
                    # checkpoint, so a resume settles THAT child
                    # instead of submitting another one.
                    "pending_child": pending_child}

        def _checkpoint() -> None:
            if on_checkpoint is not None:
                try:
                    on_checkpoint(_state())
                except Exception as e:
                    add_log(f"taskexec: checkpoint callback failed ({e})")

        add_log(f"taskexec: starting delegated task '{goal[:60]}'")
        self._op_ids = {}
        self._last_grade = None
        # Pass 28 #20: a pending child survives in the checkpoint
        # state across parks; a fresh run has none.
        pending_child = (resume or {}).get("pending_child")
        if resume and resume.get("plan"):
            # Resume from a persisted checkpoint: reuse the plan,
            # keep the finished steps and their results. skip_steps
            # is the resume-only skip set: after a REPLAN the new
            # plan's steps are fresh work even if their numbers
            # collide with recorded results (results still feed
            # {prev}/{stepN} substitution, they just stop skipping).
            plan = resume["plan"]
            completed = list(resume.get("completed") or [])
            results = {int(k): v for k, v in
                       (resume.get("results") or {}).items()}
            skip_steps = set(results)
            add_log(f"taskexec: resuming with {len(completed)} "
                    "step(s) already complete")
        else:
            try:
                plan = planner.create_plan(goal, llm=self._llm)
            except planner.PlanningFailed as e:
                # Pass 36 #33: no usable plan and no
                # research-shaped fallback — abort with the
                # reason instead of searching the raw goal.
                add_log(f"taskexec: planning failed — {e.reason}")
                return _finish_outcome(
                    "I couldn't make a plan for that task: "
                    f"{e.reason}.")
            completed = []
            results = {}
            skip_steps = set()
        replans = 0
        _checkpoint()

        while True:
            steps = plan.get("steps") or []
            if not steps:
                return _finish_outcome(
                    "I couldn't create a valid plan for that task.")
            failed_step: Optional[dict] = None
            failed_error = ""

            for step in steps:
                if step.get("step") in skip_steps:
                    continue  # resume: this step already ran
                if pending_child and pending_child.get("step") == \
                        step.get("step"):
                    # Pass 28 #20: this step already spawned a
                    # child and parked waiting for it — settle the
                    # child from the durable journal instead of
                    # submitting another one. The journal is the
                    # source of truth (same project: delegate_task
                    # submits under the parent's project).
                    from aria.agent import taskstore as _ts
                    _child = _ts.get(
                        str(pending_child.get("task_id") or ""))
                    _cstatus = (_child or {}).get("status")
                    if _cstatus == "completed":
                        results[step.get("step")] = \
                            (_child or {}).get("result") or ""
                        completed.append(step)
                        pending_child = None
                        _checkpoint()
                        continue
                    if _cstatus in ("failed", "cancelled"):
                        failed_step = step
                        failed_error = (
                            (_child or {}).get("error")
                            or (_child or {}).get("result")
                            or f"delegated child "
                               f"{pending_child.get('task_id')} "
                               f"{_cstatus}")
                        pending_child = None
                        break
                    if _cstatus == "blocked":
                        raise TaskBlocked(
                            f"delegated child "
                            f"{pending_child.get('task_id')} is blocked: "
                            f"{(_child or {}).get('blocked_reason') or 'needs input'}",
                            _state())
                    if _cstatus in ("pending", "running", "interrupted"):
                        raise TaskBlocked(
                            f"waiting for delegated child "
                            f"{pending_child.get('task_id')} "
                            f"(step {step.get('step')})",
                            _state())
                    # No journal record (or an unknown status): the
                    # child cannot be settled — fail the step so the
                    # replan path retries the delegation instead of
                    # spawning a second child or certifying nothing.
                    failed_step = step
                    failed_error = (
                        f"delegated child "
                        f"{pending_child.get('task_id')} has no journal "
                        f"record")
                    pending_child = None
                    break
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
                        from aria.action_journal import signature
                        # The receipt id is built from the STABLE
                        # execution scope (pass 26 B1#5), never the
                        # volatile budget scope: a resume from
                        # ordinary conversation carries a different
                        # budget scope, and the same step under a
                        # different id would re-execute an effect
                        # that already landed instead of hitting
                        # the journal's cached/UNVERIFIED path.
                        # First run: the current execution id IS
                        # the task's scope (task:{id}); a resume
                        # carries the original scope in resume[].
                        _scope = (resume or {}).get("execution_scope") \
                            or runtime.current().id
                        op = (f"{_scope}:step:{step.get('step')}:"
                              f"{signature(tool, params)}")
                        self._op_ids[step.get("step")] = op
                        with runtime.operation(op):
                            out = self._tool_runner(tool, params)
                    except runtime.Cancelled:
                        raise
                    except Exception as e:
                        out = f"[Tool Error: {type(e).__name__}: {e}]"
                    if tool in ("delegate_task", "task_resume") and \
                            normalize(out).status == STARTED:
                        # Pass 28 #20: a child-spawning step that
                        # started a child parks WITH the child's
                        # handle in the checkpoint — the generic
                        # STARTED branch below would park with the
                        # step uncheckpointed, and the resume would
                        # submit another child instead of settling
                        # this one.
                        _m = re.search(r"task_id=([A-Za-z0-9_-]+)",
                                       str(out))
                        _p = re.search(r"project=([A-Za-z0-9_-]+)",
                                       str(out))
                        if _m is not None:
                            pending_child = {
                                "step": step.get("step"),
                                "task_id": _m.group(1),
                                "project": (_p.group(1) if _p
                                            else None)}
                            raise TaskBlocked(
                                f"waiting for delegated child "
                                f"{_m.group(1)} "
                                f"(step {step.get('step')})",
                                _state())
                    if normalize(out).status in (UNVERIFIED, STARTED, ASK) and not str(out).startswith('[AWAITING_APPROVAL'):
                        raise TaskBlocked(str(out), _state())
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
                        if isinstance(out, str) and \
                                out.startswith("NEED_INPUT:"):
                            # The step is asking the user something
                            # only the user can answer: park the
                            # task with its checkpoint (Gap fill 3).
                            reason = out[len("NEED_INPUT:"):].strip() \
                                or "a step needs user input"
                            add_log(f"taskexec: task blocked — {reason[:80]}")
                            raise TaskBlocked(reason, _state())
                        results[step.get("step")] = out
                        completed.append(step)
                        step_done = True
                        _checkpoint()
                        break
                    from aria.tool_policy import retry_safe
                    if _is_failure(out) and not retry_safe(tool):
                        raise TaskBlocked('An action with effects failed; check its receipt before another attempt. ' + str(out), _state())
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
                        _checkpoint()
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
                if verify:
                    # Pass 36 #31: the grade gets what is left
                    # of the task budget (capped at 300 s, so one
                    # slow grade can never hold the worker past a
                    # useful window), as a monotonic deadline.
                    _remaining = budget_s - (time.time() - t0)
                    _grade_deadline = time.monotonic() + max(
                        0.0, min(_remaining, 300.0))
                    rejection = self._verify(
                        goal, completed, results, plan=plan,
                        journal_ids=self._journal_ids(
                            completed, results, resume),
                        deadline=_grade_deadline)
                    if rejection:
                        add_log("taskexec: verifier parked the task — "
                                f"{rejection[:80]}")
                        raise TaskBlocked(
                            f"verifier: {rejection}", _state())
                summary = self._summarizer(goal, completed)
                if self._last_grade and \
                        self._last_grade.get("verdict") == "pass":
                    from aria.agent import grader as _grader
                    summary = (summary + " (" + _grader
                               .format_grade_note(self._last_grade)
                               + ")")
                add_log(f"taskexec: task complete — {summary[:80]}")
                return _finish_outcome(summary, status="done")

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
            try:
                plan = planner.replan(goal, completed, failed_step,
                                      failed_error, llm=self._llm)
            except planner.PlanningFailed as e:
                # Pass 36 #33: the replan produced nothing usable
                # and the goal is not research-shaped — abort
                # with the reason instead of searching the goal.
                add_log(f"taskexec: replan failed — {e.reason}")
                return _finish_outcome(
                    "I couldn't find another approach for that task: "
                    f"{e.reason}.")
            skip_steps = set()  # replanned steps are fresh work
            _checkpoint()


def run_delegated(goal: str, speak: Optional[Callable] = None,
                  cancel_flag=None, **executor_kwargs) -> str:
    """Headless entry for a delegated task (blueprint SS3.3): runs the
    executor synchronously and returns the outcome text. The queue
    (aria.agent.taskqueue) is the normal caller; brain.run_headless
    serves callers that need the full conversation loop instead."""
    return AgentExecutor(**executor_kwargs).execute(
        goal, speak=speak, cancel_flag=cancel_flag)
