"""
ARIA Bounded Workflow Skills (Leon-inspired).
Reusable multi-step procedures with strict step/tool call budgets.
"""

import os
import re
import socket
import threading
from typing import Callable, Any, Dict, Optional
import psutil

class SkillBudgetExceeded(Exception):
    """Raised when a skill exceeds its allocated tool call budget."""
    pass

_SKILL_STATE = threading.local()

def _default_tool_executor(fn_name: str, args: dict) -> str:
    from aria.tools.dispatch import execute_tool
    res, _ = execute_tool(fn_name, args or {}, preauthorized=True)
    return res

def skill_call(fn_name: str, args: dict, executor: Optional[Callable[[str, dict], str]] = None) -> str:
    """Tool dispatcher for skills: enforces the per-skill call budget."""
    rem = getattr(_SKILL_STATE, "remaining", 0)
    if rem <= 0:
        raise SkillBudgetExceeded("skill budget exhausted")
    _SKILL_STATE.remaining = rem - 1
    exec_fn = executor or getattr(_SKILL_STATE, "executor", None) or _default_tool_executor
    return exec_fn(fn_name, args or {})

def skill_deep_research(objective: str, call: Callable) -> str:
    """Bounded web research: up to 4 searches, cited summary, no invention."""
    queries = [
        objective,
        objective + " explained",
        objective + " latest news",
        objective + " details"
    ]
    findings, used = [], []
    for q in queries:
        try:
            r = call("web_search", {"query": q})
        except SkillBudgetExceeded:
            break
        used.append(q)
        if r and "No web results" not in r and "Search error" not in r:
            findings.append(r[:1200])
        if len(findings) >= 3:
            break
    if not findings:
        return ("Research on '%s': no results from %d searches (%s). "
                "Nothing to report - not inventing findings."
                % (objective, len(used), "; ".join(used)))
    return ("Research on '%s' (%d searches: %s):\n\n%s"
            % (objective, len(used), "; ".join(used),
               "\n\n".join(findings)))

def skill_system_check(objective: str, call: Callable) -> str:
    """Laptop health report: CPU, memory, disk, battery, network."""
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.expanduser("~"))
    battery = psutil.sensors_battery()
    try:
        s = socket.create_connection(("8.8.8.8", 53), timeout=5)
        s.close()
        net = "online"
    except Exception:
        net = "OFFLINE"
    bat = f"{battery.percent}%{' (charging)' if battery.power_plugged else ''}" \
        if battery else "no battery sensor"
    return ("System check: CPU %s%%, memory %s%% (%s), disk %s%% free, "
            "battery %s, network %s."
            % (cpu, mem.percent,
               f"{mem.used // 1073741824}G/{mem.total // 1073741824}G used",
               100 - disk.percent, bat, net))

def skill_file_sweep(objective: str, call: Callable) -> str:
    """Scan the workspace: list files, read up to 3 matching the objective."""
    words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", objective)]
    try:
        listing = call("list_workspace", {})
    except SkillBudgetExceeded:
        return "File sweep: budget exhausted before listing."
    read, notes = [], []
    for line in listing.splitlines():
        name = line.strip().lower()
        if name and any(w in name for w in words):
            try:
                content = call("read_file", {"filename": line.strip()})
            except SkillBudgetExceeded:
                break
            read.append(line.strip())
            notes.append(f"--- {line.strip()} ---\n{content[:1500]}")
        if len(read) >= 3:
            break
    if not read:
        return ("File sweep for '%s': no workspace files matched "
                "(checked: %s)." % (objective, listing[:300]))
    return ("File sweep for '%s': read %d file(s) (%s):\n\n%s"
            % (objective, len(read), ", ".join(read), "\n\n".join(notes)))

SKILLS: Dict[str, Dict[str, Any]] = {
    "deep_research": {
        "description": ("Bounded web research on the objective: up to 4 searches, "
                        "returns a cited summary. Never invents findings."),
        "max_calls": 8, "run": skill_deep_research},
    "system_check": {
        "description": ("Quick laptop health report: CPU, memory, disk, battery, "
                        "network. Objective is ignored."),
        "max_calls": 2, "run": skill_system_check},
    "file_sweep": {
        "description": ("Scan the workspace for files matching the objective, "
                        "read up to 3, return digested contents."),
        "max_calls": 5, "run": skill_file_sweep},
}

def tool_run_skill(skill_name: str,
                   objective: str,
                   tool_executor: Optional[Callable[[str, dict], str]] = None,
                   state_callback: Optional[Callable[[str], Any]] = None,
                   log_callback: Optional[Callable[[str], None]] = None) -> str:
    """Run a bounded workflow skill: deep_research, system_check, file_sweep."""
    name = (skill_name or "").strip().lower()
    sk = SKILLS.get(name)
    if not sk:
        return (f"Unknown skill '{skill_name}'. Available: "
                f"{', '.join(sorted(SKILLS))}.")
    if not (objective or "").strip():
        return f"Give me an objective for the '{name}' skill to work on."
    
    _SKILL_STATE.remaining = sk["max_calls"]
    _SKILL_STATE.executor = tool_executor
    
    prev_face = None
    if state_callback:
        prev_face = state_callback("coding")
    try:
        if log_callback:
            log_callback(f"Skill '{name}' started (budget {sk['max_calls']} calls)")
        caller = lambda fn, args: skill_call(fn, args, tool_executor)
        return sk["run"]((objective or "").strip(), caller)
    except SkillBudgetExceeded:
        return (f"Skill '{name}' hit its step budget ({sk['max_calls']} calls) - "
                "stopping with what it found so far.")
    except Exception as e:
        return f"Skill '{name}' failed: {e}"
    finally:
        _SKILL_STATE.remaining = 0
        _SKILL_STATE.executor = None
        if state_callback and prev_face:
            state_callback(prev_face)
