"""
ARIA Persistent Autonomous Workers & Event Supervisor.
Runs background workers for:
1. Long-running asynchronous background jobs & process tracking.
2. Real-time user Downloads folder watcher (notifies when downloads complete).
3. System resource, memory, disk space, and performance anomaly monitoring.
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import psutil

from aria.config import WORKSPACE_DIR, ROOT_DIR, add_log
from aria.memory import (
    job_db_create, job_db_update, job_db_list, spine_append
)

_JOBS_LOG_DIR = os.path.join(WORKSPACE_DIR, "logs", "jobs")
os.makedirs(_JOBS_LOG_DIR, exist_ok=True)

_ACTIVE_JOBS: Dict[int, Tuple[subprocess.Popen, str, str, float]] = {}  # job_id -> (proc, log_file, name, start_time)
_JOBS_LOCK = threading.Lock()

_PENDING_DOWNLOADS: List[Dict[str, Any]] = []
_PENDING_SYSTEM_ALERTS: List[str] = []
_PENDING_JOB_NOTICES: List[str] = []
_QUEUES_LOCK = threading.Lock()

_WORKERS_STARTED = False


def _fmt_size(num_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


# --- Background Job Management ---

def start_background_job(command: str, name: str = "") -> Dict[str, Any]:
    """Spawn an asynchronous background job and track execution in worker supervisor."""
    job_name = name or (command[:40] + ("..." if len(command) > 40 else ""))
    log_file = os.path.join(_JOBS_LOG_DIR, f"job_{int(time.time() * 1000)}.log")
    
    try:
        log_fp = open(log_file, "w", encoding="utf-8", errors="ignore")
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            cwd=WORKSPACE_DIR
        )
        jid = job_db_create(job_name, command, proc.pid, log_file)
        
        with _JOBS_LOCK:
            _ACTIVE_JOBS[jid] = (proc, log_file, job_name, time.time())
            
        spine_append("job_started", {"job_id": jid, "name": job_name, "pid": proc.pid})
        add_log(f"Worker: Started job #{jid} '{job_name}' (PID: {proc.pid})")
        return {
            "job_id": jid,
            "pid": proc.pid,
            "name": job_name,
            "status": "running",
            "log_file": log_file
        }
    except Exception as e:
        add_log(f"Worker: Failed to start background job: {e}")
        return {"error": str(e)}


def list_background_jobs(limit: int = 10) -> List[Dict[str, Any]]:
    """Return list of background jobs and their current runtime statuses."""
    jobs = job_db_list(limit)
    with _JOBS_LOCK:
        for j in jobs:
            jid = j["id"]
            if jid in _ACTIVE_JOBS:
                proc, _, _, _ = _ACTIVE_JOBS[jid]
                if proc.poll() is None:
                    j["status"] = "running"
    return jobs


def cancel_background_job(job_id: int) -> bool:
    """Terminate or kill a running background job."""
    with _JOBS_LOCK:
        if job_id in _ACTIVE_JOBS:
            proc, log_file, name, _ = _ACTIVE_JOBS[job_id]
            try:
                proc.terminate()
                time.sleep(0.2)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            job_db_update(job_id, "killed", exit_code=-9)
            del _ACTIVE_JOBS[job_id]
            add_log(f"Worker: Cancelled job #{job_id} '{name}'")
            return True

    # If not in active memory, check DB
    jobs = job_db_list(20)
    for j in jobs:
        if j["id"] == job_id and j["status"] == "running":
            pid = j["pid"]
            try:
                p = psutil.Process(pid)
                p.terminate()
            except Exception:
                pass
            job_db_update(job_id, "killed", exit_code=-9)
            return True
    return False


def get_background_job_log(job_id: int, tail_lines: int = 40) -> str:
    """Read the stdout/stderr log output for a given job."""
    jobs = job_db_list(50)
    target_job = next((j for j in jobs if j["id"] == job_id), None)
    if not target_job or not target_job.get("log_file"):
        return f"Job #{job_id} not found."

    log_path = target_job["log_file"]
    if not os.path.exists(log_path):
        return f"Log file for job #{job_id} does not exist."

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        tail = "".join(lines[-tail_lines:]) if lines else "(no output captured yet)"
        return f"Job #{job_id} ({target_job.get('name')}) Status: {target_job.get('status')}\n---\n{tail}"
    except Exception as e:
        return f"Failed to read job log: {e}"


def _job_supervisor_loop():
    """Background supervisor monitoring running child processes."""
    while True:
        time.sleep(2)
        finished_jids = []
        with _JOBS_LOCK:
            for jid, (proc, log_file, name, start_t) in list(_ACTIVE_JOBS.items()):
                code = proc.poll()
                if code is not None:
                    finished_jids.append(jid)
                    dur = round(time.time() - start_t, 1)
                    status = "completed" if code == 0 else "failed"
                    job_db_update(jid, status, exit_code=code)
                    spine_append("job_complete", {
                        "job_id": jid, "name": name, "exit_code": code, "duration_s": dur
                    })
                    msg = f"Job #{jid} '{name}' {status} with code {code} ({dur}s)."
                    add_log(f"Worker: {msg}")
                    with _QUEUES_LOCK:
                        _PENDING_JOB_NOTICES.append(msg)

            for jid in finished_jids:
                if jid in _ACTIVE_JOBS:
                    del _ACTIVE_JOBS[jid]


# --- Downloads Folder Watcher ---

def _downloads_watcher_loop():
    """Monitors the user Downloads directory for newly completed files."""
    dl_path = Path.home() / "Downloads"
    if not dl_path.exists():
        return

    # Track seen files: path -> (size, mtime, settled_count)
    seen: Dict[str, Tuple[int, float, int]] = {}
    
    # Initialize initial state so existing files aren't treated as new downloads
    try:
        for entry in dl_path.iterdir():
            if entry.is_file():
                try:
                    stat = entry.stat()
                    seen[str(entry)] = (stat.st_size, stat.st_mtime, 2)
                except Exception:
                    pass
    except Exception:
        pass

    TEMP_EXTS = {".tmp", ".crdownload", ".part", ".opdownload", ".aria2", ".download"}

    while True:
        time.sleep(5)
        try:
            current_entries = list(dl_path.iterdir())
        except Exception:
            continue

        for entry in current_entries:
            if not entry.is_file():
                continue
            name = entry.name
            if name.startswith("~$") or any(name.lower().endswith(ext) for ext in TEMP_EXTS):
                continue

            entry_str = str(entry)
            try:
                stat = entry.stat()
                size = stat.st_size
                mtime = stat.st_mtime
            except Exception:
                continue

            if entry_str not in seen:
                # First time seeing file
                seen[entry_str] = (size, mtime, 0)
            else:
                prev_size, prev_mtime, count = seen[entry_str]
                if count < 2:
                    if prev_size == size and prev_mtime == mtime:
                        count += 1
                        seen[entry_str] = (size, mtime, count)
                        if count >= 2:
                            # File has settled — download complete!
                            spine_append("download_complete", {
                                "name": name,
                                "size": size,
                                "path": entry_str
                            })
                            summary = f"Download complete: '{name}' ({_fmt_size(size)})"
                            add_log(f"Worker: {summary}")
                            with _QUEUES_LOCK:
                                _PENDING_DOWNLOADS.append({
                                    "name": name,
                                    "path": entry_str,
                                    "size": size,
                                    "summary": summary
                                })
                    else:
                        seen[entry_str] = (size, mtime, 0)


# --- System Resource & Health Monitor ---

_LAST_ALERT_TIMES: Dict[str, float] = {}

def _system_resource_loop():
    """Periodically checks CPU, RAM, and Disk space for critical thresholds."""
    while True:
        time.sleep(45)
        now = time.time()
        try:
            # 1. Disk space check
            for part in psutil.disk_partitions():
                if not part.mountpoint:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    free_gb = usage.free / (1024 ** 3)
                    if free_gb < 5.0:
                        key = f"disk_{part.mountpoint}"
                        if now - _LAST_ALERT_TIMES.get(key, 0) > 1800:
                            _LAST_ALERT_TIMES[key] = now
                            alert = f"Low disk space on {part.mountpoint}: {free_gb:.1f} GB free remaining."
                            add_log(f"Worker Warning: {alert}")
                            spine_append("system_alert", {"type": "disk", "target": part.mountpoint, "free_gb": free_gb})
                            with _QUEUES_LOCK:
                                _PENDING_SYSTEM_ALERTS.append(alert)
                except Exception:
                    pass

            # 2. RAM check
            mem = psutil.virtual_memory()
            if mem.percent > 94.0:
                key = "ram_high"
                if now - _LAST_ALERT_TIMES.get(key, 0) > 1800:
                    _LAST_ALERT_TIMES[key] = now
                    alert = f"High RAM usage: {mem.percent}% utilized ({mem.available / (1024**3):.1f} GB available)."
                    add_log(f"Worker Warning: {alert}")
                    spine_append("system_alert", {"type": "ram", "percent": mem.percent})
                    with _QUEUES_LOCK:
                        _PENDING_SYSTEM_ALERTS.append(alert)

        except Exception as e:
            add_log(f"Worker resource loop error: {e}")


# --- Notification Handlers for Heartbeat / Prompt Integration ---

def pop_pending_downloads() -> List[Dict[str, Any]]:
    """Retrieve and clear pending download completion events."""
    with _QUEUES_LOCK:
        items = list(_PENDING_DOWNLOADS)
        _PENDING_DOWNLOADS.clear()
        return items


def pop_system_alerts() -> List[str]:
    """Retrieve and clear pending system resource alerts."""
    with _QUEUES_LOCK:
        items = list(_PENDING_SYSTEM_ALERTS)
        _PENDING_SYSTEM_ALERTS.clear()
        return items


def pop_job_notifications() -> List[str]:
    """Retrieve and clear pending job completion notices."""
    with _QUEUES_LOCK:
        items = list(_PENDING_JOB_NOTICES)
        _PENDING_JOB_NOTICES.clear()
        return items


def system_health_audit() -> str:
    """Generate comprehensive audit of system resources, workers, and background jobs."""
    lines = ["System Health & Autonomous Workers Audit:"]
    
    # 1. CPU & Memory
    cpu = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()
    lines.append(f"- CPU Usage: {cpu}%")
    lines.append(f"- RAM: {mem.percent}% used ({mem.available / (1024**3):.1f} GB / {mem.total / (1024**3):.1f} GB available)")
    
    # 2. Storage
    lines.append("- Storage Disks:")
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            lines.append(f"  * {part.mountpoint} ({part.device}): {u.percent}% used ({u.free / (1024**3):.1f} GB free / {u.total / (1024**3):.1f} GB total)")
        except Exception:
            pass

    # 3. Active Background Jobs
    with _JOBS_LOCK:
        active_count = len(_ACTIVE_JOBS)
    lines.append(f"- Background Jobs Active: {active_count}")
    recent_jobs = list_background_jobs(5)
    if recent_jobs:
        for j in recent_jobs[:3]:
            lines.append(f"  * #{j['id']} '{j['name']}': status={j['status']}, PID={j.get('pid')}")

    # 4. Watchers
    lines.append("- Persistent Watchers: Downloads Watcher (Active), Resource Anomaly Watcher (Active)")
    return "\n".join(lines)


def start_all_workers():
    """Start all autonomous background worker threads."""
    global _WORKERS_STARTED
    if _WORKERS_STARTED:
        return
    _WORKERS_STARTED = True
    
    threading.Thread(target=_job_supervisor_loop, daemon=True, name="AriaJobSupervisor").start()
    threading.Thread(target=_downloads_watcher_loop, daemon=True, name="AriaDownloadsWatcher").start()
    threading.Thread(target=_system_resource_loop, daemon=True, name="AriaResourceMonitor").start()
    add_log("Persistent Autonomous Workers initialized.")
