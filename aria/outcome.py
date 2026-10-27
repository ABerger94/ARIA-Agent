# Ported from Mark-LVI-ARIA core/outcome.py (commit 61a47b0e) —
# adapted only in module references (the LVI incidents recorder has
# no Agent counterpart; _record below is a guarded no-op seam).
# See LICENSES/NOTICE.md.
"""
aria/outcome.py — what a tool tells the model when it is done.

THE PROBLEM THIS EXISTS FOR
    `open_app` returned the sentence "Opened Spotify." whenever its launcher
    returned True — and on Windows that launcher returned True immediately
    after `subprocess.Popen(..., shell=True)`, which succeeds because a SHELL
    started, not because an application did. A misspelt name, an app that is
    not installed, an installer that moved: all of them produced "Opened
    Spotify.", the model said so out loud, and the user was left looking at a
    desktop where nothing had happened, told that it had.

    That is worse than an error. An error is something the user can act on. A
    confident false success sends them looking for the window.

THREE OUTCOMES, NOT TWO
    Every tool in this project used to answer with success or an exception, and
    the interesting case is neither. Driving somebody else's application means
    routinely NOT KNOWING: the launcher returned but the window has not
    appeared yet; the close request was accepted but the app is showing a "save
    changes?" dialog; the keystroke went somewhere and we cannot see where.

        done         it happened, and that was CHECKED — not assumed from a
                     return code that only proves the request was accepted
        unverified   the request went through and the result could not be
                     confirmed. NOT a failure, and never to be spoken as a
                     success
        failed       it did not happen, and here is why

WHY THE FIRST PARAMETERS ARE POSITIONAL-ONLY (the `/` in every signature)
    `action`, `reason` and `question` are the names of these functions' own
    arguments AND perfectly natural fact keys — a desktop tool wants to report
    which action it was asked for, a close tool wants to report the reason it
    matched. Written the ordinary way, `failed("desktop_control", "...",
    action="wallpaper")` raises "got multiple values for argument 'action'",
    which is a crash in the error path: the one place a crash is least
    affordable, because it replaces a message about what went wrong with a
    traceback about the message.

    The `/` makes these positional-only, so the same call now puts
    `action="wallpaper"` in the facts where it was meant to go, and no fact
    key can ever collide with a parameter name again.

WHY FACTS AND NOT SENTENCES
    The return value is read by a model that must then speak to a person in
    THEIR language. Handing it a finished English sentence invites it to
    translate or simply repeat that sentence.

    So these are facts in a fixed, machine-shaped form. The model writes the
    sentence. This module never writes prose the user could hear.

    The format is `key=value`, space separated, values quoted when they contain
    spaces. It is not JSON on purpose: a bare line survives being logged,
    truncated and pasted into a transcript, and small local models parse it
    more reliably than they parse braces.

Stdlib-only, by design: anything may import this, including modules
that load before the rest of the package is ready.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# The three words a tool may answer with, plus the one that asks and
# the one that reports work under way. Kept deliberately tiny: every
# extra synonym is another thing a small model can read as "fine, it
# worked".
DONE = "done"
UNVERIFIED = "unverified"
FAILED = "failed"
ASK = "ask"
STARTED = "started"

_KINDS = (DONE, UNVERIFIED, FAILED, ASK, STARTED)


def _render(value: Any) -> str:
    """One value, quoted only when it has to be.

    Booleans go out lowercase so `verified=true` reads the same to every model
    that has ever seen JSON, rather than Python's `True`.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace('"', "'").replace("\n", " ").strip()
    return f'"{text}"' if (" " in text or not text) else text


def _line(status: str, action: str, facts: dict) -> str:
    parts = [f"status={status}", f"action={action}"]
    parts += [f"{k}={_render(v)}" for k, v in facts.items() if v is not None]
    return " ".join(parts)


def done(action: str, /, **facts: Any) -> str:
    """It happened and it was checked.

    Use this ONLY where something was observed afterwards — a process that now
    exists, a window that went away, a text box that emptied. If the only
    evidence is that a call returned without raising, the honest answer is
    `unverified`, and saying so costs nothing: the model tells the user it has
    asked and cannot confirm, which is exactly true.
    """
    return _line(DONE, action, facts)


def _record(status: str, action: str, rendered: str) -> None:
    """Durable incident record for a problem outcome — a seam.

    LVI wrote these to core.incidents; ARIA-Agent has no equivalent
    store, so this is deliberately a guarded no-op: the import shape
    is kept so a future Agent incident sink can slot in here without
    touching callers, and any failure of the record itself is
    swallowed — this module's founding rule is that the error path
    must not crash.
    """
    try:
        from aria.agent import failures as _failures  # noqa: F401
        recorder = getattr(_failures, "record_outcome", None)
        if recorder is not None:
            recorder(status, action, rendered)
    except Exception:
        pass


def unverified(action: str, reason: str, /, **facts: Any) -> str:
    """It was requested; the result could not be confirmed.

    The instruction on the end is not decoration. Without it, a model handed
    "status=unverified" reliably rounds it off to "Done!" — this was watched
    happening, which is why the sentence spells out what may not be said.
    """
    out = _line(UNVERIFIED, action, {**facts, "reason": reason}) + (
        " -- NOT confirmed. Tell the user plainly that you asked and cannot "
        "confirm it worked, and say what they can check. Do NOT say it is done."
    )
    _record(UNVERIFIED, action, out)
    return out


def failed(action: str, reason: str, /, **facts: Any) -> str:
    """It did not happen.

    `reason` is for the user, through the model, so write it as a cause a
    person can act on — "no running application matches that name" rather than
    "returncode 1".
    """
    out = _line(FAILED, action, {**facts, "reason": reason}) + (
        " -- This did NOT happen. Tell the user so in English, with "
        "the reason. Do not claim any part of it succeeded."
    )
    _record(FAILED, action, out)
    return out


def started(action: str, /, **facts: Any) -> str:
    """It is under way on a background thread, and the result will follow.

    A tool that says `started` MUST report the ending too, off the
    thread. Saying `started` and then never mentioning a failure is
    exactly the false success this whole module exists to prevent,
    moved one step later. Silence on success is fine and is the
    intent: the user can see it, and a second announcement is noise.
    """
    return _line(STARTED, action, facts) + (
        " -- UNDER WAY, not finished. Say in one short sentence, in English, "
        "that it is being done. Do not say it is complete, and "
        "do not call this tool again to check."
    )


def ask(action: str, question: str, /, **facts: Any) -> str:
    """It was NOT done, because doing it would have been a guess.

    The case this exists for: several things match what the user said, or the
    one thing that matches is not obviously the one they meant. Refusing to
    guess is the whole point — a wrong action lands on a real person or a
    real file.
    """
    return _line(ASK, action, {**facts, "question": question}) + (
        " -- NOTHING was done yet. Put this question to the user in their own "
        "language and wait for their answer before calling again."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Parsing helpers — reading a rendered outcome back
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_RE = re.compile(r"^status=(done|unverified|failed|ask|started)(?:\s|$)")


def kind_of(text) -> Optional[str]:
    """The outcome kind a result string starts with, or None.

    A tool result rendered by this module begins `status=<kind> `;
    anything else — prose, an error bracket from another funnel, an
    empty string, a non-string — has no kind and returns None. This
    is how callers (the fleet registry, log surfaces) read the
    outcome back without re-deriving it from the wording.
    """
    if not isinstance(text, str):
        return None
    m = _STATUS_RE.match(text)
    return m.group(1) if m else None


def is_failure(text) -> bool:
    """True when the result is a rendered `failed` outcome."""
    return kind_of(text) == FAILED


def is_problem(result) -> bool:
    """Does this tool result need the user's attention?

    The activity log is for the conversation and for things that
    went wrong — not for a running commentary of every tool that
    worked. So tools print their full result and log only what this
    returns True for.
    """
    return kind_of(result) in (FAILED, UNVERIFIED, ASK)


# ─────────────────────────────────────────────────────────────────────────────
#  Content this machine did not write
# ─────────────────────────────────────────────────────────────────────────────
_OPEN = "<<<UNTRUSTED source={source}>>>"
_CLOSE = "<<<END UNTRUSTED>>>"


def untrusted(source: str, content: str) -> str:
    """Wrap text that came from outside so the model reads it as data.

    A model that can call tools, and that reads a web page, is one page
    away from being told what to do by whoever wrote the page. The same
    goes for a document it summarises, a file name it lists, an incoming
    message it relays. None of that text is the user speaking.

    It marks a boundary. The prompt carries the matching rule —
    everything between the markers is data, and an instruction inside
    them is something to REPORT, not to follow. It is not a guarantee
    or a sandbox; it is the cheapest real defence available at this
    layer, and it is worth far more than nothing.

    Content is scanned for the closing marker and any occurrence is
    defanged, because a page that can close the wrapper early can put
    instructions back into trusted space.
    """
    text = str(content or "")
    text = text.replace(_CLOSE, "<<<END-UNTRUSTED-ESCAPED>>>")
    text = text.replace("<<<UNTRUSTED", "<<<UNTRUSTED-ESCAPED")
    label = re.sub(r"[^a-z0-9_-]", "", str(source or "external").lower())[:24]
    return f"{_OPEN.format(source=label or 'external')}\n{text}\n{_CLOSE}"


_WRAP_RE = re.compile(r"<<<UNTRUSTED source=[^>]*>>>\n?(.*?)\n?<<<END UNTRUSTED>>>",
                      re.S)


def unwrap(text: str) -> str:
    """The content inside an untrusted wrapper, for showing to a PERSON.

    The markers exist so the model treats fetched text as data. A human
    looking at the HUD needs no such warning — they know they asked for a
    web search — and showing them `<<<UNTRUSTED source=web>>>` above their
    own search result is the machinery leaking through the interface.

    Anything that is not wrapped comes back unchanged, so this is safe to
    call on any tool result before displaying it.
    """
    text = str(text or "")
    inner = _WRAP_RE.findall(text)
    if not inner:
        return text
    return "\n\n".join(inner).replace("<<<END-UNTRUSTED-ESCAPED>>>",
                                      "<<<END UNTRUSTED>>>") \
                             .replace("<<<UNTRUSTED-ESCAPED", "<<<UNTRUSTED")
