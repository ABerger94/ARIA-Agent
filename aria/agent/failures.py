"""
ARIA failure surfacing (Merger Phase 1 — blueprint SS3.2; completes
audit SS1D / SS3B-4).

Before this module, most subsystem failures went to log_silent() and
were never seen again: 99 real call sites wrote to a 200 KB forensic
file nobody reads in real time. Now every failure that matters gets:

  * a CLASS from a fixed taxonomy — retry / skip / replan / abort
    (the taxonomy is Mark-XXXIX-OR's error_handler.ErrorDecision,
    reimplemented as a pattern; his classifier was an LLM call per
    failure, this one is deterministic rules — no model ever runs on
    the voice hot path to classify a failure);
  * a one-sentence REASON;
  * a USER MESSAGE of at most ERROR_USER_MESSAGE_MAX_WORDS (15) words;
  * a RECORD in both stores: the bounded event ring
    (aria.config.add_log — the data layer the OPS Log tab reads, kept
    deliberately even though the OPS screen is slated for removal)
    and the incident DB (aria.memory.incident_db_log, the
    autonomous_incidents table).

Surfacing never raises and never floods: identical failures from the
same source inside THROTTLE_S are counted, not re-recorded, and the
next record after the window notes the repeat count. Setting
ARIA_FAILURE_SURFACING=0 (config.FAILURE_SURFACING) restores the old
log_silent-only behaviour — that is the rollback (blueprint SS6).

Voice loudness (blueprint SS3.2, approved): records here are NOT
spoken. The only failures announced aloud are delegated-task
outcomes, and the Phase 3 executor does that speaking itself with
the record's user_message.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Optional, Union

from aria import config as _config
from aria.config import ERROR_USER_MESSAGE_MAX_WORDS, log_silent

# --- Taxonomy (Mark-XXXIX-OR error_handler.ErrorDecision, as strings) ---
RETRY = "retry"    # transient: the same step can succeed if tried again
SKIP = "skip"      # not critical: the work can succeed without this piece
REPLAN = "replan"  # approach was wrong: retrying unchanged will not fix it
ABORT = "abort"    # impossible or unsafe to continue: stop

DECISIONS = (RETRY, SKIP, REPLAN, ABORT)

# Default user-facing line per decision — each well under the 15-word
# cap (blueprint SS3.2). Callers may override per failure.
_DEFAULT_USER_MESSAGES = {
    RETRY: "That hit a temporary snag — retrying now.",
    SKIP: "One part failed and was skipped; the rest continues.",
    REPLAN: "That approach failed, so I'm trying a different one.",
    ABORT: "That failed and cannot continue, so I stopped it.",
}

_DEFAULT_REASONS = {
    RETRY: "Transient failure; the same step can succeed if tried again.",
    SKIP: "The failing piece is not critical; the work continues without it.",
    REPLAN: "Retrying unchanged will not fix this; a different approach is needed.",
    ABORT: "The failure is not recoverable from here; continuing is pointless or unsafe.",
}

# --- Deterministic signal rules (blueprint SS3.2: rules before models) ---
_TRANSIENT_RE = re.compile(
    r"\b429\b|rate.?limit|too many requests|quota|timed? ?out|timeout|"
    r"connection|network|temporarily|service unavailable|bad gateway|"
    r"gateway timeout|\b50[234]\b|disconnected|unavailable|econnreset|"
    r"broken pipe|overloaded", re.I)
_PERMANENT_RE = re.compile(
    r"permission denied|access denied|unauthorized|\b401\b|\b403\b|"
    r"invalid api key|authentication|not allowed|forbidden|"
    r"insufficient funds|payment required", re.I)
_APPROACH_RE = re.compile(
    r"no such file|not found|filenotfounderror|no module named|"
    r"modulenotfounderror|missing required|argument|syntaxerror|"
    r"invalid|parse|decode|keyerror|attributeerror|typeerror|valueerror",
    re.I)


def _err_text(error: Union[str, BaseException, Any]) -> str:
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {error}"
    return str(error)


def _cap_words(msg: str, limit: int = ERROR_USER_MESSAGE_MAX_WORDS) -> str:
    words = str(msg).split()
    if len(words) <= limit:
        return str(msg)
    return " ".join(words[:limit]).rstrip(".,;:") + "."


def classify_failure(source: str, error: Union[str, BaseException],
                     *, critical: bool = False, attempts: int = 0,
                     max_attempts: Optional[int] = None,
                     unknown_tool: bool = False) -> Dict[str, Any]:
    """Deterministic-first failure classifier. Pure function of the
    error text + caller context; no I/O, no model. Returns a record
    dict {decision, reason, user_message} (surface_failure adds the
    rest). Rules, in order:

      unknown tool            -> replan (abort when criticality is
                                 enforced by the caller; the Phase 3
                                 executor hard-aborts unknown tools
                                 itself, per blueprint SS2.1)
      transient signals       -> retry, until the attempt budget is
                                 out, then replan (critical) / skip
      permanent signals       -> abort (critical) / skip
      approach signals        -> replan (critical) / skip; budget out
                                 escalates critical to abort
      anything else           -> replan; budget out -> abort
    """
    text = _err_text(error)
    low = text.lower()
    budget_out = max_attempts is not None and attempts >= max_attempts

    if unknown_tool or "unknown tool" in low:
        decision = REPLAN
        reason = (f"'{source}' is not a real tool; the step must name "
                  f"a tool that exists.")
    elif _TRANSIENT_RE.search(low):
        if budget_out:
            decision = REPLAN if critical else SKIP
        else:
            decision = RETRY
        reason = _DEFAULT_REASONS[decision]
    elif _PERMANENT_RE.search(low):
        decision = ABORT if critical else SKIP
        reason = _DEFAULT_REASONS[decision]
    elif _APPROACH_RE.search(low):
        if budget_out:
            decision = ABORT if critical else SKIP
        else:
            decision = REPLAN if critical else SKIP
        reason = _DEFAULT_REASONS[decision]
    else:
        decision = ABORT if budget_out else REPLAN
        reason = _DEFAULT_REASONS[decision]

    return {
        "decision": decision,
        "reason": reason,
        "user_message": _DEFAULT_USER_MESSAGES[decision],
    }


# --- Throttle: identical failures are counted, not re-recorded --------
THROTTLE_S = 60.0
_THROTTLE_LOCK = threading.Lock()
_THROTTLE: Dict[Any, Dict[str, Any]] = {}  # key -> {ts, suppressed}


def _throttle_key(source: str, decision: str, err_text: str):
    sig = re.sub(r"[^a-z0-9]+", " ", err_text.lower())[:80].strip()
    return (source, decision, sig)


def clear_throttle() -> None:
    """Reset surfacing throttle state (tests; also safe anytime)."""
    with _THROTTLE_LOCK:
        _THROTTLE.clear()


def _check_throttle(key):
    """Returns (should_record, suppressed_count). Inside the throttle
    window: (False, running repeat count) — count it, don't record it.
    Outside: (True, repeats suppressed since the last record) — record
    fresh and fold the count into the reason."""
    now = time.time()
    with _THROTTLE_LOCK:
        ent = _THROTTLE.get(key)
        if ent and now - ent["ts"] < THROTTLE_S:
            ent["suppressed"] += 1
            return False, ent["suppressed"]
        suppressed = ent["suppressed"] if ent else 0
        _THROTTLE[key] = {"ts": now, "suppressed": 0}
        return True, suppressed


# --- Sink-failure counter (for when a store itself is the failure) -----
_SINK_FAILURES: Dict[str, int] = {}
_SINK_LOCK = threading.Lock()


def note_sink_failure(context: str, exc: BaseException) -> None:
    """Called when one of the failure stores fails (today: the event
    ring inside config.add_log). Counts the failure and best-effort
    writes an incident row — never touches the ring (that's what's
    broken) and never raises."""
    try:
        with _SINK_LOCK:
            _SINK_FAILURES[context] = _SINK_FAILURES.get(context, 0) + 1
        try:
            from aria.memory import incident_db_log
            incident_db_log("failure_sink", context, _err_text(exc),
                            "A failure store failed while recording.",
                            "counted", resolved=0)
        except Exception:
            pass
    except Exception:
        pass


def sink_failure_count(context: Optional[str] = None) -> int:
    with _SINK_LOCK:
        if context is not None:
            return _SINK_FAILURES.get(context, 0)
        return sum(_SINK_FAILURES.values())


# --- The surfacing entry point ------------------------------------------

def surface_failure(source: str, error: Union[str, BaseException],
                    *, critical: bool = False, attempts: int = 0,
                    max_attempts: Optional[int] = None,
                    unknown_tool: bool = False,
                    decision: Optional[str] = None,
                    reason: Optional[str] = None,
                    user_message: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Classify a failure and record it in both stores.

    Returns the record dict (with "recorded": False when the throttle
    suppressed the write), or None when surfacing is disabled or every
    store failed. NEVER raises.
    """
    err_text = _err_text(error)
    try:
        if not getattr(_config, "FAILURE_SURFACING", True):
            # Rollback posture: exactly the pre-Phase-1 behaviour.
            log_silent(source, error if isinstance(error, BaseException)
                       else RuntimeError(err_text))
            return None

        cls = classify_failure(source, err_text, critical=critical,
                               attempts=attempts, max_attempts=max_attempts,
                               unknown_tool=unknown_tool)
        dec = decision if decision in DECISIONS else cls["decision"]
        rsn = reason or cls["reason"]
        umsg = _cap_words(user_message or cls["user_message"])

        should_record, suppressed = _check_throttle(
            _throttle_key(source, dec, err_text))
        record = {
            "ts": time.time(),
            "source": source,
            "error": err_text[:500],
            "decision": dec,
            "reason": rsn,
            "user_message": umsg,
            "critical": bool(critical),
            "attempts": attempts,
            "recorded": should_record,
            "suppressed": suppressed,
        }
        if not should_record:
            return record
        if suppressed:
            rsn = f"{rsn} (repeated {suppressed} more times in the last minute)"
            record["reason"] = rsn

        wrote = False
        # Store 1: the bounded event ring (OPS Log data layer).
        try:
            _config.add_log(
                f"Failure [{dec}] {source}: {umsg} ({rsn})",
                level="warn",
                meta={"failure": True, "decision": dec, "source": source})
            wrote = True
        except Exception as e:
            log_silent("failures.ring", e)
        # Store 2: the incident DB (her existing autonomous_incidents).
        try:
            from aria.memory import incident_db_log
            incident_db_log("failure", source, err_text, rsn, dec,
                            resolved=1 if dec == SKIP else 0)
            wrote = True
        except Exception as e:
            log_silent("failures.incident_db", e)
        if not wrote:
            log_silent(source, error if isinstance(error, BaseException)
                       else RuntimeError(err_text))
            record["recorded"] = False
        return record
    except Exception as e:  # surfacing must never break the caller
        try:
            log_silent("failures.surface", e)
        except Exception:
            pass
        return None
