"""Skills v1 — the file-based skill store (data-only).

A skill is a FOLDER under <repo root>/skills/:

    skills/<name>/SKILL.md                 an ACTIVE skill
    skills/<name>/resources/<file>         optional text/data resources
    skills/<name>/scan.json                the gate's verdict (written
                                           ONLY by aria.tools.skill_gate)
    skills/_drafts/<name>/...              proposed, not yet active
    skills/_retired/<name>/...             retired (never offered)
    skills/_drafts/.autolog.json          autonomous-authoring counter

SKILL.md carries a small --- fenced frontmatter (name, description,
budget, origin, and — for autonomous drafts — the evidence that
justified it); the body is the instruction text.

HARD INVARIANT: this module READS TEXT ONLY. Nothing in a skill
folder is ever executed, imported, or eval'd — here, in the gate, or
at runtime. A skill influences ARIA only as prompt text the model
chooses to follow through its normal tools.

Settings (usage.settings()['skills']):
    {'enabled': True, 'auto_activate_in_auto_mode': True,
     'autonomous_authoring': True}
enabled=False restores legacy behaviour exactly: the propose tool
refuses, the prompt catalog is empty, built-in run_skill is all
there is.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from aria import usage
from aria.config import add_log

DEFAULT_BUDGET = 12
HARD_BUDGET_CAP = 20
CATALOG_CAP = 20
MAX_INSTRUCTIONS_BYTES = 16 * 1024
MAX_RESOURCE_BYTES = 64 * 1024
RESOURCE_EXTS = {".md", ".txt", ".json", ".csv"}
AUTONOMOUS_DAILY_CAP = 2

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_RESERVED = {"drafts", "retired"}

# Test seam: when set, skills_root() returns this instead of the
# repo-derived skills/ directory.
_ROOT_OVERRIDE: Optional[str] = None


# --- locations -------------------------------------------------------

def skills_root() -> str:
    if _ROOT_OVERRIDE:
        return _ROOT_OVERRIDE
    env = os.environ.get("ARIA_SKILLS_ROOT")
    if env:
        return env
    # aria/tools/skill_store.py -> aria/tools -> aria -> repo root
    root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "skills")


def drafts_dir() -> str:
    return os.path.join(skills_root(), "_drafts")


def retired_dir() -> str:
    return os.path.join(skills_root(), "_retired")


def _status_dirs() -> List[Tuple[str, str]]:
    """(status, directory) in display order."""
    return [("active", skills_root()),
            ("draft", drafts_dir()),
            ("retired", retired_dir())]


# --- settings ----------------------------------------------------------

def skills_cfg() -> Dict[str, Any]:
    try:
        cfg = usage.settings().get("skills") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def skills_enabled() -> bool:
    return skills_cfg().get("enabled", True) is not False


def autonomous_authoring_enabled() -> bool:
    cfg = skills_cfg()
    return (skills_enabled()
            and cfg.get("autonomous_authoring", True) is not False)


def auto_activate_in_auto_mode() -> bool:
    return skills_cfg().get("auto_activate_in_auto_mode", True) is not False


# --- names / frontmatter ----------------------------------------------

def normalize_name(name: Any) -> Optional[str]:
    """Slugify a proposed skill name; None when it can't be made safe."""
    if not isinstance(name, str):
        return None
    slug = re.sub(r"[\s_]+", "-", name.strip().lower())
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug or slug in _RESERVED or not _NAME_RE.match(slug):
        return None
    return slug


def clamp_budget(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BUDGET
    return max(1, min(HARD_BUDGET_CAP, n))


def parse_skill_md(text: str) -> Tuple[Dict[str, str], str]:
    """Split SKILL.md into (frontmatter dict, instruction body)."""
    meta: Dict[str, str] = {}
    body = text or ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            for line in body[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = body[end + 4:].lstrip("\n")
    return meta, body.strip()


def render_skill_md(meta: Dict[str, Any], body: str) -> str:
    order = ["name", "description", "budget", "origin",
             "evidence_pattern", "evidence_count"]
    lines = ["---"]
    seen = set()
    for key in order:
        if key in meta and meta[key] not in (None, ""):
            lines.append(f"{key}: {meta[key]}")
            seen.add(key)
    for key in sorted(meta):
        if key not in seen and meta[key] not in (None, ""):
            lines.append(f"{key}: {meta[key]}")
    lines.append("---")
    lines.append("")
    lines.append((body or "").strip())
    return "\n".join(lines) + "\n"


# --- loading ------------------------------------------------------------

def load_skill(folder: str, status: str) -> Optional[Dict[str, Any]]:
    """Read one skill folder as data. None when it has no SKILL.md."""
    md_path = os.path.join(folder, "SKILL.md")
    if not os.path.isfile(md_path):
        return None
    try:
        with open(md_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    meta, body = parse_skill_md(text)
    name = meta.get("name") or os.path.basename(folder.rstrip("/\\"))
    resources: Dict[str, str] = {}
    resource_files: List[str] = []
    res_dir = os.path.join(folder, "resources")
    if os.path.isdir(res_dir):
        for fname in sorted(os.listdir(res_dir)):
            fpath = os.path.join(res_dir, fname)
            if not os.path.isfile(fpath):
                continue
            resource_files.append(fname)
            if os.path.splitext(fname)[1].lower() in RESOURCE_EXTS:
                try:
                    with open(fpath, encoding="utf-8",
                              errors="replace") as f:
                        resources[fname] = f.read()
                except OSError:
                    pass
    return {
        "name": name,
        "description": meta.get("description", ""),
        "budget": clamp_budget(meta.get("budget")),
        "origin": meta.get("origin", ""),
        "evidence_pattern": meta.get("evidence_pattern", ""),
        "evidence_count": meta.get("evidence_count", ""),
        "instructions": body,
        "resources": resources,
        "resource_files": resource_files,
        "folder": folder,
        "status": status,
    }


def find_skill(name: str) -> Optional[Dict[str, Any]]:
    for status, base in _status_dirs():
        skill = load_skill(os.path.join(base, name), status)
        if skill is not None:
            return skill
    return None


def list_skills() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for status, base in _status_dirs():
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if entry.startswith("_") or entry.startswith("."):
                continue
            skill = load_skill(os.path.join(base, entry), status)
            if skill is not None:
                out.append(skill)
    return out


def content_hash(skill: Dict[str, Any]) -> str:
    """Hash of everything the gate judges: instructions + resources.
    Activation refuses when the live draft no longer matches the hash
    recorded in its passing scan.json (post-scan edits can't ride a
    stale pass)."""
    h = hashlib.sha256()
    h.update((skill.get("instructions") or "").encode("utf-8"))
    for fname in sorted(skill.get("resources") or {}):
        h.update(b"\x00" + fname.encode("utf-8"))
        h.update(b"\x00" + skill["resources"][fname].encode("utf-8"))
    return h.hexdigest()


# --- scan.json (gate-owned; the store only provides IO) -----------------

def read_scan(folder: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(folder, "scan.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_scan(folder: str, scan: Dict[str, Any]) -> None:
    """Persist a scan verdict. Called ONLY by aria.tools.skill_gate —
    skill content has no path to this function."""
    _write_json_atomic(os.path.join(folder, "scan.json"), scan)


def _write_json_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_text_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- lifecycle -----------------------------------------------------------

def write_draft(name: str, description: str, instructions: str, *,
                budget: Any = None,
                resources: Optional[Dict[str, str]] = None,
                origin: str = "conversation",
                evidence: Optional[Dict[str, Any]] = None,
                ) -> Tuple[bool, str]:
    """Create skills/_drafts/<name>/. Returns (ok, message).
    (False, ...) when an active skill or a draft already owns the
    name — propose re-scans an existing draft instead of clobbering
    it."""
    existing = find_skill(name)
    if existing is not None and existing["status"] == "active":
        return False, (f"a skill named '{name}' is already active; "
                       "retire it before proposing a replacement.")
    if existing is not None and existing["status"] == "draft":
        return False, f"a draft named '{name}' already exists."
    folder = os.path.join(drafts_dir(), name)
    meta: Dict[str, Any] = {
        "name": name,
        "description": (description or "").strip(),
        "budget": clamp_budget(budget),
        "origin": origin,
    }
    if evidence:
        meta["evidence_pattern"] = str(evidence.get("pattern", ""))[:300]
        meta["evidence_count"] = evidence.get("count", "")
    _write_text_atomic(os.path.join(folder, "SKILL.md"),
                       render_skill_md(meta, instructions or ""))
    for fname, content in (resources or {}).items():
        safe = os.path.basename(str(fname))
        if safe:
            _write_text_atomic(
                os.path.join(folder, "resources", safe),
                content if isinstance(content, str) else str(content))
    return True, f"draft '{name}' written."


def activate_draft(name: str) -> Tuple[bool, str]:
    """Move a draft folder into the active store. The CALLER (the
    gate) is responsible for the passing-scan invariant; this is the
    mechanical move only."""
    import shutil
    src = os.path.join(drafts_dir(), name)
    dst = os.path.join(skills_root(), name)
    if not os.path.isdir(src):
        return False, f"no draft named '{name}'."
    if os.path.exists(dst):
        return False, f"an active skill named '{name}' already exists."
    os.makedirs(skills_root(), exist_ok=True)
    shutil.move(src, dst)
    return True, f"skill '{name}' is now active."


def retire_skill(name: str) -> Tuple[bool, str]:
    """Move an active skill (or a draft) into _retired/. Instant and
    logged; a retired skill is never offered in the catalog again."""
    import shutil
    skill = find_skill(name)
    if skill is None:
        return False, f"no skill named '{name}'."
    if skill["status"] == "retired":
        return False, f"skill '{name}' is already retired."
    os.makedirs(retired_dir(), exist_ok=True)
    dst = os.path.join(retired_dir(), name)
    suffix = 2
    while os.path.exists(dst):
        dst = os.path.join(retired_dir(), f"{name}-{suffix}")
        suffix += 1
    shutil.move(skill["folder"], dst)
    add_log(f"Skill retired: '{name}' (was {skill['status']}).")
    return True, f"skill '{name}' retired."


# --- prompt catalog -------------------------------------------------------

_catalog_cache: Dict[str, Any] = {"sig": None, "block": ""}


def _catalog_signature() -> Tuple:
    sig = []
    for _status, base in _status_dirs():
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            entries = []
        for entry in entries:
            if entry.startswith("_") or entry.startswith("."):
                continue
            md = os.path.join(base, entry, "SKILL.md")
            try:
                md_mtime = os.path.getmtime(md)
            except OSError:
                md_mtime = None
            try:
                scan_mtime = os.path.getmtime(
                    os.path.join(base, entry, "scan.json"))
            except OSError:
                scan_mtime = None
            sig.append((base, entry, md_mtime, scan_mtime))
    return tuple(sig)


def scan_is_current(skill: Dict[str, Any]) -> bool:
    """Whether the recorded passing scan still matches the live
    skill: status pass, instructions+resources hash, and (for
    scans recorded after pass 21) the description the scan was
    taken against. One source of truth — the prompt catalog
    (_prompt_eligible), the scan display, and skill_show all
    read this, so a post-activation edit cannot drop out of one
    surface and still load through another (pass 25)."""
    scan = read_scan(skill["folder"])
    if not scan or scan.get("status") != "pass":
        return False
    if scan.get("content_hash") != content_hash(skill):
        return False
    recorded = scan.get("description")
    if recorded is not None and recorded != (skill.get("description")
                                             or ""):
        return False
    return True


def _prompt_eligible(skill: Dict[str, Any]) -> bool:
    """Whether a skill may publish its catalog line into the prompt.

    ACTIVE only, with a CURRENT passing scan (see scan_is_current):
    drafts never qualify, even scan-passed ones still awaiting
    activation approval. A post-activation edit voids eligibility,
    so the skill drops out of the prompt catalog (it stays active;
    retire + re-propose is the recovery path)."""
    if skill["status"] != "active":
        return False
    return scan_is_current(skill)


def catalog_block(limit: int = CATALOG_CAP) -> str:
    """The prompt-assembly catalog: ACTIVE skills with a current
    passing scan, as name + one-liner + status, capped. Drafts
    (failed, unscanned, or scan-passed but unapproved) and retired
    skills never appear here — a draft's description is not prompt
    text. Full instructions are NOT here — they load via
    skill_show only when a skill is used. Empty string when skills
    are disabled or no eligible skill exists (legacy prompts stay
    byte-identical). Cached on folder + scan mtimes."""
    if not skills_enabled():
        return ""
    sig = _catalog_signature()
    if _catalog_cache["sig"] == sig:
        return _catalog_cache["block"]
    skills = [s for s in list_skills() if _prompt_eligible(s)]
    lines: List[str] = []
    if skills:
        lines.append(
            "Active learned skills (file-based workflows; call "
            "skill_show with a skill's name to load its full "
            "instructions before following it; skill_list shows "
            "scan/activation state):")
        for s in skills[:limit]:
            lines.append(
                f"• {s['name']} [{s['status']}]: {s['description']}")
        if len(skills) > limit:
            lines.append(f"• … and {len(skills) - limit} more "
                         "(skill_list to see all)")
    block = "\n".join(lines)
    _catalog_cache["sig"] = sig
    _catalog_cache["block"] = block
    return block


# --- per-turn run budget ---------------------------------------------------
# Mirrors skills.py's SkillBudgetExceeded pattern for file skills:
# skill_show on an ACTIVE skill arms the run; dispatch counts each
# subsequent tool call; exhaustion blocks further calls with a plain
# notice. Management tools (skill_*) never consume budget.

_RUN = threading.local()
_MANAGEMENT_TOOLS = {"skill_propose", "skill_list", "skill_show",
                     "skill_activate", "skill_retire"}


def arm_skill_run(name: str, budget: Any) -> int:
    n = clamp_budget(budget)
    _RUN.name = name
    _RUN.remaining = n
    add_log(f"Skill '{name}' loaded for this turn "
            f"(tool-call budget {n}).")
    return n


def clear_skill_run() -> None:
    _RUN.name = None
    _RUN.remaining = None


def note_skill_tool_call(fn_name: str) -> Optional[str]:
    """Count one tool call against the armed skill's budget.
    Returns a block message when the budget is exhausted, else None.
    Fail-open by design at the dispatch call site."""
    name = getattr(_RUN, "name", None)
    if not name or fn_name in _MANAGEMENT_TOOLS:
        return None
    remaining = getattr(_RUN, "remaining", None)
    if remaining is None:
        return None
    if remaining <= 0:
        return (f"[Skill budget exhausted: '{name}' has used its "
                "tool-call budget for this turn. Stop here and report "
                "what was accomplished.]")
    _RUN.remaining = remaining - 1
    return None


# --- autonomous-authoring daily counter -------------------------------------
# skills/_drafts/.autolog.json, merge-in-place: the file is read,
# only today's counters change, and the result is verified by
# re-reading after the write.

def _autolog_path() -> str:
    return os.path.join(drafts_dir(), ".autolog.json")


def _read_autolog() -> Dict[str, Any]:
    try:
        with open(_autolog_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def autonomous_count_today(now: Optional[datetime] = None) -> int:
    data = _read_autolog()
    today = (now or datetime.now()).date().isoformat()
    if data.get("date") != today:
        return 0
    try:
        return int(data.get("count", 0))
    except (TypeError, ValueError):
        return 0


def record_autonomous_draft(name: str, reason: str,
                            now: Optional[datetime] = None) -> int:
    """Record one autonomous draft against today's cap. Returns the
    verified count for today after the write."""
    now = now or datetime.now()
    today = now.date().isoformat()
    data = _read_autolog()
    if data.get("date") != today:
        data = {"date": today, "count": 0, "entries": []}
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    count += 1
    entries.append({"name": name, "reason": reason[:300],
                    "ts": now.isoformat(timespec="seconds")})
    data["count"] = count
    data["entries"] = entries
    _write_json_atomic(_autolog_path(), data)
    return autonomous_count_today(now)
