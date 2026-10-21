"""
ARIA self-editing tools (2026-10-21 fix package).

The brain's restart watcher (aria/agent/brain.py) hashes every
aria/**/*.py file, the aria.py launcher, and SOUL at boot; after
each turn it re-checks them, and a changed file is compiled,
smoke-imported, announced ("My code changed — restarting now"),
and either loaded via restart or restored from git when broken.
But until now NO tool could write those files: write_file is
sandboxed to the workspace directory, so when ARIA "edited her
code" she wrote a copy into the workspace, the watcher saw no
change, and nothing was announced and nothing restarted — the
exact symptom in the 2026-10-21 session report.

These two tools are the missing writer, allowlisted to EXACTLY
the watched set:

  * *.py files inside the repo's aria/ package;
  * the root aria.py launcher;
  * the SOUL file the brain watcher tracks (aria.config.SOUL_PATH
    — the same path brain.py hashes, not a hardcoded guess).

Everything else is refused: traversal, absolute paths outside the
repo, non-.py targets other than SOUL, repo-root files other than
the launcher. Writes are atomic-ish (temp file + os.replace in
the same directory). The tools deliberately do NOT restart or
validate anything themselves — the post-turn watcher owns that,
and the return text says so, so the brain can tell the user what
happens next.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from aria import config as _config
from aria.config import add_log

_RESTART_NOTE = (
    " The post-turn restart check will validate the change, "
    "announce it, and restart me to load it — or restore the last "
    "committed version from git if the new code is broken.")


def _repo_root() -> str:
    return _config.ROOT_DIR


def _resolve_target(path: str) -> Tuple[Optional[str], Optional[str]]:
    """(absolute_path, None) when the target is allowlisted, else
    (None, refusal_reason). Relative paths resolve against the
    repo root (config.ROOT_DIR — the parent of the aria/ package)."""
    raw = (path or "").strip()
    if not raw:
        return None, "no path given"
    repo = os.path.realpath(_repo_root())
    norm = raw.replace("\\", "/")
    if os.path.isabs(raw) or (len(norm) > 1 and norm[1] == ":"):
        cand = os.path.realpath(raw)
    else:
        cand = os.path.realpath(os.path.join(repo, *norm.split("/")))
    if os.path.commonpath([cand, repo]) != repo:
        return None, f"'{path}' resolves outside my source tree"
    if cand == os.path.realpath(_config.SOUL_PATH):
        return cand, None
    if cand == os.path.join(repo, "aria.py"):
        return cand, None
    pkg = os.path.join(repo, "aria")
    if os.path.commonpath([cand, pkg]) == pkg and cand.endswith(".py"):
        return cand, None
    return None, (f"'{path}' is not one of my editable files — I can "
                  f"only edit .py files inside the aria/ package, "
                  f"the aria.py launcher, or soul.md")


def _atomic_write(path: str, content: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp-selfedit"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def _rel(path: str) -> str:
    return os.path.relpath(path, _repo_root())


def tool_edit_own_code(path: str, content: str) -> str:
    """Rewrite one of ARIA's own source files in full."""
    target, refusal = _resolve_target(path)
    if refusal:
        return f"[Self-edit refused: {refusal}.]"
    try:
        _atomic_write(target, content or "")
    except Exception as e:
        return f"[Self-edit failed: {type(e).__name__}: {e}]"
    add_log(f"Self-edit: rewrote {_rel(target)} "
            f"({len(content or '')} chars)")
    return (f"Rewrote {_rel(target)} "
            f"({len(content or '')} characters)." + _RESTART_NOTE)


def tool_patch_own_code(path: str, old_text: str, new_text: str) -> str:
    """Replace exactly one exact-match occurrence in one of ARIA's
    own source files. Refuses when old_text is absent or matches
    more than one place — a partial or ambiguous self-patch is how
    a working file becomes a broken one."""
    target, refusal = _resolve_target(path)
    if refusal:
        return f"[Self-edit refused: {refusal}.]"
    if not old_text:
        return "[Self-edit failed: old_text is empty — nothing to match.]"
    try:
        with open(target, "r", encoding="utf-8") as f:
            current = f.read()
    except FileNotFoundError:
        return (f"[Self-edit failed: {_rel(target)} does not exist — "
                f"use edit_own_code to create it.]")
    except Exception as e:
        return f"[Self-edit failed: {type(e).__name__}: {e}]"
    n = current.count(old_text)
    if n == 0:
        return ("[Self-edit failed: old_text not found in "
                f"{_rel(target)} — no change made.]")
    if n > 1:
        return (f"[Self-edit failed: old_text matches {n} places in "
                f"{_rel(target)} — include more surrounding context "
                f"so it matches exactly one. No change made.]")
    try:
        _atomic_write(target, current.replace(old_text, new_text, 1))
    except Exception as e:
        return f"[Self-edit failed: {type(e).__name__}: {e}]"
    add_log(f"Self-edit: patched {_rel(target)} "
            f"({len(old_text)} -> {len(new_text or '')} chars)")
    return (f"Patched {_rel(target)}: replaced {len(old_text)} "
            f"characters with {len(new_text or '')}." + _RESTART_NOTE)
