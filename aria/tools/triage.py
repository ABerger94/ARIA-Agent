"""Notification triage — Module 6 of ARIA ULTIMATE.

``tool_triage_email(limit=20)`` reads the inbox with the EXISTING
``tool_read_email`` implementation (imported read-only from
``aria.tools.builtins`` — same package direction, no cycle) and classifies
each message via the provider chain's plain-text path
(``aria.agent.providers.provider_text``).

Conservative classification: bills, job responses, security alerts, real
humans waiting = important; promos/newsletters = noise.
"""
from __future__ import annotations

NOT_CONFIGURED_MSG = "[Gmail not set up — call gmail_setup first.]"

_TRIAGE_SYSTEM = (
    "You are triaging the user's email inbox summaries. Classify each message "
    "into exactly one bucket. Be CONSERVATIVE — when in doubt, err toward "
    "important.\n\n"
    "IMPORTANT (act now): bills or payment reminders, job applications / "
    "interview responses, security or account alerts (logins, password resets, "
    "fraud), real humans waiting on a reply, delivery problems, appointments "
    "being confirmed or changed.\n"
    "FYI (worth a glance, no rush): receipts and confirmations, shipping "
    "updates, newsletters from sources the user actually reads, personal "
    "updates from friends/family.\n"
    "NOISE (ignore): promotions, sales, marketing newsletters, spam-adjacent "
    "mailing lists, social-media notification digests, anything asking to "
    "buy something.\n\n"
    "Respond ONLY in this exact format — one message per line, then the "
    "noise count:\n"
    "IMPORTANT (act now): <one-line summary of the message>\n"
    "FYI: <one-line summary of the message>\n"
    "NOISE (ignored): <count of noise messages>"
)


def tool_triage_email(limit: int = 20) -> str:
    """Read recent email and classify it: IMPORTANT / FYI / NOISE."""
    try:
        limit = max(1, min(50, int(limit or 20)))
    except (TypeError, ValueError):
        limit = 20

    try:
        from aria.tools.builtins import tool_read_email
    except Exception as e:
        return f"[Triage unavailable: could not import email reader ({e}).]"

    raw = tool_read_email(limit=limit)
    if "isn't set up yet" in raw:
        return NOT_CONFIGURED_MSG
    if "rejected the login" in raw or "Could not reach Gmail" in raw:
        return (f"[Gmail error — check credentials with gmail_setup: "
                f"{raw[:200]}]")
    if "No matching emails." in raw or "Gmail search failed." in raw:
        return "Inbox is empty — nothing to triage."

    try:
        from aria.agent.providers import provider_text
    except Exception as e:
        return f"[Triage unavailable: provider_text import failed ({e}).]"

    user_text = (
        "Triage these email summaries (newest first):\n\n"
        f"{raw}"
    )
    answer = provider_text(_TRIAGE_SYSTEM, user_text)
    if not answer or answer.startswith("["):
        return f"[Triage failed: {answer or 'empty provider response'}]"
    return answer.strip()
