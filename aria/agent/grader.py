"""
ARIA outcome grader (Outcomes/Grader upgrade, design
2026-11-08 — aria-audit/outcomes-grader-design-2026-11-08.md).

One grader both verify paths consume (aria.missions and
aria.agent.taskexec). It judges a finished run against a RUBRIC
(aria.agent.rubrics): named must/advisory criteria plus named
failure cases, over an EVIDENCE BUNDLE the engine assembles —
never the worker:

  * the goal and the plan steps with their recorded outputs
    (claims — they are the executing tools' own words);
  * DESTINATION PROBES read back from the world at grade time
    (evidence — action-journal receipt state for effect steps,
    stat + content sample for file-writing steps).

No chat history, no worker reasoning, no self-assessment enters
the bundle; the builder reads only known step keys, so nothing
else a step dict carries can leak in.

The verdict contract is strict JSON, and the overall verdict is
COMPUTED here, never trusted from the model: a failed `must`
criterion or a present failure case fails the grade; a `must`
criterion the grader could not judge makes the whole grade
cannot_grade. Any error — pool down, unparseable output, bad
shape — is cannot_grade, which each engine maps onto its
existing fallback. A dead grader never certifies and never
makes a task immortal.

Model policy (setting grader_model_policy, default "different"):
the grader leg should not be the leg that did the work — a
grader sharing the worker's model shares its blind spots. The
GradeResult records the model that actually graded (the pool
reports it via chat_traced); when it equals the worker's model
the result carries self_graded=True so a self-check is never
dressed as an independent grade. When the caller cannot say
which model did the work (worker_model None) the result
records worker_model None and the note says "worker unknown":
roster[0] is never presumed to be the worker, and the pool
walk enforces the exclusion itself (pass 36 #29). A grade
also carries a wall-clock deadline from the run's remaining
budget (pass 36 #31); expiry is cannot_grade, and a
cancelled grade propagates instead of being swallowed.

Master switch: setting outcomes_grader_enabled (default False).
With it off, neither engine calls this module and both behave
exactly as before.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from aria import config as _config
from aria import runtime
from aria.agent import rubrics as _rubrics
from aria.config import add_log

OUTPUT_CAP = 1500          # chars of one step's recorded output
_EVIDENCE_CAP = 240        # chars of cited evidence per criterion
_SAMPLE_CAP = 300          # chars of file content sample per probe
_BUNDLE_CAP = 64000        # chars of the whole evidence bundle sent
                           # to the grader (pass 36 #31: total
                           # evidence budget, not just per-step caps)
_POLICIES = ("different", "cheap", "same")

_FENCE_RE = re.compile(r"```(?:json)?")

_SYSTEM = (
    "You are an independent grader for ARIA, a personal AI "
    "assistant. You did NOT do this work and you have no stake "
    "in it passing. Judge ONLY the evidence bundle below.\n\n"
    "Recorded step outputs are CLAIMS — the executing tools' own "
    "words about what happened. Destination probes are EVIDENCE "
    "— state read back from the world at grade time. When a "
    "claim and a probe disagree, the probe wins.\n\n"
    "Check every rubric criterion and every named failure case "
    "against the bundle. For each criterion answer pass, fail, "
    "or na (genuinely does not apply). Missing evidence for a "
    "criterion is a fail, not an na. Cite the specific step "
    "output or probe each verdict rests on, briefly.\n\n"
    "Return ONLY valid JSON, no markdown, no explanation:\n"
    '{"criteria": [{"id": "<criterion id>", "verdict": '
    '"pass|fail|na", "evidence": "<short citation>"}], '
    '"failure_cases": [{"id": "<case id>", "present": '
    'true|false, "evidence": "<short citation>"}]}')


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def outcomes_enabled() -> bool:
    try:
        return bool(_config.get_setting(
            "outcomes_grader_enabled", False))
    except Exception:
        return False


def grader_policy() -> str:
    try:
        policy = str(_config.get_setting(
            "grader_model_policy", "different") or "different")
    except Exception:
        policy = "different"
    return policy if policy in _POLICIES else "different"


# ------------------------------------------------------------------
# Step normalization + evidence bundle
# ------------------------------------------------------------------

def _step_output(step: dict, results: Optional[dict]) -> str:
    if results is not None:
        for key in (step.get("step"), step.get("idx")):
            if key is not None and key in results:
                return str(results[key] or "")
            if key is not None and str(key) in results:
                return str(results[str(key)] or "")
    return str(step.get("output") or step.get("result") or "")


def normalize_steps(steps: List[dict],
                    results: Optional[dict] = None) -> List[dict]:
    """Both engines' step shapes → the grader's one shape. Only
    known keys are read — anything else a step dict carries
    (chat context, reasoning, journal internals) stays out of
    the bundle by construction."""
    out = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        number = s.get("step")
        if number is None:
            idx = s.get("idx")
            number = (idx + 1) if isinstance(idx, int) else i + 1
        out.append({
            "step": number,
            "tool": str(s.get("tool") or "?"),
            "description": str(s.get("description") or ""),
            "parameters": s.get("parameters")
            if isinstance(s.get("parameters"), dict)
            else (s.get("args") if isinstance(s.get("args"), dict)
                  else {}),
            "output": _step_output(s, results),
        })
    return out


def _cap_output(text: str) -> str:
    text = str(text or "")
    if len(text) <= OUTPUT_CAP:
        return text
    return (text[:OUTPUT_CAP] +
            f" … [truncated: {len(text) - OUTPUT_CAP} more chars]")


def build_bundle(goal: str, steps: List[dict], rubric: dict,
                 probes: Optional[List[dict]] = None) -> str:
    """The grader's entire world: goal, rubric, recorded step
    outputs (claims), destination probes (evidence)."""
    lines = [f"GOAL: {goal}", "",
             f"RUBRIC: {rubric['id']} v{rubric['version']}",
             "CRITERIA:"]
    for c in rubric["criteria"]:
        scope = c["scope"]
        scope_txt = scope if isinstance(scope, str) \
            else "tools: " + ", ".join(scope)
        lines.append(f"- [{c['kind']}, scope: {scope_txt}] "
                     f"{c['id']}: {c['text']}")
    if rubric["failure_cases"]:
        lines.append("FAILURE CASES TO CHECK:")
        for fc in rubric["failure_cases"]:
            lines.append(f"- {fc['id']}: {fc['text']}")
    lines.append("")
    lines.append("STEP EVIDENCE (recorded outputs are claims):")
    if steps:
        for s in steps:
            desc = f" — {s['description']}" if s["description"] else ""
            lines.append(f"- step {s['step']} ({s['tool']}){desc}: "
                         f"{_cap_output(s['output']) or '(no output)'}")
    else:
        lines.append("(no steps)")
    lines.append("")
    lines.append("DESTINATION PROBES (read back at grade time; "
                 "these are evidence):")
    if probes:
        for p in probes:
            lines.append("- " + _render_probe(p))
    else:
        lines.append("(none available)")
    return "\n".join(lines)


def _render_probe(p: dict) -> str:
    kind = p.get("kind")
    head = f"step {p.get('step')} ({p.get('tool', '?')})"
    if kind == "journal":
        if p.get("state"):
            return (f"{head} journal receipt: state="
                    f"{p['state']}")
        return f"{head} journal receipt: no receipt found"
    if kind == "file":
        if not p.get("exists"):
            return (f"{head} file probe: {p.get('path', '?')} "
                    f"does not exist")
        sample = str(p.get("sample") or "").replace("\n", " ")
        return (f"{head} file probe: {p.get('path', '?')} exists, "
                f"{p.get('size', '?')} bytes, sha256 "
                f"{str(p.get('sha256') or '')[:12]}…, sample: "
                f"\"{sample[:_SAMPLE_CAP]}\"")
    return f"{head} probe: {p}"


# ------------------------------------------------------------------
# Destination probes (engine-run, read-only, never raise)
# ------------------------------------------------------------------

_FILE_WRITE_TOOLS = ("write_file", "code_helper", "dev_agent")


def _file_probe(step: dict) -> Optional[dict]:
    tool = step["tool"]
    args = step.get("parameters") or {}
    if tool == "write_file":
        path = args.get("filename") or args.get("path")
    else:
        path = args.get("output_path") or args.get("file_path")
    if not path:
        return None
    probe = {"kind": "file", "step": step["step"], "tool": tool,
             "path": str(path), "exists": False}
    try:
        from aria import action_journal, workspaces
        from aria.tools import builtins
        try:
            resolved = workspaces.resolve_workspace_file(
                str(path), builtins.WORKSPACE_DIR)
        except Exception:
            resolved = str(path)
        info = action_journal.check_artifact(resolved)
        probe["path"] = info.get("path", str(path))
        if not info.get("verified") and "File missing" in \
                str(info.get("reason") or ""):
            return probe
        probe["exists"] = True
        probe["size"] = info.get("size")
        probe["sha256"] = info.get("sha256")
        try:
            with open(info.get("path", resolved), "r",
                      encoding="utf-8", errors="replace") as f:
                probe["sample"] = f.read(_SAMPLE_CAP)
        except Exception:
            probe["sample"] = ""
    except Exception as e:
        probe["error"] = f"{type(e).__name__}"
    return probe


def build_probes(steps: List[dict],
                 journal_ids: Optional[Dict[Any, str]] = None
                 ) -> List[dict]:
    """Read-only destination probes for state-changing steps.
    journal_ids maps a step number → its action-journal
    operation id (each engine knows its own id scheme); steps
    without a supplied id simply get no journal probe. File
    probes cover the file-writing tools. Never raises."""
    from aria.tool_policy import retry_safe
    probes: List[dict] = []
    journal_ids = journal_ids or {}
    for s in steps or []:
        tool = s.get("tool") or ""
        try:
            if tool in _FILE_WRITE_TOOLS:
                fp = _file_probe(s)
                if fp is not None:
                    probes.append(fp)
            if not retry_safe(tool):
                op_id = journal_ids.get(s.get("step"))
                if op_id is None:
                    op_id = journal_ids.get(str(s.get("step")))
                if op_id:
                    from aria import action_journal
                    row = action_journal.get(op_id)
                    probes.append({
                        "kind": "journal", "step": s.get("step"),
                        "tool": tool,
                        "state": (row or {}).get("state")})
        except Exception as e:
            add_log(f"grader: probe for step {s.get('step')} "
                    f"failed ({e}); probe omitted")
    return probes


# ------------------------------------------------------------------
# Grader call
# ------------------------------------------------------------------

def _roster() -> List[str]:
    try:
        from aria.agent.orpool import TEXT_MODELS
        return list(TEXT_MODELS)
    except Exception:
        return []


def _choose_model(policy: str, worker_model: Optional[str],
                  roster: List[str]) -> Optional[str]:
    """The grader leg under the policy. Pass 36 #29: when the
    worker model is UNKNOWN (None) there is nothing to exclude
    and nothing to presume — the roster head is picked as the
    grader, but the result labels provenance unknown and
    self_graded stays False; roster[0] is never treated as
    "the model that did the work"."""
    if not roster:
        return worker_model if policy == "same" else None
    if policy == "same":
        return worker_model
    if policy == "cheap":
        return roster[-1]
    if worker_model is None:
        return roster[0]
    for m in roster:
        if m != worker_model:
            return m
    return None


def _default_chat(prompt: str, system: str,
                  model: Optional[str], *,
                  deadline: Optional[float] = None,
                  exclude: Optional[str] = None):
    """Pool-backed chat reporting the model that answered.
    Raises when the pool cannot serve — grade() turns that
    into cannot_grade. deadline/exclude are the _walk bounds
    (pass 36 #29/#31): a monotonic wall-clock bound and a
    model the walk must never fall back onto."""
    from aria.agent.orpool import get_pool
    return get_pool().chat_traced(
        prompt, system=system, model=model,
        max_tokens=2048, temperature=0.2,
        deadline=deadline, exclude=exclude)


def _parse_grade(raw: Any, rubric: dict) -> Optional[dict]:
    """Strict contract: a JSON object with a criteria array.
    Returns the computed verdict parts, or None (cannot_grade).
    The overall verdict is computed from the criteria, never
    taken from the model's own summary of itself."""
    try:
        text = _FENCE_RE.sub("", str(raw or "")).strip()
        text = text.rstrip("`").strip()
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or \
            not isinstance(data.get("criteria"), list):
        return None
    by_id: Dict[str, dict] = {}
    for entry in data["criteria"]:
        if isinstance(entry, dict) and entry.get("id"):
            by_id[str(entry["id"])] = entry
    criteria_out = []
    failed: List[str] = []
    incomplete = False
    for c in rubric["criteria"]:
        entry = by_id.get(c["id"])
        verdict = str((entry or {}).get("verdict") or "").lower()
        if verdict not in ("pass", "fail", "na"):
            verdict = "na"
        if verdict == "na" and c["kind"] == "must":
            # A must criterion the grader could not judge —
            # missing entry, bad verdict string, or an explicit
            # na — makes the grade incomplete, not a pass.
            incomplete = True
        evidence = str((entry or {}).get("evidence") or "")[
            :_EVIDENCE_CAP]
        criteria_out.append({"id": c["id"], "verdict": verdict,
                             "evidence": evidence})
        if verdict == "fail" and c["kind"] == "must":
            failed.append(c["id"])
    case_by_id: Dict[str, dict] = {}
    for entry in data.get("failure_cases") or []:
        if isinstance(entry, dict) and entry.get("id"):
            case_by_id[str(entry["id"])] = entry
    cases_out = []
    for fc in rubric["failure_cases"]:
        entry = case_by_id.get(fc["id"])
        present = bool(entry and entry.get("present") is True)
        evidence = str((entry or {}).get("evidence") or "")[
            :_EVIDENCE_CAP]
        cases_out.append({"id": fc["id"], "present": present,
                          "evidence": evidence})
        if present:
            failed.append(fc["id"])
    if incomplete:
        return {"verdict": "cannot_grade", "criteria": criteria_out,
                "failure_cases": cases_out, "failed_criteria": failed}
    return {"verdict": "fail" if failed else "pass",
            "criteria": criteria_out, "failure_cases": cases_out,
            "failed_criteria": failed}


def _cannot_grade(rubric: dict, reason: str,
                  worker_model: Optional[str] = None) -> dict:
    add_log(f"grader: cannot grade ({reason})")
    return {"verdict": "cannot_grade",
            "rubric_id": rubric["id"],
            "rubric_version": rubric["version"],
            "criteria": [], "failure_cases": [],
            "failed_criteria": [],
            "grader_model": "", "self_graded": False,
            "worker_model": worker_model}


def grade(goal: str, steps: List[dict],
          results: Optional[dict] = None, *,
          rubric: Optional[dict] = None,
          task_type: Optional[str] = None,
          worker_model: Optional[str] = None,
          chat: Optional[Callable] = None,
          probes: Optional[List[dict]] = None,
          policy: Optional[str] = None,
          deadline: Optional[float] = None) -> dict:
    """Grade one finished run. Returns the GradeResult dict
    from the design; never raises — EXCEPT runtime.Cancelled,
    which propagates (pass 36 #31 coordinates with the
    cancel-propagation fix: a cancelled grade must not be
    swallowed into a cannot_grade that the engines then treat
    as "unverifiable, carry on").

    worker_model: the model (or model set label) that did the
    work, captured by the caller (planner via chat_traced for
    delegated tasks). None means UNKNOWN provenance: the
    result records worker_model None, self_graded is False,
    and the grade note says so — roster[0] is never presumed
    to be the worker (pass 36 #29).
    deadline: monotonic-seconds (time.monotonic) wall-clock
    bound for the whole grade, derived from the run's
    remaining deadline/budget by the caller (pass 36 #31).
    A grade that cannot finish inside it is cannot_grade,
    and the already-completed work is retained by the caller.
    The evidence bundle is capped at _BUNDLE_CAP chars in
    total (per-artifact samples were already capped when the
    probes were built; full-file hashing is the probe layer's
    bound, not this module's)."""
    if rubric is None:
        tools = [s.get("tool") for s in steps or []
                 if isinstance(s, dict)]
        rubric = _rubrics.select_rubric(task_type, tools)
    try:
        norm = normalize_steps(steps, results)
        if probes is None:
            probes = build_probes(norm)
        bundle = build_bundle(goal, norm, rubric, probes)
        if len(bundle) > _BUNDLE_CAP:
            bundle = (bundle[:_BUNDLE_CAP] +
                      f"\n… [bundle truncated: "
                      f"{len(bundle) - _BUNDLE_CAP} more chars]")
        policy = policy if policy in _POLICIES else grader_policy()
        roster = _roster()
        chosen = _choose_model(policy, worker_model, roster)
        # Pass 36 #29: exclusion is enforced inside the walk,
        # not just at the pick — the pool loop must never fall
        # back onto the worker when the preferred grader fails.
        exclude = worker_model if policy == "different" else None
        if deadline is not None and time.monotonic() >= deadline:
            return _cannot_grade(
                rubric, "grading deadline already passed",
                worker_model)
        call = chat or _default_chat
        try:
            raw = call(bundle, _SYSTEM, chosen,
                       deadline=deadline, exclude=exclude)
        except TypeError:
            # Injected 3-arg chat callables (the (prompt, system,
            # model) contract tests and engines use) do not take
            # the walk bounds — retry positionally. The real
            # default chat takes them, so production never lands
            # here.
            raw = call(bundle, _SYSTEM, chosen)
        if isinstance(raw, tuple):
            raw_text, model_used = raw[0], str(raw[1] or "")
        else:
            raw_text, model_used = raw, (chosen or "")
        parsed = _parse_grade(raw_text, rubric)
        if parsed is None:
            return _cannot_grade(
                rubric, "grader output unparseable", worker_model)
        result = {
            "verdict": parsed["verdict"],
            "rubric_id": rubric["id"],
            "rubric_version": rubric["version"],
            "criteria": parsed["criteria"],
            "failure_cases": parsed["failure_cases"],
            "failed_criteria": parsed["failed_criteria"],
            "grader_model": model_used,
            "self_graded": bool(
                model_used and worker_model
                and model_used == worker_model),
            "worker_model": worker_model,
        }
        add_log(f"grader: {rubric['id']} v{rubric['version']} → "
                f"{result['verdict']} (grader: "
                f"{model_used or 'unknown'}"
                f"{', self-graded' if result['self_graded'] else ''}"
                f"{', worker unknown' if worker_model is None else ''})")
        return result
    except runtime.Cancelled:
        raise
    except Exception as e:
        return _cannot_grade(rubric, f"{type(e).__name__}: {e}",
                             worker_model)


def format_failure_reason(result: dict) -> str:
    """The structured rejection a human resume reads: failed
    criteria + the grader's cited evidence, bounded."""
    parts = []
    cited = {c["id"]: c.get("evidence", "")
             for c in result.get("criteria") or []}
    cited.update({fc["id"]: fc.get("evidence", "")
                  for fc in result.get("failure_cases") or []})
    for fid in result.get("failed_criteria") or []:
        ev = cited.get(fid, "")
        parts.append(f"[{fid}] {ev}" if ev else f"[{fid}]")
    reason = (f"grader (rubric {result.get('rubric_id')} "
              f"v{result.get('rubric_version')}) failed: "
              + "; ".join(parts))
    if result.get("self_graded"):
        reason += " (self-graded)"
    return reason[:800]


def format_grade_note(result: dict) -> str:
    """One-line rendering for summaries / status surfaces.
    Unknown worker provenance is labelled explicitly (pass 36
    #29) — a grade never silently claims independence it
    cannot establish."""
    note = (f"Grade: {result.get('rubric_id')} "
            f"v{result.get('rubric_version')} "
            f"{str(result.get('verdict') or '').upper()}"
            f" — grader {result.get('grader_model') or 'unknown'}")
    if result.get("self_graded"):
        note += ", self-graded"
    elif result.get("worker_model") is None:
        note += ", worker unknown"
    failed = result.get("failed_criteria") or []
    if failed:
        note += f"; failed criteria: {', '.join(failed)}"
    return note + "."
