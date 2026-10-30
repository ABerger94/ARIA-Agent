"""App enumeration + launch for ARIA OS Shell Mode (2026-10-30).

Pure module — no Qt, no side effects at import. The shell's app
launcher works off Start Menu shortcuts: a ``.lnk`` file's stem IS
the display name a human reads in the Start Menu, and the ``.lnk``
path itself is launchable (``os.startfile`` on a shortcut runs its
target). Tests inject temporary roots full of dummy ``.lnk`` files,
so enumeration never touches the real Start Menu unless a caller
passes the real roots from :func:`default_roots`.

The launch seam is injectable for the same reason: tests pass a
recorder instead of really opening anything.
"""
from __future__ import annotations

import os

#: Verbs the command bar strips before matching ("open chrome").
LAUNCH_VERBS = ("open", "launch", "start", "run")


def default_roots(env=None):
    """The real Start Menu roots on Windows, from the environment.

    Order matters for dedupe: the per-user root first, then the
    all-users root, so a user's own shortcut wins over a machine
    one with the same name. Missing variables are skipped; on a
    non-Windows box this returns whatever the env happens to say
    (usually nothing), never a guess.
    """
    env = os.environ if env is None else env
    roots = []
    appdata = env.get("APPDATA")
    if appdata:
        roots.append(os.path.join(
            appdata, "Microsoft", "Windows", "Start Menu", "Programs"))
    programdata = env.get("PROGRAMDATA") or env.get("ProgramData")
    if programdata:
        roots.append(os.path.join(
            programdata, "Microsoft", "Windows", "Start Menu",
            "Programs"))
    return roots


def _normalize_name(name):
    return " ".join(str(name).strip().lower().split())


def enumerate_apps(roots):
    """Walk *roots* for ``.lnk`` files -> [{name, path}], deduped
    by lowercase display name (first root wins) and sorted by name.

    Unreadable roots are skipped silently — a shell that dies
    because one Start Menu folder is missing is worse than one
    with fewer apps. Entries keep their original-case name.
    """
    seen = {}
    for root in roots or []:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fname in sorted(files):
                if not fname.lower().endswith(".lnk"):
                    continue
                name = fname[:-4].strip()
                if not name:
                    continue
                key = _normalize_name(name)
                if key in seen:
                    continue
                seen[key] = {"name": name,
                             "path": os.path.join(dirpath, fname)}
    return sorted(seen.values(),
                  key=lambda a: _normalize_name(a["name"]))


def _rank(app_name, query):
    """Lower is a better match; None means no match."""
    name = _normalize_name(app_name)
    q = _normalize_name(query)
    if not q:
        return 3
    if name == q:
        return 0
    if name.startswith(q):
        return 1
    if any(word.startswith(q) for word in name.split()):
        return 2
    if q in name:
        return 3
    return None


def search_apps(apps, query, limit=12):
    """Ranked substring search over enumerated apps.

    Ranking: exact name, name-prefix, word-prefix, substring —
    alphabetical inside each tier. An empty query returns the
    first *limit* apps in their (already sorted) order, which is
    what the shell shows before he types anything.
    """
    apps = list(apps or [])
    if not _normalize_name(query or ""):
        return apps[:limit]
    scored = []
    for app in apps:
        rank = _rank(app.get("name", ""), query)
        if rank is not None:
            scored.append((rank, _normalize_name(app.get("name", "")),
                           app))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [app for _r, _n, app in scored[:limit]]


def strip_launch_verb(text):
    """'open Google Chrome' -> 'Google Chrome'; bare text passes
    through. Returns (remainder, had_verb)."""
    words = _normalize_name(text or "").split()
    if words and words[0] in LAUNCH_VERBS and len(words) > 1:
        return " ".join(words[1:]), True
    return " ".join(words), False


def launch_app(entry, opener=None):
    """Launch one enumerated app entry ({name, path}).

    *opener* is the injectable seam: it receives the path and does
    the OS-level open. The default is os.startfile (Windows); off
    Windows it reports failure instead of pretending. Returns
    True when the opener ran without raising.
    """
    path = entry.get("path") if isinstance(entry, dict) else entry
    if not path:
        return False
    if opener is None:
        if not hasattr(os, "startfile"):
            return False

        def opener(p):
            os.startfile(p)  # the user's own Start Menu shortcut
    try:
        opener(path)
        return True
    except Exception:
        return False
