"""
ARIA Execution Sandboxing, Validation, and Safety Guards.
Provides Python subprocess isolation, stale target detection, duplicate blocking,
argument validation against schemas, and risky operation formatting.
"""

import hashlib
import json
import os
import re
import sys
import subprocess
from typing import Dict, List, Optional, Set, Tuple, Callable, Any

from aria.config import WORKSPACE_DIR, MAX_TOOL_OUTPUT, GITHUB_USERNAME
from aria.tools.schemas import get_tool_decls_by_name

RISKY_TOOLS: Set[str] = {
    "run_python_code", "gui_click", "gui_type",
    "open_app_or_url", "github_push_file", "send_email", "close_window"
}


def call_signature(fn_name: str, args: dict) -> str:
    """Generate deterministic signature hash for duplicate tool call prevention."""
    try:
        blob = json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        blob = str(args)
    return fn_name + ":" + hashlib.md5(blob.encode("utf-8"),
                                          usedforsecurity=False).hexdigest()


def missing_required_args(fn_name: str, args: dict, decls: Optional[Dict[str, Any]] = None) -> List[str]:
    """Check tool call arguments against schema declarations for missing/empty required params."""
    all_decls = decls or get_tool_decls_by_name()
    decl = all_decls.get(fn_name)
    if not decl:
        return []
    required = (decl.get("parameters") or {}).get("required", [])
    args = args or {}
    return [p for p in required if not args.get(p)]


def target_mentioned(target: str, message: str) -> bool:
    """True if target's distinctive tokens appear in the user message."""
    stop_words = {
        "com", "net", "org", "io", "exe", "app", "www",
        "http", "https", "open", "the"
    }
    tokens = [t for t in re.split(r"[^a-z0-9]+", (target or "").lower()) if t]
    msg = (message or "").lower()
    for t in tokens:
        if len(t) >= 3 and t not in stop_words and t in msg:
            return True
    return False


def stale_target_check(fn_name: str,
                       args: dict,
                       last_open_target: str,
                       last_user_message: str,
                       turn_nudged: bool) -> Tuple[Optional[str], bool]:
    """Return (nudge_message, new_turn_nudged).
    Prevents repetitive loop on previous turn's target if user has moved on.
    """
    if fn_name != "open_app_or_url":
        return None, turn_nudged
    target = (args or {}).get("target", "")
    if (target and last_open_target and target == last_open_target
            and not target_mentioned(target, last_user_message)):
        if not turn_nudged:
            nudge = (
                "STALE TARGET GUARD: that target matches the PREVIOUS request. "
                "If the user's LATEST message explicitly refers back to it "
                "('that one again', 'reopen it', 'the link from earlier'), "
                "proceed with this call. Otherwise re-read the LATEST message "
                "and call open_app_or_url with the correct new target."
            )
            return nudge, True
    return None, turn_nudged


def risky_description(fn_name: str, args: dict, github_username: str = GITHUB_USERNAME) -> str:
    """Format human-readable audit description of executed risky actions."""
    user = github_username or "ABerger94"
    desc_map = {
        "run_python_code": f"run Python code ({str(args.get('code', ''))[:80]}...)",
        "gui_click": f"click at screen coordinates ({args.get('x')}, {args.get('y')})",
        "gui_type": f"type '{str(args.get('text', ''))[:60]}' into the active window",
        "open_app_or_url": f"open '{args.get('target')}'",
        "github_push_file": f"push '{args.get('filepath')}' to {args.get('repo') or user + '/ARIA-Agent'}",
        "close_window": f"close the '{args.get('title')}' window",
        "send_email": f"send email to '{args.get('to')}' ('{args.get('subject', '')}')",
    }
    return desc_map.get(fn_name, f"execute {fn_name}")


def truncate_output(text: str, max_len: int = MAX_TOOL_OUTPUT) -> str:
    """Truncate tool output if it exceeds max budget."""
    if len(text) > max_len:
        return text[:max_len] + f"\n...[output truncated, {len(text)} chars total]"
    return text


def tool_run_python(code: str,
                    workspace_dir: str = WORKSPACE_DIR,
                    timeout: int = 15,
                    busy_callback: Optional[Callable[[bool], None]] = None,
                    log_callback: Optional[Callable[[str], None]] = None) -> str:
    """Execute Python code in an isolated subprocess inside the workspace directory.

    NOTE: this is process isolation only — the code runs with the same
    interpreter, user, and filesystem access as ARIA itself. Treat it as
    ARIA acting, not as an untrusted sandbox.
    """
    import tempfile
    if busy_callback:
        busy_callback(True)
    temp_script = None
    try:
        if log_callback:
            log_callback("Executing Python script...")
        os.makedirs(workspace_dir, exist_ok=True)
        fd, temp_script = tempfile.mkstemp(suffix=".py", prefix="_aria_run_",
                                           dir=workspace_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            result = subprocess.run(
                [sys.executable, temp_script],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workspace_dir
            )
            output = result.stdout + result.stderr
            if log_callback:
                log_callback("Execution complete.")
            return output if output.strip() else "[Code ran with no console output]"
        except subprocess.TimeoutExpired:
            return f"[Execution Error: Script timed out after {timeout} seconds]"
        except Exception as e:
            return f"[Execution Error: {e}]"
    finally:
        if temp_script and os.path.exists(temp_script):
            try:
                os.remove(temp_script)
            except OSError:
                pass
        if busy_callback:
            busy_callback(False)
