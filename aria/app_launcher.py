"""App enumeration + launch for ARIA OS Shell Mode (2026-10-30).

Pure module — no Qt, no side effects at import. The shell's app
launcher works off Start Menu shortcuts: a ``.lnk`` file's stem IS
the display name a human reads in the Start Menu, and the ``.lnk``
path itself is launchable (``os.startfile`` on a shortcut runs its
target). Tests inject temporary roots full of dummy ``.lnk`` files,
so enumeration never touches the real Start Menu unless a caller
passes the real roots from :func:`default_roots`.

V17 (2026-10-31, Alek's order — "some apps like Spotify aren't
showing"): Store / per-user apps installed without a Start Menu
shortcut never appear in the ``.lnk`` walk. A second source covers
them: the shell's AppsFolder, enumerated through PowerShell's
``Get-StartApps`` (Name + AppID). AppsFolder entries carry an
``appid`` instead of a ``path``; they launch through
``shell:AppsFolder\\<AppID>`` on the same opener seam, and their
pin identity is ``appid:<AppID>`` (see :func:`app_key`). The
PowerShell call is fail-soft — any failure reads as "no
AppsFolder apps", exactly the pre-V17 ``.lnk``-only behaviour,
with no error surface. Tests inject a fake runner, so nothing
real is shelled out.

The launch seam is injectable for the same reason: tests pass a
recorder instead of really opening anything.

V18 (2026-10-31, Alek's ask — "is there anyway to get the app
symbol/icon for things like Spotify? Instead of just a
letter."): AppsFolder entries also gain a real icon where one
can be resolved from the installed package: the AppID's
package family name locates the package (one fail-soft
``Get-AppxPackage`` call, cached for the process), the
package's AppxManifest.xml names its Square44x44Logo asset,
and the largest on-disk variant of that asset becomes the
entry's ``icon`` path, which the shell panel loads like any
image file. Every step fails soft to the letter tile the
shell already draws — an unresolvable icon changes nothing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET

#: Verbs the command bar strips before matching ("open chrome").
LAUNCH_VERBS = ("open", "launch", "start", "run")

#: AppsFolder launch URI prefix: os.startfile on
#: "shell:AppsFolder\<AppID>" opens the app, the documented
#: shell-namespace route for Store / per-user apps.
APPSFOLDER_URI = "shell:AppsFolder\\"

#: Pin-identity prefix for AppsFolder entries: their key in the
#: ordered ui_prefs pin list (and the pin_groups map) is
#: "appid:<AppID>", where a .lnk entry's key is its path.
APPID_KEY_PREFIX = "appid:"


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


def app_kind(entry):
    """'appsfolder' for a Get-StartApps entry (it carries an
    ``appid``), 'lnk' for a Start Menu shortcut entry."""
    if isinstance(entry, dict) and str(entry.get("appid") or ""):
        return "appsfolder"
    return "lnk"


def app_key(entry):
    """The entry's pin identity: ``appid:<AppID>`` for an
    AppsFolder app, the ``.lnk`` path for a shortcut, '' for
    anything without a launchable identity. Pin storage
    (ui_prefs) holds these keys; for .lnk apps the key IS the
    path, so every pre-V17 pin resolves unchanged."""
    if not isinstance(entry, dict):
        return ""
    if app_kind(entry) == "appsfolder":
        return APPID_KEY_PREFIX + str(entry.get("appid")).strip()
    return str(entry.get("path") or "").strip()


def launch_target(entry):
    """What the opener receives for *entry*: the .lnk path for
    a shortcut, ``shell:AppsFolder\\<AppID>`` for an AppsFolder
    app, '' when the entry has no launchable identity."""
    if not isinstance(entry, dict):
        return ""
    if app_kind(entry) == "appsfolder":
        appid = str(entry.get("appid") or "").strip()
        return (APPSFOLDER_URI + appid) if appid else ""
    return str(entry.get("path") or "").strip()


def _get_start_apps_stdout(timeout=20):
    """Run Get-StartApps and return its JSON stdout. The one
    real side effect in this module's AppsFolder path, isolated
    so tests inject a fake runner instead. Raises on any
    failure — the caller (enumerate_appsfolder) fails soft."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive",
           "-Command",
           "Get-StartApps | ConvertTo-Json -Compress"]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NO_WINDOW", 0)
    out = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=timeout, **kwargs)
    if out.returncode != 0:
        raise RuntimeError(
            f"Get-StartApps exited {out.returncode}")
    return out.stdout or ""


def enumerate_appsfolder(runner=None, timeout=20):
    """AppsFolder apps via Get-StartApps -> [{name, appid}],
    deduped by lowercase display name and sorted by name.

    *runner* is the injectable seam: a callable returning the
    command's stdout text. FAIL-SOFT by design — a missing
    PowerShell, a timeout, junk output, anything at all reads
    as [] (the .lnk-only world), never an exception, never an
    error surface in the shell."""
    if runner is None:
        def runner():
            return _get_start_apps_stdout(timeout)
    try:
        raw = json.loads(runner() or "")
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    seen = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        appid = str(item.get("AppID") or "").strip()
        if not name or not appid:
            continue
        key = _normalize_name(name)
        if key not in seen:
            seen[key] = {"name": name, "appid": appid}
    return sorted(seen.values(),
                  key=lambda a: _normalize_name(a["name"]))


def merge_app_sources(lnk_apps, folder_apps):
    """One app list from both enumeration sources, deduped by
    lowercase display name. The .lnk entry WINS a name tie: it
    carries the real shortcut (and with it the real icon),
    while the AppsFolder twin would render as a letter tile.
    Sorted by name, like each source."""
    seen = {}
    for app in list(lnk_apps or []) + list(folder_apps or []):
        if not isinstance(app, dict):
            continue
        key = _normalize_name(app.get("name", ""))
        if key and key not in seen:
            seen[key] = app
    return sorted(seen.values(),
                  key=lambda a: _normalize_name(a["name"]))


def enumerate_all_apps(roots, appsfolder_runner=None,
                       appx_runner=None):
    """Every app the shell can show: Start Menu .lnk files
    merged with AppsFolder entries (the dashboard's provider
    calls this; the panel never knows two sources exist).
    AppsFolder entries then gain a resolved package icon where
    one exists (V18, see attach_appsfolder_icons)."""
    return attach_appsfolder_icons(
        merge_app_sources(
            enumerate_apps(roots),
            enumerate_appsfolder(runner=appsfolder_runner)),
        runner=appx_runner)


# ------------------------------------------------------------------
# V18: real icons for AppsFolder apps. An AppID is
# "<PackageFamilyName>!<EntryId>"; the family name identifies
# the installed package, whose AppxManifest.xml declares the
# app's logo asset. Resolution is Windows-only in effect (the
# package source is PowerShell) and fail-soft at every step:
# any failure reads as "no icon", and the shell keeps the
# letter tile it already draws for path-less entries.
# ------------------------------------------------------------------

#: In-process caches: package locations are read at most once
#: per process (packages do not move while ARIA runs; a
#: restart re-reads), and each AppID's icon resolution —
#: hit or miss — is computed at most once, so re-enumeration
#: on every Shell Home open stays as cheap as V17's.
_APPX_LOCATIONS_CACHE = None
_ICON_CACHE = {}


def package_family_name(appid):
    """The package family name inside an AppID: the segment
    before "!". '' when the AppID carries no family segment."""
    text = str(appid or "").strip()
    if "!" not in text:
        return ""
    return text.split("!", 1)[0].strip()


def appid_entry_id(appid):
    """The application entry id inside an AppID: the segment
    after "!" (the manifest Application's Id). '' if absent."""
    text = str(appid or "").strip()
    if "!" not in text:
        return ""
    return text.split("!", 1)[1].strip()


def _get_appx_packages_stdout(timeout=15):
    """Run Get-AppxPackage (family name + install location
    for every installed package) and return its JSON stdout.
    The AppsFolder icon path's one real side effect, isolated
    so tests inject a fake runner. Raises on any failure —
    the caller fails soft."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive",
           "-Command",
           "Get-AppxPackage | Select-Object PackageFamilyName,"
           " InstallLocation | ConvertTo-Json -Compress"]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NO_WINDOW", 0)
    out = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=timeout, **kwargs)
    if out.returncode != 0:
        raise RuntimeError(
            f"Get-AppxPackage exited {out.returncode}")
    return out.stdout or ""


def enumerate_appx_locations(runner=None, timeout=15):
    """{PackageFamilyName: InstallLocation} for the installed
    AppX packages, via PowerShell. FAIL-SOFT like the
    AppsFolder source: any failure reads as {} (no package
    icons), never an exception."""
    if runner is None:
        def runner():
            return _get_appx_packages_stdout(timeout)
    try:
        raw = json.loads(runner() or "")
    except Exception:
        return {}
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return {}
    out = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        family = str(item.get("PackageFamilyName") or "").strip()
        location = str(item.get("InstallLocation") or "").strip()
        if family and location and family not in out:
            out[family] = location
    return out


def _local_name(tag):
    """An XML tag's local name, namespace stripped."""
    return str(tag or "").rsplit("}", 1)[-1]


def manifest_logo(manifest_text, entry_id=""):
    """The Square44x44Logo asset path declared in an
    AppxManifest.xml for the application *entry_id* (the
    AppID's after-"!" segment). Falls back to the first
    application that declares a logo — a manifest with one
    app is the common case, and a near-miss logo beats the
    letter tile. '' when nothing usable is declared or the
    XML does not parse."""
    try:
        root = ET.fromstring(manifest_text or "")
    except Exception:
        return ""
    fallback = ""
    for elem in root.iter():
        if _local_name(elem.tag) != "Application":
            continue
        logo = ""
        for child in elem.iter():
            if _local_name(child.tag) == "VisualElements":
                logo = str(child.get("Square44x44Logo")
                           or "").strip()
                if logo:
                    break
        if not logo:
            continue
        if not fallback:
            fallback = logo
        if entry_id and str(elem.get("Id") or "").strip() \
                == str(entry_id).strip():
            return logo
    return fallback


_VARIANT_RE = re.compile(
    r"^(?P<stem>.+)\.(?P<kind>targetsize|scale)-(?P<n>\d+)"
    r"(?P<ext>\.[^.]+)$", re.IGNORECASE)


def choose_logo_variant(filenames, logo_name):
    """Pick the best on-disk variant of the manifest's logo
    asset among *filenames* (one directory's names). Store
    packages ship the declared file plus sized variants
    (``Logo.targetsize-48.png``, ``Logo.scale-200.png``).
    Preference: the largest targetsize variant (the unplated
    tile art, sized for exactly this use), then the largest
    scale variant, then the declared base file itself.
    Returns the chosen filename, or '' when no candidate
    matches the asset at all."""
    logo_name = str(logo_name or "").strip()
    if not logo_name:
        return ""
    stem, ext = os.path.splitext(logo_name)
    best = ""
    best_rank = None
    for name in filenames or []:
        if name == logo_name:
            rank = (0, 0)
        else:
            match = _VARIANT_RE.match(name or "")
            if not match or match.group("stem") != stem \
                    or match.group("ext").lower() != ext.lower():
                continue
            n = int(match.group("n"))
            if match.group("kind").lower() == "targetsize":
                rank = (2, n)
            else:
                rank = (1, n)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = name
    return best


def resolve_appsfolder_icon(appid, locations, read_text=None,
                            listdir=None, isfile=None):
    """The on-disk logo file for an AppsFolder AppID, or ''.

    *locations* is enumerate_appx_locations' map. The disk
    seams (read_text / listdir / isfile) are injectable so
    tests resolve against a fake package tree; defaults are
    the real filesystem. Fail-soft throughout."""
    if read_text is None:
        def read_text(path):
            with open(path, encoding="utf-8",
                      errors="replace") as f:
                return f.read()
    if listdir is None:
        listdir = os.listdir
    if isfile is None:
        isfile = os.path.isfile
    family = package_family_name(appid)
    if not family:
        return ""
    locations = locations or {}
    install = locations.get(family)
    if not install:
        folded = family.casefold()
        for key, value in locations.items():
            if str(key).casefold() == folded:
                install = value
                break
    if not install:
        return ""
    try:
        manifest = read_text(os.path.join(
            install, "AppxManifest.xml"))
    except Exception:
        return ""
    logo_rel = manifest_logo(manifest, appid_entry_id(appid))
    if not logo_rel:
        return ""
    logo_rel = logo_rel.replace("\\", "/")
    logo_dir = os.path.join(install,
                            os.path.dirname(logo_rel))
    logo_name = os.path.basename(logo_rel)
    try:
        names = listdir(logo_dir)
    except Exception:
        names = []
    chosen = choose_logo_variant(names, logo_name)
    if chosen:
        return os.path.join(logo_dir, chosen)
    base = os.path.join(logo_dir, logo_name)
    try:
        return base if isfile(base) else ""
    except Exception:
        return ""


def attach_appsfolder_icons(apps, locations=None, runner=None):
    """Return *apps* with an ``icon`` path resolved for every
    AppsFolder entry that lacks one (V18). Entries that
    resolve keep everything else identical; entries that do
    not are returned untouched (the SAME dict), so a failed
    resolution is indistinguishable from V17. Locations are
    fetched at most once per process and each AppID resolves
    at most once per process (the caches above); the
    PowerShell call happens only when an uncached AppsFolder
    entry actually needs it."""
    global _APPX_LOCATIONS_CACHE
    apps = list(apps or [])
    pending = []
    for app in apps:
        if not isinstance(app, dict) \
                or app_kind(app) != "appsfolder" \
                or app.get("icon"):
            continue
        appid = str(app.get("appid") or "")
        if appid and appid not in _ICON_CACHE:
            pending.append(appid)
    if pending:
        if locations is None:
            if _APPX_LOCATIONS_CACHE is None:
                _APPX_LOCATIONS_CACHE = \
                    enumerate_appx_locations(runner=runner)
            locations = _APPX_LOCATIONS_CACHE
        for appid in pending:
            try:
                _ICON_CACHE[appid] = resolve_appsfolder_icon(
                    appid, locations)
            except Exception:
                _ICON_CACHE[appid] = ""
    out = []
    for app in apps:
        if isinstance(app, dict) \
                and app_kind(app) == "appsfolder" \
                and not app.get("icon"):
            icon = _ICON_CACHE.get(
                str(app.get("appid") or ""), "")
            if icon:
                app = dict(app)
                app["icon"] = icon
        out.append(app)
    return out


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
    """Launch one enumerated app entry ({name, path} for a
    shortcut, {name, appid} for an AppsFolder app).

    *opener* is the injectable seam: it receives the launch
    TARGET (:func:`launch_target` — the .lnk path, or the
    ``shell:AppsFolder\\<AppID>`` URI) and does the OS-level
    open. The default is os.startfile (Windows); off Windows it
    reports failure instead of pretending. A bare string still
    works as an entry (it IS the target). Returns True when the
    opener ran without raising.
    """
    if isinstance(entry, dict):
        target = launch_target(entry)
    else:
        target = str(entry or "")
    if not target:
        return False
    if opener is None:
        if not hasattr(os, "startfile"):
            return False

        def opener(p):
            os.startfile(p)  # shortcut path or AppsFolder URI
    try:
        opener(target)
        return True
    except Exception:
        return False
