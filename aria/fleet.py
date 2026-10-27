"""Fleet — what is actually running, rendered from runtime state.

Ported from Mark-LVI-ARIA's core/fleet.py (CC BY-NC 4.0, see
LICENSES/), adapted to this codebase's sources. Alek's rule for
the view (2026-10-22, carried here 2026-10-27): every row comes
from runtime state — a registry in this process, a store the
owning module wrote, a live status call. Nothing is composed
from anything the model said. If a piece of work is not in the
data, it does not appear, and the empty state says so plainly,
because the empty state IS the feature: "Nothing running. If
she says she's working in the background, it will appear here."

The activity registry: tool calls are the unit of work in
flight, and this app has one execution funnel —
aria.tools.dispatch.execute_tool — which registers each call
(begin) and closes it (finish) on the way out, so the registry
holds the calls in flight now (with elapsed) and the newest
finished calls (with durations and failure flags).

snapshot() composes delegated tasks (the task queue), the
schedule, saved routines, and MCP servers — each source
guarded on its own: a broken source costs its section, never
the view. render_text() paints the Fleet section of the
dashboard's Tasks tab (Alek, 2026-10-27: inside Tasks — no
new tab). Every function here never raises: a status surface
must never be able to break the work it watches.
"""
from __future__ import annotations

import threading
import time
from collections import deque

RECENT_CAP = 30          # finished calls kept in the registry tail
DETAIL_LIMIT = 120       # a call detail's visible length
GOAL_LIMIT = 60          # a delegated goal's visible length

_ACTIVE_DELEGATED = ("queued", "running", "blocked",
                     "blocked_model", "blocked_input",
                     "blocked_unknown", "pending", "in_progress")

# ── The activity registry ──────────────────────────────────────

_lock = threading.Lock()
_inflight: dict = {}
_recent: deque = deque(maxlen=RECENT_CAP)
_next_token = 0


def begin(tool, detail: str = ""):
    """Register an in-flight tool call; returns an opaque token
    for finish(). Never raises — a token of None simply means
    the call goes unrecorded."""
    global _next_token
    try:
        with _lock:
            _next_token += 1
            token = _next_token
            _inflight[token] = {
                "tool": str(tool or "?"),
                "detail": str(detail or ""),
                "started": time.time(),
            }
        return token
    except Exception:
        return None


def finish(token, ok: bool = True) -> None:
    """Close out a call begun with begin(): it leaves the
    in-flight set and joins the recent tail with its duration.
    An unknown or None token is a no-op. Never raises."""
    try:
        if token is None:
            return
        with _lock:
            row = _inflight.pop(token, None)
            if row is None:
                return
            now = time.time()
            _recent.append({
                "tool": row["tool"],
                "detail": row["detail"],
                "started": row["started"],
                "finished": now,
                "duration": max(0.0, now - row["started"]),
                "ok": bool(ok),
            })
    except Exception:
        pass


def current(now: float = None) -> list:
    """The calls in flight right now, oldest first, each with
    its elapsed seconds. Never raises."""
    now = time.time() if now is None else now
    try:
        with _lock:
            rows = [dict(r) for r in _inflight.values()]
    except Exception:
        return []
    for r in rows:
        r["elapsed"] = max(0.0, now - r["started"])
    rows.sort(key=lambda r: r["started"])
    return rows


def recent(n: int = None) -> list:
    """The finished tail, newest first. Never raises."""
    try:
        with _lock:
            rows = [dict(r) for r in _recent]
    except Exception:
        return []
    rows.reverse()
    if n is not None:
        rows = rows[: max(0, int(n))]
    return rows


def reset_for_tests() -> None:
    global _next_token
    with _lock:
        _inflight.clear()
        _recent.clear()
        _next_token = 0


# ── What the funnel records ────────────────────────────────────

def detail_for(name, args) -> str:
    """The human-meaningful detail of a call — what makes a
    registry row evidence rather than a label: the code itself
    for run_python_code (the "is she really looking at code?"
    case), a file path for file tools, a URL for fetches.
    Anything else records no detail rather than an invented
    summary."""
    try:
        args = args or {}
        if name == "run_python_code":
            code = str(args.get("code") or "").strip()
            first = code.splitlines()[0] if code else ""
            return first[:DETAIL_LIMIT]
        for key in ("path", "file", "target", "url", "query", "name"):
            val = str(args.get(key) or "").strip()
            if val:
                return val[:DETAIL_LIMIT]
    except Exception:
        pass
    return ""


def result_ok(result) -> bool:
    """Did a finished call fail? Read off the result text the
    funnel itself produced: its error brackets and the unknown-
    tool line. Anything else — including a self-heal note — is
    not a failure."""
    try:
        text = str(result or "")
    except Exception:
        return True
    # Outcome typing first (aria/outcome.py): a rendered failed
    # outcome is a failure whatever words follow it; done /
    # unverified / ask / started are not. Guarded — outcome is an
    # optional sibling, and the marker logic below stays the
    # fallback for prose results.
    try:
        from aria import outcome as _outcome
        if _outcome.is_failure(text):
            return False
        if _outcome.kind_of(text) is not None:
            return True
    except Exception:
        pass
    if text.startswith("Unknown tool:"):
        return False
    for marker in ("[Tool Error", "[Argument error",
                   "[Duplicate call blocked", "status=failed"):
        if marker in text[:160]:
            return False
    return True


# ── Snapshot (each source guarded) ─────────────────────────────

def _delegated_rows() -> list:
    try:
        from aria.agent.taskqueue import get_queue
        rows = []
        for t in get_queue().get_all_statuses() or []:
            status = str(t.get("status") or "?")
            rows.append({
                "goal": str(t.get("goal") or "")[:GOAL_LIMIT],
                "status": status,
                "active": status.lower() in _ACTIVE_DELEGATED,
                "error": str(t.get("error") or "")[:120],
                "result": str(t.get("result") or "")[:120],
            })
        rows.sort(key=lambda r: 0 if r["active"] else 1)
        return rows
    except Exception:
        return []


def _scheduled_count() -> int:
    try:
        from aria.scheduler import sched_list
        return len(sched_list() or [])
    except Exception:
        return 0


def _routines_count() -> int:
    try:
        import os
        from aria import routines
        d = routines._routines_dir()
        return len([f for f in os.listdir(d) if f.endswith(".json")])
    except Exception:
        return 0


def _mcp_rows() -> list:
    try:
        from aria import mcp
        servers = mcp.get_servers() or {}
        connected = set()
        try:
            for row in mcp.get_bridge().status() or []:
                if isinstance(row, dict) and row.get("connected"):
                    connected.add(str(row.get("name") or ""))
        except Exception:
            pass
        return [{"name": str(name), "connected": str(name) in connected}
                for name in servers]
    except Exception:
        return []


def snapshot(now: float = None) -> dict:
    """The whole picture, from the owning modules. One broken
    source costs its section, never the snapshot."""
    now = time.time() if now is None else now
    snap = {
        "inflight": current(now),
        "recent": recent(10),
        "delegated": _delegated_rows(),
        "scheduled": _scheduled_count(),
        "routines": _routines_count(),
        "mcp": _mcp_rows(),
    }
    snap["running"] = running_count(snap)
    return snap


def running_count(snap: dict = None) -> int:
    """Work actually in flight: tool calls running now plus
    delegated tasks in an active state. The Tasks plaque and
    the Fleet section read the same number's sources, so the
    surfaces cannot disagree about whether she is busy."""
    snap = snap if snap is not None else snapshot()
    return len(snap.get("inflight") or []) + sum(
        1 for t in snap.get("delegated") or [] if t.get("active"))


# ── Rendering ──────────────────────────────────────────────────

def _fmt_secs(s: float) -> str:
    s = max(0.0, float(s or 0))
    if s < 60:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def render_text(snap: dict = None) -> str:
    """The Fleet section of the Tasks tab, as plain text."""
    snap = snap if snap is not None else snapshot()
    lines = ["FLEET — what is actually running (runtime state, not her word)"]
    inflight = snap.get("inflight") or []
    delegated = snap.get("delegated") or []
    active = [t for t in delegated if t.get("active")]
    if not inflight and not active:
        lines.append("Nothing running. If she says she's working in the "
                     "background, it will appear here.")
    if inflight:
        lines.append(f"\nIN FLIGHT ({len(inflight)})")
        for r in inflight:
            detail = f" — {r['detail']}" if r.get("detail") else ""
            lines.append(f"  {r['tool']}{detail}  ({_fmt_secs(r.get('elapsed', 0))})")
    if delegated:
        lines.append(f"\nDELEGATED ({len(active)} active / {len(delegated)} total)")
        for t in delegated[:12]:
            note = t.get("error") or t.get("result") or ""
            lines.append(f"  {t['status'].upper()}  {t['goal']}"
                         + (f"\n    {note}" if note else ""))
    recent_rows = snap.get("recent") or []
    if recent_rows:
        lines.append("\nRECENT CALLS")
        for r in recent_rows:
            detail = f" — {r['detail']}" if r.get("detail") else ""
            flag = "ok" if r.get("ok") else "FAILED"
            lines.append(f"  {r['tool']}{detail}  [{flag}, {_fmt_secs(r.get('duration', 0))}]")
    mcp = snap.get("mcp") or []
    mcp_txt = (f"{sum(1 for s in mcp if s['connected'])}/{len(mcp)} connected"
               if mcp else "none configured")
    lines.append(f"\nSCHEDULED: {snap.get('scheduled', 0)} · "
                 f"ROUTINES SAVED: {snap.get('routines', 0)} · MCP: {mcp_txt}")
    return "\n".join(lines)
