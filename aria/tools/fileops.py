"""
ARIA File Commander tools (Module 2 — ARIA ULTIMATE).
Handlers in builtins style: tool_* functions taking explicit args, returning str.
Imports: stdlib + aria.config only (no dispatch cycles).
"""

import fnmatch
import hashlib
import os
import shutil
import tempfile
import time
from typing import Dict, List, Optional

from aria.config import add_log, redact, MAX_TOOL_OUTPUT

# ---------------- Safety ----------------

# Absolute paths that file commander tools refuse to touch.
# (Kept simple per spec: everything else is allowed.)
def _is_refused(path: str) -> Optional[str]:
    rp = os.path.realpath(path)
    rl = rp.lower().replace("\\", "/")
    if rl == "/" or rl == "/etc" or rl.startswith("/etc/"):
        return f"[Refused: '{path}' resolves to a protected system directory.]"
    if rl == "/usr" or rl.startswith("/usr/"):
        return f"[Refused: '{path}' resolves to a protected system directory.]"
    if rl == "c:/windows" or rl.startswith("c:/windows/"):
        return f"[Refused: '{path}' resolves to a protected system directory.]"
    return None


def _resolve_dir(directory: str) -> "tuple[str, Optional[str]]":
    """Expand and validate a directory. Returns (abs_path, refusal_message_or_None).

    Pass 27 #3: a non-string (or empty) directory used to fall
    back to the process directory (`directory or ""` -> abspath
    of "" is the cwd), so file_organize(directory=false) moved
    files outside the configured roots. Wrong-typed input is
    refused here as well as at the dispatcher.
    """
    if not isinstance(directory, str) or not directory.strip():
        return "", (f"[Error: directory must be a text path, got "
                    f"{type(directory).__name__}.]")
    d = os.path.abspath(os.path.expanduser(directory))
    refused = _is_refused(d)
    if refused:
        return d, refused
    if not os.path.isdir(d):
        return d, f"[Not a directory: {d}]"
    return d, None


def _file_allowed(path: str) -> bool:
    """Per-file project-root check (pass 18, closing the pass-16
    runner-up). The dispatcher validates only the top-level
    `directory` argument (tool_policy.FILE_ARGUMENTS), so a
    symlink inside an allowed directory would otherwise let the
    recursive readers below read and report files outside the
    configured roots. With no roots configured,
    workspaces.file_allowed is always True and this is a no-op —
    legacy behaviour is unchanged; the check bites exactly when
    a roots policy exists. Imported lazily: this module's import
    contract is stdlib + aria.config only."""
    try:
        from aria import workspaces
        return workspaces.file_allowed(path)
    except Exception:
        return False


def _inside(directory: str, path: str) -> bool:
    """True when path's REAL location stays inside directory's
    real location — a symlink hop out (e.g. a category folder
    that is a symlink elsewhere) reads as outside."""
    rd = os.path.realpath(directory)
    rp = os.path.realpath(path)
    return rp == rd or rp.startswith(rd + os.sep)


def _routines_dir() -> str:
    """Canonical routines storage dir: the install root's
    routines/ (aria.data_dir, V20) — must agree with
    aria.routines._routines_dir, its owner."""
    from aria import data_dir
    return os.path.realpath(data_dir.subdir("routines"))


def _is_dotfile(name: str) -> bool:
    return name.startswith(".")


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + f"\n...[truncated, {len(text)} chars total]"
    return text


# ---------------- Module 2a: file_organize ----------------

EXT_MAP: Dict[str, List[str]] = {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff",
               ".tif", ".svg", ".ico", ".heic", ".avif"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt",
                  ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".epub", ".log"],
    "Videos": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m2ts",
               ".ts", ".flv", ".m4v"],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".opus",
              ".wma", ".mid"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"],
    "Code": [".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
             ".xml", ".yml", ".yaml", ".toml", ".sh", ".bat", ".ps1",
             ".c", ".cpp", ".h", ".hpp", ".java", ".rs", ".go", ".sql"],
}
_OTHER = "Other"


def _category_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    for cat, exts in EXT_MAP.items():
        if ext in exts:
            return cat
    return _OTHER


def _unique_dest(dest_dir: str, name: str, reserved=None) -> str:
    """First free destination for `name` in `dest_dir`.

    `reserved` is a set of already-planned destinations (as
    casefolded absolute paths, pass 26 B1#7): a planned move is
    not on disk yet, so existence alone cannot see it — without
    the reservation two sources could plan the same destination
    and the second move would overwrite the first. Casefolded
    because Windows treats A.txt and a.txt as the same file.
    """
    base, ext = os.path.splitext(name)
    cand = os.path.join(dest_dir, name)
    i = 1
    # Case-insensitive on-disk check as well: on Windows A.txt
    # and a.txt are the same file, so os.path.exists alone
    # cannot see the collision there (and this repo is tested
    # on a case-sensitive FS, where the check must still hold).
    try:
        existing = {e.casefold() for e in os.listdir(dest_dir)}
    except OSError:
        existing = set()
    while os.path.exists(cand) or (
            reserved is not None
            and os.path.abspath(cand).casefold() in reserved) or (
            os.path.basename(cand).casefold() in existing):
        cand = os.path.join(dest_dir, f"{base}_{i}{ext}")
        i += 1
    if reserved is not None:
        reserved.add(os.path.abspath(cand).casefold())
    return cand


def _move_atomic(src: str, dest: str) -> None:
    """Move src to dest without ever overwriting an existing dest
    (pass 29 #51). The shipped plan-time reservation + move-time
    re-check left a microsecond window between the `exists` check
    and shutil.move in which a destination created by another
    writer was still overwritten (POSIX rename / shutil.move
    copy overwrites). Same filesystem: os.link claims the
    destination atomically (EEXIST when something is already
    there) and the source link is then removed. Cross-device
    (link raises EXDEV, or a filesystem without hard links):
    claim the name with an exclusive-create placeholder, copy
    the source to a temp file beside dest, and os.replace the
    temp over our own placeholder, then remove the source.
    Raises FileExistsError when the destination is already
    taken — the caller skips the source instead of destroying
    what is there. A failed copy leaves no empty placeholder
    behind."""
    try:
        os.link(src, dest)
    except FileExistsError:
        raise
    except OSError:
        pass  # cross-device or no hard links: copy fallback below
    else:
        os.unlink(src)
        return
    # Cross-device fallback: claim the name first, so no other
    # writer can take it while the copy runs.
    try:
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise
    tmp = None
    replaced = False
    try:
        fd, tmp = tempfile.mkstemp(prefix=".organize-",
                                   dir=os.path.dirname(dest))
        os.close(fd)
        with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        try:
            shutil.copystat(src, tmp)
        except OSError:
            pass
        os.replace(tmp, dest)
        tmp = None
        replaced = True
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if not replaced:
            # The placeholder is still ours and empty: remove it
            # so a failed move leaves no empty dest behind.
            try:
                os.unlink(dest)
            except OSError:
                pass
    os.unlink(src)


def tool_file_organize(directory: str, dry_run: bool = True) -> str:
    """Sort files in a directory into subfolders (Images/Documents/Videos/Audio/Archives/Code/Other).

    dry_run=true (DEFAULT) returns the move plan and touches nothing.
    dry_run=false executes the moves. NOTE: executing is DESTRUCTIVE (files are
    moved) — the approval layer treats dry_run=false as destructive.
    A non-boolean dry_run (null, a string, a list, a number) is
    rejected, never coerced: coercion once turned a preview-classified
    null into a real move. Never touches dotfiles or the routines dir
    (the install root's routines/).
    Non-recursive: only files directly inside the directory are sorted.
    """
    if not isinstance(dry_run, bool):
        return "[Error: file_organize dry_run must be true or false.]"
    d, err = _resolve_dir(directory)
    if err:
        return err
    rd = _routines_dir()
    if d == rd or d.startswith(rd + os.sep):
        return "[Refused: will not organize the routines dir.]"
    add_log(f"file_organize: {redact(d)} (dry_run={dry_run})")

    plan = []  # (src_abs, dest_abs)
    reserved = set()  # planned destinations, casefolded (B1#7)
    skipped = 0
    escaped = 0
    try:
        entries = sorted(os.listdir(d))
    except Exception as e:
        return f"[Error listing directory: {e}]"
    for name in entries:
        src = os.path.join(d, name)
        try:
            if os.path.isdir(src):
                # never descend into or move the routines dir or category dirs
                if _is_dotfile(name) or os.path.realpath(src) == rd:
                    skipped += 1
                continue
            if _is_dotfile(name):
                skipped += 1
                continue
            # ARIA's own control files (settings.json carries
            # safe_mode) must never be moved out of the workspace
            # root — moving settings.json away would switch safe
            # mode off exactly like writing it (pass 27 #2).
            from aria import config as _config
            from aria import workspaces as _workspaces
            if _workspaces.is_reserved_control_file(
                    src, _config.WORKSPACE_DIR):
                skipped += 1
                continue
            if not os.path.isfile(src):
                skipped += 1
                continue
            cat = _category_for(name)
            dest_dir = os.path.join(d, cat)
            if os.path.realpath(src).startswith(rd + os.sep):
                skipped += 1
                continue
            if not _inside(d, dest_dir):
                # The category folder itself is a symlink out of
                # the directory the user approved organizing —
                # moving into it would write outside that
                # directory (pass 18). Skip and say so below.
                skipped += 1
                escaped += 1
                continue
            dest = _unique_dest(dest_dir, name, reserved)
            plan.append((src, dest))
        except Exception:
            skipped += 1

    if not plan:
        note = f" (skipped {skipped})"
        if escaped:
            note += (f" — {escaped} file(s) not moved: their "
                     f"category folder is a symlink out of {d}")
        return f"No sortable files found in {d}.{note}"

    lines = [f"{'PLAN' if dry_run else 'MOVED'} — {d}:"]
    for src, dest in plan:
        rel_src = os.path.relpath(src, d)
        rel_dst = os.path.relpath(dest, d)
        lines.append(f"  {rel_src} -> {rel_dst}")
    if skipped:
        lines.append(f"(skipped {skipped} dotfiles/dirs)")
    if escaped:
        lines.append(
            f"({escaped} file(s) not moved: their category folder "
            f"is a symlink out of this directory)")

    if dry_run:
        lines.append(f"\nDry run only — nothing was moved. Call with dry_run=false to execute ({len(plan)} moves).")
        return _truncate("\n".join(lines))

    # execute
    moved = 0
    errors = []
    for src, dest in plan:
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            # Atomic claim (pass 29 #51): _move_atomic refuses
            # (FileExistsError) when the destination is already
            # taken instead of checking-then-moving — a destination
            # created after planning, even in the microsecond
            # before the move, is never overwritten; the source
            # is skipped instead.
            _move_atomic(src, dest)
            moved += 1
        except FileExistsError:
            errors.append(
                f"{os.path.basename(src)}: skipped, destination "
                f"already exists: {dest}")
        except Exception as e:
            errors.append(f"{os.path.basename(src)}: {e}")
    result = [f"Organized {moved}/{len(plan)} files in {d}."]
    if errors:
        result.append("Errors: " + "; ".join(errors[:5]))
    return _truncate("\n".join(result))


# ---------------- Module 2b: file_find_advanced ----------------

_MAX_CONTENT_FILE_MB = 50
_FIND_CAP = 200


def tool_file_find_advanced(directory: str, pattern: str = "", min_size_mb: float = 0,
                            max_age_days: float = 0, content_contains: str = "") -> str:
    """Recursive filtered find. Filters: fnmatch pattern on filename, min size (MB),
    max age (days since mtime), substring inside file contents (text search).
    Cap: 200 results (count reported when truncated).
    """
    d, err = _resolve_dir(directory)
    if err:
        return err
    add_log(f"file_find_advanced: {redact(d)} pattern={pattern!r}")
    min_size = float(min_size_mb or 0) * 1024 * 1024
    cutoff = time.time() - float(max_age_days or 0) * 86400 if max_age_days else None
    needle = content_contains or ""

    hits = []
    total = 0
    truncated = False
    for root, _dirs, files in os.walk(d):
        for name in files:
            if _is_dotfile(name):
                continue
            full = os.path.join(root, name)
            if not _file_allowed(full):
                continue
            try:
                st = os.stat(full)
                if not st or st.st_size < min_size:
                    continue
                if cutoff is not None and st.st_mtime < cutoff:
                    continue
                if pattern and not fnmatch.fnmatch(name, pattern):
                    continue
                if needle:
                    if st.st_size > _MAX_CONTENT_FILE_MB * 1024 * 1024:
                        continue
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as f:
                            if needle not in f.read():
                                continue
                    except Exception:
                        continue
                total += 1
                if len(hits) < _FIND_CAP:
                    hits.append(full)
                else:
                    truncated = True
            except Exception:
                continue

    lines = [f"Found {total} match(es) in {d}:"]
    for h in hits:
        rel = os.path.relpath(h, d)
        try:
            st = os.stat(h)
            lines.append(f"  {rel}  ({st.st_size / (1024 * 1024):.2f} MB)")
        except Exception:
            lines.append(f"  {rel}")
    if truncated:
        lines.append(f"...[showing first {_FIND_CAP} of {total}]")
    return _truncate("\n".join(lines))


# ---------------- Module 2c: file_duplicates ----------------

_QUICK_HASH_BYTES = 8192


def _sha256_file(path: str, limit_bytes: Optional[int] = None) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            remaining = limit_bytes
            while True:
                chunk = f.read(65536 if remaining is None else min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break
        return h.hexdigest()
    except Exception:
        return None


def tool_file_duplicates(directory: str) -> str:
    """Find duplicate files by SHA-256. Two-stage: quick-hash first 8KB, then full
    hash only on size+quickhash collision candidates. REPORT ONLY — no deletion in v1.
    """
    d, err = _resolve_dir(directory)
    if err:
        return err
    add_log(f"file_duplicates: {redact(d)}")

    by_size: Dict[int, List[str]] = {}
    scanned = 0
    for root, _dirs, files in os.walk(d):
        for name in files:
            if _is_dotfile(name):
                continue
            full = os.path.join(root, name)
            if not _file_allowed(full):
                continue
            try:
                size = os.path.getsize(full)
            except Exception:
                continue
            scanned += 1
            if size == 0:
                continue
            by_size.setdefault(size, []).append(full)

    # stage 1: quick hash on size-collision candidates
    candidates = [p for size, paths in by_size.items() if len(paths) > 1 for p in paths]
    by_quick: Dict["tuple", List[str]] = {}
    for p in candidates:
        qh = _sha256_file(p, _QUICK_HASH_BYTES)
        if qh is None:
            continue
        try:
            size = os.path.getsize(p)
        except Exception:
            continue
        by_quick.setdefault((size, qh), []).append(p)

    # stage 2: full hash on quick-hash collisions
    dup_groups = []
    for paths in by_quick.values():
        if len(paths) < 2:
            continue
        by_full: Dict[str, List[str]] = {}
        for p in paths:
            fh = _sha256_file(p)
            if fh is None:
                continue
            by_full.setdefault(fh, []).append(p)
        for grp in by_full.values():
            if len(grp) > 1:
                dup_groups.append(grp)

    lines = [f"Scanned {scanned} files in {d}."]
    if not dup_groups:
        lines.append("No duplicates found.")
        return "\n".join(lines)
    lines.append(f"{len(dup_groups)} duplicate group(s) — REPORT ONLY, nothing deleted:")
    for i, grp in enumerate(dup_groups, 1):
        try:
            size = os.path.getsize(grp[0])
        except Exception:
            size = -1
        lines.append(f"Group {i} ({len(grp)} files, {size} bytes each):")
        for p in grp:
            lines.append(f"  {os.path.relpath(p, d)}")
    lines.append("v1 is report-only: tell the user before deleting anything.")
    return _truncate("\n".join(lines))


# ---------------- Module 2d: disk_usage ----------------

def tool_disk_usage(directory: str, top_n: int = 20) -> str:
    """Largest files and largest immediate subdirectories under a directory."""
    d, err = _resolve_dir(directory)
    if err:
        return err
    add_log(f"disk_usage: {redact(d)}")
    try:
        top_n = max(1, min(int(top_n), 100))
    except Exception:
        top_n = 20

    file_sizes: List["tuple[int, str]"] = []
    dir_sizes: Dict[str, int] = {}
    total = 0
    count = 0
    for root, dirs, files in os.walk(d):
        # prune dot dirs for speed
        dirs[:] = [x for x in dirs if not _is_dotfile(x)]
        for name in files:
            if _is_dotfile(name):
                continue
            full = os.path.join(root, name)
            if not _file_allowed(full):
                continue
            try:
                size = os.path.getsize(full)
            except Exception:
                continue
            count += 1
            total += size
            file_sizes.append((size, full))
            # attribute to immediate subdir of d (or d itself for top-level files)
            rel = os.path.relpath(root, d)
            key = rel.split(os.sep)[0] if rel != "." else "<top>"
            dir_sizes[key] = dir_sizes.get(key, 0) + size

    def fmt(b: int) -> str:
        for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
            if b >= div:
                return f"{b / div:.1f} {unit}"
        return f"{b} B"

    lines = [f"Disk usage of {d}: {fmt(total)} across {count} files."]
    lines.append(f"Top {top_n} files:")
    for size, full in sorted(file_sizes, reverse=True)[:top_n]:
        lines.append(f"  {fmt(size):>10}  {os.path.relpath(full, d)}")
    lines.append("Subdirectory totals:")
    for key, size in sorted(dir_sizes.items(), key=lambda kv: kv[1], reverse=True)[:top_n]:
        lines.append(f"  {fmt(size):>10}  {key}")
    return _truncate("\n".join(lines))
