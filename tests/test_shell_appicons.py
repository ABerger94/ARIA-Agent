"""Shell Home V18 (2026-10-31, Alek's ask): real icons for
AppsFolder apps — "is there anyway to get the app symbol/icon
for things like Spotify? Instead of just a letter."

The pure chain in aria/app_launcher.py: AppID -> package
family name -> the package's InstallLocation (one fail-soft
Get-AppxPackage call) -> AppxManifest.xml's Square44x44Logo
-> the best on-disk variant (largest targetsize, then scale,
then the base asset) -> the entry's "icon" path, which
ShellPanel._icon_for loads before the letter fallback.
Everything here runs on fakes — a fake package tree on disk,
fake PowerShell runners — never a live Windows call.
"""
import json
import os
import sys
import types
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import importlib
app_launcher = importlib.import_module("aria.app_launcher")
shell_panel = importlib.import_module("aria.shell_panel")

NOW = datetime(2026, 10, 31, 9, 5)
SPOTIFY_ID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
SPOTIFY_FAMILY = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0"

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
  <Applications>
    <Application Id="Other" Executable="Other.exe"
                 EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Other"
        Square44x44Logo="Assets\\Other44.png"
        Square150x150Logo="Assets\\Other150.png" />
    </Application>
    <Application Id="Spotify" Executable="Spotify.exe"
                 EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Spotify"
        Square44x44Logo="Assets\\Square44x44Logo.png"
        Square150x150Logo="Assets\\Square150x150Logo.png" />
    </Application>
  </Applications>
</Package>
"""


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    """The launcher's icon caches are process-global by
    design (resolve at most once per process); tests reset
    them so one case's resolution cannot leak into another."""
    monkeypatch.setattr(app_launcher, "_ICON_CACHE", {})
    monkeypatch.setattr(app_launcher, "_APPX_LOCATIONS_CACHE",
                        None)


def _package_tree(tmp_path):
    """A fake installed package: manifest + Assets folder
    with the declared logo and sized variants."""
    install = tmp_path / "Spotify"
    assets = install / "Assets"
    assets.mkdir(parents=True)
    (install / "AppxManifest.xml").write_text(
        MANIFEST, encoding="utf-8")
    for name in ("Square44x44Logo.png",
                 "Square44x44Logo.targetsize-24.png",
                 "Square44x44Logo.targetsize-48.png",
                 "Square44x44Logo.scale-200.png",
                 "Square150x150Logo.png", "Other44.png"):
        (assets / name).write_bytes(b"\x89PNG")
    return install


# -- pure pieces ------------------------------------------------

def test_family_name_and_entry_id():
    assert app_launcher.package_family_name(SPOTIFY_ID) == \
        SPOTIFY_FAMILY
    assert app_launcher.appid_entry_id(SPOTIFY_ID) == "Spotify"
    assert app_launcher.package_family_name("NoBang") == ""
    assert app_launcher.package_family_name("") == ""
    assert app_launcher.appid_entry_id("Family!") == ""


def test_manifest_logo_prefers_matching_application():
    assert app_launcher.manifest_logo(MANIFEST, "Spotify") == \
        "Assets\\Square44x44Logo.png"
    assert app_launcher.manifest_logo(MANIFEST, "Other") == \
        "Assets\\Other44.png"
    # Unknown entry id -> first declared logo as the fallback.
    assert app_launcher.manifest_logo(MANIFEST, "Nope") == \
        "Assets\\Other44.png"
    assert app_launcher.manifest_logo(MANIFEST) == \
        "Assets\\Other44.png"
    assert app_launcher.manifest_logo("not xml <") == ""
    assert app_launcher.manifest_logo("") == ""


def test_choose_logo_variant_order():
    names = ["Square44x44Logo.png",
             "Square44x44Logo.targetsize-24.png",
             "Square44x44Logo.targetsize-48.png",
             "Square44x44Logo.scale-200.png",
             "Square44x44Logo.scale-400.png",
             "Unrelated.targetsize-96.png"]
    # Largest targetsize wins over any scale variant.
    assert app_launcher.choose_logo_variant(
        names, "Square44x44Logo.png") == \
        "Square44x44Logo.targetsize-48.png"
    # No targetsize -> largest scale.
    names = [n for n in names if "targetsize" not in n]
    assert app_launcher.choose_logo_variant(
        names, "Square44x44Logo.png") == \
        "Square44x44Logo.scale-400.png"
    # Base file only -> itself; nothing matching -> ''.
    assert app_launcher.choose_logo_variant(
        ["Square44x44Logo.png"], "Square44x44Logo.png") == \
        "Square44x44Logo.png"
    assert app_launcher.choose_logo_variant(
        ["Other.png"], "Square44x44Logo.png") == ""
    assert app_launcher.choose_logo_variant(names, "") == ""


def test_enumerate_appx_locations_parse_and_failsoft():
    payload = json.dumps([
        {"PackageFamilyName": SPOTIFY_FAMILY,
         "InstallLocation": "C:\\Packages\\Spotify"},
        {"PackageFamilyName": "OtherPkg_abc",
         "InstallLocation": "C:\\Packages\\Other"},
        {"PackageFamilyName": "", "InstallLocation": "x"},
        {"InstallLocation": "orphan"}])
    assert app_launcher.enumerate_appx_locations(
        runner=lambda: payload) == {
            SPOTIFY_FAMILY: "C:\\Packages\\Spotify",
            "OtherPkg_abc": "C:\\Packages\\Other"}
    single = json.dumps({"PackageFamilyName": "One_1",
                         "InstallLocation": "C:\\One"})
    assert app_launcher.enumerate_appx_locations(
        runner=lambda: single) == {"One_1": "C:\\One"}

    def boom():
        raise RuntimeError("no powershell")
    assert app_launcher.enumerate_appx_locations(
        runner=boom) == {}
    assert app_launcher.enumerate_appx_locations(
        runner=lambda: "junk") == {}


# -- resolution against a fake package tree ---------------------

def test_resolve_icon_end_to_end(tmp_path):
    install = _package_tree(tmp_path)
    locations = {SPOTIFY_FAMILY: str(install)}
    icon = app_launcher.resolve_appsfolder_icon(
        SPOTIFY_ID, locations)
    assert icon == str(install / "Assets"
                       / "Square44x44Logo.targetsize-48.png")
    # Family match is case-insensitive.
    lowered = {SPOTIFY_FAMILY.lower(): str(install)}
    assert app_launcher.resolve_appsfolder_icon(
        SPOTIFY_ID, lowered) == icon
    # Unknown family / no bang / missing manifest -> ''.
    assert app_launcher.resolve_appsfolder_icon(
        SPOTIFY_ID, {}) == ""
    assert app_launcher.resolve_appsfolder_icon(
        "NoBang", locations) == ""
    empty = tmp_path / "Empty"
    empty.mkdir()
    assert app_launcher.resolve_appsfolder_icon(
        SPOTIFY_ID, {SPOTIFY_FAMILY: str(empty)}) == ""


def test_enumerate_all_apps_attaches_icon(tmp_path):
    install = _package_tree(tmp_path)
    root = tmp_path / "Programs"
    root.mkdir()
    (root / "Google Chrome.lnk").write_text("x")
    start_apps = json.dumps(
        [{"Name": "Spotify", "AppID": SPOTIFY_ID}])
    appx = json.dumps([{
        "PackageFamilyName": SPOTIFY_FAMILY,
        "InstallLocation": str(install)}])
    apps = app_launcher.enumerate_all_apps(
        [str(root)], appsfolder_runner=lambda: start_apps,
        appx_runner=lambda: appx)
    spotify = next(a for a in apps if a["name"] == "Spotify")
    assert spotify["icon"] == str(
        install / "Assets" / "Square44x44Logo.targetsize-48.png")
    chrome = next(a for a in apps
                  if a["name"] == "Google Chrome")
    assert "icon" not in chrome  # .lnk entries untouched

    # The package source dying changes nothing (V17
    # behaviour) — in a FRESH process: reset the caches the
    # first enumeration filled, since a successful resolution
    # is served from cache for the process's life by design.
    app_launcher._ICON_CACHE = {}
    app_launcher._APPX_LOCATIONS_CACHE = None

    def boom():
        raise RuntimeError("no powershell")
    apps = app_launcher.enumerate_all_apps(
        [str(root)], appsfolder_runner=lambda: start_apps,
        appx_runner=boom)
    spotify = next(a for a in apps if a["name"] == "Spotify")
    assert "icon" not in spotify


# -- the panel renders it ---------------------------------------

def _panel(apps):
    return shell_panel.ShellPanel(
        apps=apps,
        on_launch=lambda app: None,
        on_command=lambda text: None,
        now_provider=lambda: NOW,
        briefing_provider=lambda: None,
        research_provider=lambda: "",
        weather_provider=lambda: None,
        pinned_provider=lambda: [],
        pinned_writer=lambda paths: None,
        pin_groups_provider=lambda: {},
        pin_groups_writer=lambda g: None,
        approval_provider=lambda: [],
        reminder_provider=lambda: [],
        files_provider=lambda: [],
        history_provider=lambda: {},
        history_writer=lambda label: None,
        tasks_provider=lambda: [],
        today_remote_provider=lambda: {"event": None,
                                       "unread": None})


def test_panel_icon_for_uses_resolved_icon(tmp_path):
    png = tmp_path / "logo.png"
    pix = QPixmap(96, 96)
    pix.fill(QColor("#1ed760"))
    assert pix.save(str(png), "PNG")
    plain = {"name": "Spotify", "appid": SPOTIFY_ID}
    with_icon = dict(plain, icon=str(png))
    bogus = dict(plain, icon=str(tmp_path / "missing.png"))
    panel = _panel([plain])
    letter = panel._icon_for(plain).pixmap(QSize(48, 48)) \
        .toImage()
    real = panel._icon_for(with_icon).pixmap(QSize(48, 48)) \
        .toImage()
    fallback = panel._icon_for(bogus).pixmap(QSize(48, 48)) \
        .toImage()
    assert real != letter          # the package art, not a tile
    assert fallback == letter      # bad path -> letter, unchanged
    panel.close()
