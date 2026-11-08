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
the launcher. Writes are atomic (a unique temp file + os.replace in the same
directory, pass 29 #49 — the old fixed `.tmp-selfedit` name was
shared by every concurrent writer of the same target, so two
writers interleaved content in one temp file and the loser got
FileNotFoundError on its own replace). Each target also has a
per-target lock held across read-validate-write, and the file is
re-read just before writing: a patch/edit prepared against a
revision that changed meanwhile is refused as a stale-edit
conflict instead of silently overwriting the newer content.
The tools deliberately do NOT restart anything themselves — the
post-turn watcher owns that, and the return text says so, so
the brain can tell the user what happens next.

Pre-write validation (2026-10-27 hardening — Alek asked ARIA to
change her pet's name colour herself; the edit corrupted a file
and a recovery had to restore it, on a one-line change): the
watcher was the ONLY net, and it only catches files that fail to
compile or import — a truncated rewrite that still parses, or
one that quietly drops a function other code calls, sails
through it and the corruption is discovered by the user. So the
tools now refuse a bad write BEFORE the file on disk changes:

  * the new source must compile;
  * it must not DROP any top-level function or class the file
    had before (the lost-helper class of corruption);
  * a full rewrite may not shrink a substantial file below 60%
    of its current size — the truncated-regeneration class, and
    the refusal steers to patch_own_code, which is the right
    tool for almost every real change anyway.
  * a NEW .py file must compile too (pass 29 #48): validation
    used to be gated on the target already existing, so a new
    file with invalid syntax was written with a success
    message — and the watcher cannot restore an untracked file
    from git. The shrink/dropped-definition checks still apply
    to existing files only (a new file has nothing to shrink
    or drop).

The watcher remains as the second net (import smoke + git
restore), unchanged.
"""

from __future__ import annotations

import ast
import os
import tempfile
import threading
from typing import Optional, Tuple

from aria import config as _config
from aria.config import add_log
from aria.outcome import legacy_error_results

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
    """Write content to path via a UNIQUE temp file in the same
    directory + os.replace (pass 29 #49). The old fixed
    `.tmp-selfedit` name was shared by every concurrent writer
    of the same target: two writers interleaved content in one
    temp file, and the writer whose temp had already been
    replaced away got FileNotFoundError on its own replace.
    mkstemp gives each writer its own temp, so a concurrent
    write can only ever replace a complete file."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".selfedit-", dir=parent or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


# Per-target locks (pass 29 #49): one threading.Lock per
# resolved target path, held across a whole read-validate-write
# so two in-process writers of the same file serialize instead
# of interleaving. Keyed by the resolved (real) path, which is
# what _resolve_target returns.
_TARGET_LOCKS: dict = {}
_TARGET_LOCKS_GUARD = threading.Lock()


def _target_lock(path: str) -> threading.Lock:
    with _TARGET_LOCKS_GUARD:
        lock = _TARGET_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _TARGET_LOCKS[path] = lock
        return lock


def _stale_conflict(target: str, what: str) -> str:
    """Refusal text when the file changed between being read and
    being written (pass 29 #49): the change was prepared against
    a different source revision, so applying it would silently
    discard the newer content. Nothing was written."""
    return (f"[Self-edit failed: {_rel(target)} changed while this "
            f"{what} was being prepared — refusing to apply it "
            f"against a stale revision. No change made.]")


def _rel(path: str) -> str:
    return os.path.relpath(path, _repo_root())


def _top_level_names(source: str) -> Optional[set]:
    """Top-level function/class names in a source string, or
    None when it does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return {node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef))}


def _validate_new_source(target: str, current: str, new: str) -> Optional[str]:
    """Refusal text when writing `new` over `current` at a .py
    target would corrupt the file, else None. The two checks
    that catch what the restart watcher's net cannot: the new
    source must compile, and it must keep every top-level
    function/class the file had (other modules call those)."""
    try:
        compile(new, target, "exec")
    except SyntaxError as e:
        return (f"the new content does not compile ({e.msg} at "
                f"line {e.lineno}) — the file was NOT changed. Fix "
                f"the content and try again, or use patch_own_code "
                f"for a smaller, exact change")
    old_names = _top_level_names(current)
    new_names = _top_level_names(new)
    if old_names is not None and new_names is not None:
        dropped = sorted(old_names - new_names)
        if dropped:
            return ("the new content DROPS top-level definitions "
                    "the file had before: " + ", ".join(dropped)
                    + " — other code may call those, so the file "
                    "was NOT changed. Keep every existing function "
                    "and class, or use patch_own_code for a "
                    "smaller, exact change")
    return None


def _read_current(target: str) -> Optional[str]:
    try:
        with open(target, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return None


@legacy_error_results('[Self-edit refused:', '[Self-edit failed:')
def tool_edit_own_code(path: str, content: str) -> str:
    """Rewrite one of ARIA's own source files in full."""
    target, refusal = _resolve_target(path)
    if refusal:
        return f"[Self-edit refused: {refusal}.]"
    content = content or ""
    # The whole read-validate-write runs under the target's lock
    # (pass 29 #49): two in-process writers of the same file
    # serialize instead of interleaving, and the re-read just
    # before writing catches a change that landed while
    # validation ran.
    with _target_lock(target):
        current = _read_current(target)
        if target.endswith(".py"):
            if current is None:
                # New file (pass 29 #48): there is nothing to shrink
                # or drop, but the proposed source must still compile
                # — _validate_new_source against an empty current
                # runs exactly the compile check and nothing else.
                problem = _validate_new_source(target, "", content)
                if problem:
                    return f"[Self-edit refused: {problem}.]"
            else:
                # The truncated-regeneration guard: a "rewrite" that
                # comes back far smaller than the file it replaces
                # is almost never a deliberate rewrite — it is a
                # generation that trailed off, and writing it would
                # corrupt the file. Steer to the patch tool.
                if len(current) > 1500 and len(content) < 0.6 * len(current):
                    return ("[Self-edit refused: the new content is "
                            f"{len(content)} characters against the "
                            f"file's current {len(current)} — a full "
                            "rewrite that much smaller would gut the "
                            "file. For a change smaller than the whole "
                            "file use patch_own_code with exact "
                            "old_text/new_text instead. The file was "
                            "NOT changed.]")
                problem = _validate_new_source(target, current, content)
                if problem:
                    return f"[Self-edit refused: {problem}.]"
        if _read_current(target) != current:
            return _stale_conflict(target, "edit")
        try:
            _atomic_write(target, content)
        except Exception as e:
            return f"[Self-edit failed: {type(e).__name__}: {e}]"
    add_log(f"Self-edit: rewrote {_rel(target)} "
            f"({len(content or '')} chars)")
    return (f"Rewrote {_rel(target)} "
            f"({len(content or '')} characters)." + _RESTART_NOTE)


@legacy_error_results('[Self-edit refused:', '[Self-edit failed:')
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
    # Read-validate-write under the target's lock (pass 29 #49),
    # with a re-read just before writing: a patch prepared
    # against a revision that changed meanwhile is refused as a
    # stale-edit conflict instead of silently overwriting the
    # newer content.
    with _target_lock(target):
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
        new_content = current.replace(old_text, new_text, 1)
        if target.endswith(".py"):
            problem = _validate_new_source(target, current, new_content)
            if problem:
                return f"[Self-edit refused: {problem}.]"
        if _read_current(target) != current:
            return _stale_conflict(target, "patch")
        try:
            _atomic_write(target, new_content)
        except Exception as e:
            return f"[Self-edit failed: {type(e).__name__}: {e}]"
    add_log(f"Self-edit: patched {_rel(target)} "
            f"({len(old_text)} -> {len(new_text or '')} chars)")
    return (f"Patched {_rel(target)}: replaced {len(old_text)} "
            f"characters with {len(new_text or '')}." + _RESTART_NOTE)
