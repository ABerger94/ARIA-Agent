"""
ARIA Autonomous Self-Healing Subsystem.
Intercepts tool execution failures, diagnoses root causes, executes automatic
remediation heuristics (path creation, dependency install, syntax repair, network retry),
and maintains an audit log of incidents.
"""

from __future__ import annotations

import os
import re
import sys
import time
import subprocess
import traceback
from typing import Optional, Dict, Any, Tuple, Callable, List

from aria.config import WORKSPACE_DIR, add_log
from aria.memory import (
    incident_db_log, incident_db_list, spine_append, memory_save
)

SAFE_AUTO_PACKAGES = {
    "requests", "urllib3", "bs4", "beautifulsoup4", "numpy", "pandas",
    "pytz", "python-dateutil", "pillow", "matplotlib", "tabulate",
    "tqdm", "scipy", "psutil", "pyyaml", "yaml", "rich", "pydantic"
}


def record_incident(category: str, source: str, error_text: str,
                    diagnosis: str, action_taken: str, resolved: bool = False) -> int:
    """Record an operational incident into the persistent database and spine."""
    try:
        iid = incident_db_log(
            category=category,
            source=source,
            error_text=error_text,
            diagnosis=diagnosis,
            action_taken=action_taken,
            resolved=1 if resolved else 0
        )
        spine_append("self_heal", {
            "incident_id": iid,
            "category": category,
            "source": source,
            "action": action_taken,
            "resolved": resolved
        })
        status_label = "RESOLVED" if resolved else "UNRESOLVED"
        add_log(f"Self-Heal [{status_label}] ({category}): {action_taken[:60]}")
        _maybe_promote_lesson(category, source, diagnosis, action_taken)
        return iid
    except Exception as e:
        add_log(f"Failed to record self-healing incident: {e}")
        return 0


def _maybe_promote_lesson(category: str, source: str, diagnosis: str,
                          action_taken: str) -> None:
    """Self-repair Part 3: incident learning.

    When the same (category, source) failure recurs unresolved 3+ times, write
    a durable lesson to memory so she stops tripping on it. Same-key saves are
    idempotent — the lesson refreshes rather than duplicates. Never raises.
    """
    try:
        recent = incident_db_list(50)
        same = [i for i in recent
                if i.get("category") == category
                and i.get("source") == source
                and not i.get("resolved")]
        if len(same) >= 3:
            key = f"lesson:{category}:{source}"
            memory_save(
                "self_heal", key,
                f"Recurring failure ({len(same)}x): {diagnosis}. "
                f"What was tried: {action_taken}. "
                f"Before retrying '{source}', check this lesson and change approach."
            )
            add_log(f"Self-Heal: promoted recurring {category}/{source} to durable memory.")
    except Exception as e:
        add_log(f"Lesson promotion failed: {e}")


def get_recent_incidents(limit: int = 10) -> List[Dict[str, Any]]:
    """Return recent incident records."""
    try:
        return incident_db_list(limit)
    except Exception:
        return []


def diagnose_error(source: str, error_text: str,
                   context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Analyze error messages and tracebacks to determine failure category and remedies."""
    err = str(error_text or "")

    # 1. Missing module / package
    m_pkg = re.search(r"(?:No module named|ModuleNotFoundError: No module named) ['\"]([^'\"]+)['\"]", err)
    if m_pkg:
        pkg = m_pkg.group(1).split(".")[0]
        return {
            "category": "MISSING_PACKAGE",
            "package": pkg,
            "can_auto_heal": True,
            "diagnosis": f"Missing Python package dependency: '{pkg}'",
            "recommended_action": f"pip install {pkg}"
        }

    # 2. File / Directory not found
    m_file = re.search(r"(?:No such file or directory|FileNotFoundError): ['\"]([^'\"]+)['\"]", err)
    if m_file:
        missing_path = m_file.group(1)
        return {
            "category": "PATH_NOT_FOUND",
            "path": missing_path,
            "can_auto_heal": True,
            "diagnosis": f"Missing file or target directory: '{missing_path}'",
            "recommended_action": f"Create parent directory for '{missing_path}'"
        }

    # 3. Windows Unicode Escape Syntax Error
    if "unicodeescape" in err or "truncated \\U" in err or "truncated \\u" in err:
        return {
            "category": "UNICODE_ESCAPE_SYNTAX",
            "can_auto_heal": True,
            "diagnosis": "Windows path backslashes causing Unicode escape syntax failure in code.",
            "recommended_action": "Sanitize backslashes to forward slashes or raw string literals"
        }

    # 4. General Syntax Error
    if "SyntaxError" in err:
        return {
            "category": "SYNTAX_ERROR",
            "can_auto_heal": False,
            "diagnosis": "Python syntax error in executed code.",
            "recommended_action": "Review script syntax and regenerate code."
        }

    # 5. Network / HTTP transient errors
    if any(k in err.lower() for k in ["timed out", "timeout", "503 service unavailable", "502 bad gateway", "connection reset"]):
        return {
            "category": "NETWORK_TRANSIENT",
            "can_auto_heal": True,
            "diagnosis": "Transient network timeout or gateway failure.",
            "recommended_action": "Back off and retry request."
        }

    # 6. File permission / locked error
    if "WinError 32" in err or "PermissionError" in err:
        return {
            "category": "PERMISSION_LOCKED",
            "can_auto_heal": True,
            "diagnosis": "File is locked by another process.",
            "recommended_action": "Briefly delay and retry access."
        }

    return {
        "category": "GENERIC_ERROR",
        "can_auto_heal": False,
        "diagnosis": f"Execution error in {source}: {err[:200]}",
        "recommended_action": "Manual inspection or error propagation"
    }


def attempt_auto_heal(fn_name: str, args: dict, error_or_output: Any,
                      handler: Optional[Callable] = None) -> Tuple[bool, Any, str]:
    """
    Attempt autonomous self-healing on a failed tool execution.
    Returns (healed_bool, result_output, summary_string).
    """
    if isinstance(error_or_output, Exception):
        err_str = f"{type(error_or_output).__name__}: {error_or_output}\n{traceback.format_exc()}"
    else:
        err_str = str(error_or_output)

    diag = diagnose_error(fn_name, err_str, args)
    cat = diag["category"]

    # --- Heuristic 1: Missing Directory on write_file ---
    if cat == "PATH_NOT_FOUND" and fn_name == "write_file":
        target = args.get("filename", "")
        if target:
            full_path = target if os.path.isabs(target) else os.path.join(WORKSPACE_DIR, target)
            parent_dir = os.path.dirname(full_path)
            if parent_dir and not os.path.exists(parent_dir):
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                    if handler:
                        new_res = handler(args)
                        record_incident(cat, fn_name, err_str, diag["diagnosis"],
                                        f"Auto-created directory '{parent_dir}' and rewrote file", resolved=True)
                        return True, new_res, f"Created directory '{parent_dir}' and saved file."
                except Exception as e:
                    add_log(f"Self-heal mkdir failed: {e}")

    # --- Heuristic 2: Missing Directory on run_python_code ---
    if cat == "PATH_NOT_FOUND" and fn_name == "run_python_code":
        missing_path = diag.get("path", "")
        if missing_path:
            parent = os.path.dirname(missing_path) if os.path.splitext(missing_path)[1] else missing_path
            if parent and not os.path.exists(parent):
                try:
                    os.makedirs(parent, exist_ok=True)
                    if handler:
                        new_res = handler(args)
                        if "Traceback" not in str(new_res) and "Error" not in str(new_res):
                            record_incident(cat, fn_name, err_str, diag["diagnosis"],
                                            f"Auto-created missing directory '{parent}' and re-executed", resolved=True)
                            return True, new_res, f"Created missing directory '{parent}' and re-ran code."
                except Exception as e:
                    add_log(f"Self-heal python mkdir failed: {e}")

    # --- Heuristic 3: Windows Unicode Escape Syntax in run_python_code ---
    if cat == "UNICODE_ESCAPE_SYNTAX" and fn_name == "run_python_code":
        code = args.get("code", "")
        if code:
            fixed_code = re.sub(r'([A-Za-z]):\\([a-zA-Z0-9_\-\\]+)',
                                lambda m: m.group(0).replace('\\', '/'), code)
            if fixed_code != code:
                new_args = dict(args)
                new_args["code"] = fixed_code
                if handler:
                    try:
                        new_res = handler(new_args)
                        if "SyntaxError" not in str(new_res):
                            record_incident(cat, fn_name, err_str, diag["diagnosis"],
                                            "Sanitized Windows path backslashes in code", resolved=True)
                            return True, new_res, "Sanitized Windows path backslashes and re-executed code successfully."
                    except Exception as e:
                        add_log(f"Self-heal backslash sanitize retry failed: {e}")

    # --- Heuristic 4: Missing Package in run_python_code ---
    if cat == "MISSING_PACKAGE" and fn_name == "run_python_code":
        pkg = diag.get("package", "")
        if pkg and (pkg in SAFE_AUTO_PACKAGES or re.match(r"^[a-zA-Z0-9_\-]+$", pkg)):
            try:
                add_log(f"Self-Heal: Attempting pip install for missing package '{pkg}'...")
                res = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True, text=True, timeout=60
                )
                if res.returncode == 0:
                    add_log(f"Self-Heal: Successfully installed '{pkg}'. Re-running code...")
                    if handler:
                        new_res = handler(args)
                        record_incident(cat, fn_name, err_str, diag["diagnosis"],
                                        f"Auto-installed package '{pkg}' via pip", resolved=True)
                        return True, new_res, f"Installed missing dependency '{pkg}' and completed execution."
                else:
                    add_log(f"Self-Heal: Pip install '{pkg}' failed: {res.stderr[:100]}")
            except Exception as e:
                add_log(f"Self-heal pip install error: {e}")

    # --- Heuristic 5: Transient Network Failure ---
    if cat == "NETWORK_TRANSIENT" and fn_name in ("fetch_url", "web_search"):
        time.sleep(1.5)
        if handler:
            try:
                new_res = handler(args)
                if "error" not in str(new_res).lower() and "fail" not in str(new_res).lower():
                    record_incident(cat, fn_name, err_str, diag["diagnosis"],
                                    "Retried transient network request with backoff", resolved=True)
                    return True, new_res, "Recovered after transient network backoff retry."
            except Exception:
                pass

    # --- Heuristic 6: Locked File Permission ---
    if cat == "PERMISSION_LOCKED":
        time.sleep(1.0)
        if handler:
            try:
                new_res = handler(args)
                if "PermissionError" not in str(new_res) and "WinError 32" not in str(new_res):
                    record_incident(cat, fn_name, err_str, diag["diagnosis"],
                                    "Resolved after releasing file lock delay", resolved=True)
                    return True, new_res, "File access recovered after brief lock clearance."
            except Exception:
                pass

    # Record unresolved incident
    record_incident(cat, fn_name, err_str, diag["diagnosis"], "No automatic remediation succeeded", resolved=False)
    return False, error_or_output, ""


def tool_self_heal_diagnose(error_text: str = "", context: str = "") -> str:
    """Tool function to inspect errors or view self-healing status."""
    if not error_text:
        incidents = get_recent_incidents(5)
        if not incidents:
            return "Self-Healing Engine: Operational. 0 recent incidents logged."
        lines = ["Self-Healing Engine: Recent Operational Incidents:"]
        for inc in incidents:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(inc["timestamp"]))
            status = "RESOLVED" if inc["resolved"] else "ACTIVE"
            lines.append(f"- [{ts}] #{inc['id']} [{inc['category']}] ({status}) Source: {inc['source']}")
            lines.append(f"  Diagnosis: {inc['diagnosis']}")
            lines.append(f"  Action: {inc['action_taken']}")
        return "\n".join(lines)

    diag = diagnose_error("manual_diagnosis", error_text, {"context": context})
    return (
        f"Diagnosis Report:\n"
        f"- Category: {diag['category']}\n"
        f"- Description: {diag['diagnosis']}\n"
        f"- Auto-heal available: {diag['can_auto_heal']}\n"
        f"- Recommended Action: {diag['recommended_action']}"
    )
