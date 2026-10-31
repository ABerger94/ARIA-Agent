"""Shell Home — the ARIA OS home screen panel (2026-10-30).

The front door of Shell Mode: a clock + greeting header with
ARIA's orb beside it, a **tile grid** of his real Start Menu
apps (icon + name), a right rail, and the command bar as a
bottom dock pill.

V3 (2026-10-30, Alek's witnessed verdict + approved redesign):
the dashboard gives Shell Home the FULL window (both side
columns step aside) and the panel paints at reading size; V3
also fixed the witnessed double-launch (one launch signal path
+ a same-app relaunch guard).

V4 (2026-10-30, Alek's direct order after witnessing V3): the
rail held Briefing / Now Playing / Status cards and the
Briefing body still rendered as a short scroll window of
section texts — he ordered the other two cards GONE and the
Briefing card extended to fill the rail, carrying **the same
text as the Workspace Briefing tab**: ``report_text`` of the
latest briefing plus the guarded research-section append,
composed by the pure ``shell_home.briefing_full_text`` helper
and rendered in a read-only text view that fills the card at
12pt-equivalent type. The rail is now: Briefing card + the
nothing else — the Shell Mode boot toggle lives in
Settings → General now (V4 addendum). (The pure
now_playing/status formatters remain in shell_home.py, unused
by this panel, per the V4 brief's no-churn call.) V4 addendum:
the shell's colours follow the dashboard accent Alek sets in
Customize — the stylesheet's accent family hue-shifts via
``aria.hue_wheel.shift_palette`` (the dashboard's mechanism),
the orb discs repaint to match, ``set_accent`` applies changes
live, and the dashboard re-resolves the accent every time
Shell Home opens.

Type (or later, say) what you want in the dock; an unambiguous
app name launches it, everything else goes to ARIA as a normal
turn through the dashboard's existing submit path, approval
gates and all. The **Dashboard** button is always visible,
bottom right: one click and the shell steps aside — Shell Mode
never replaces explorer.exe, so Windows was never gone, just
covered.

V6 (2026-10-30, Alek's order): the header gains a local
weather readout under the date line — a condition symbol
(``shell_home.weather_symbol``) beside one short line
(``shell_home.weather_line``: temp + condition + the day's
high/low). Data comes from ARIA's existing weather source,
``aria.scheduler._weather_full`` (wttr.in, Dundalk), through
an injectable provider, fetched OFF the UI thread (daemon
thread + Qt signal) and throttled to one fetch per ~15
minutes. Fail-soft: no data (offline, source down) leaves the
labels empty — no placeholder, no error, ever.

V8 (2026-10-30, Alek's order + approved design): the home
grid opens to his PINNED apps only. Pins are .lnk paths kept
in pin order in ``aria/ui_prefs`` (injectable provider/writer
seams here, like the weather's); right-clicking any tile —
in the pinned grid, in All Apps, or in search results —
offers Pin to Home / Remove from Home. An "All apps" toggle
beside the search box reveals the full enumeration; search
always searches ALL apps and clearing it returns to the
current view. The count line is honest per view (pure
``shell_home.view_count_line``). Zero pins shows a guidance
note in the grid area, never an empty void. Left-click
behaviour is unchanged (single select, double launch, the
V3 relaunch guard intact), and the command bar still routes
against ALL apps, pinned or not.

V9 (2026-10-30, Alek's order): the header's static OrbDisc
is replaced by her real waveform orb
(``aria.resonance.ResonanceOrb``, transparent, 96px) — the
dashboard's render_once forwards its (state, level, muted)
triple to ``advance_orb`` while Shell Home is active (the V7
flag), so the home screen's orb breathes with her voice. Its
colours are the resonance module palette's, shifted by the
dashboard's painted-accent path — Customize reaches it for
free. The dock's 18px dot stays an OrbDisc.

V10 (2026-10-30, Alek's witnessed report): the V4 accent
shift covered only the accent HUES — the shell's navy
surfaces (panel/card/grid/dock backgrounds) stayed stock
and clashed with the shifted dashboard frame. The shift now
covers EVERY hex in the stylesheet (derived from the sheet
itself, surfaces and text tones included) plus the painted
surfaces audited onto the same map (dock orb colours, the
letter tiles' glyph ink; the tile FACE palette stays an
intentional multi-hue exception — those stand in for app
icons). With a non-default accent, no stock stylesheet hex
survives; with none, the sheet is byte-identical stock.

V11 (2026-10-31, Alek's order): action cards. A pending
approval (aria.approval's store, read via the pending_entries
snapshot that module documents as the UI-surface seam) shows
a card above the dock with the request's description and
Approve/Deny buttons wired to approve_token / deny_token —
the SAME resolution path the dashboard's ConfirmBanner uses,
no parallel policy, no second store. A delivered reminder
still awaiting acknowledgement (aria.reminder_gate's
pending_cards over the scheduler's gate store) shows a card
with its text and a "Got it" button wired to
scheduler.tool_acknowledge_reminders — the voice tool's path.
Cards poll on the panel's existing refresh tick; with nothing
pending there are no cards and zero visual change.

V12 (2026-10-31, Alek's order): the command bar is a real
command palette. Typing opens a dropdown of candidates —
apps (the launcher's ranking), files/folders from a bounded
cached enumeration of his own folders, the dashboard's tabs,
and ARIA actions (Lock, Open ARIA's Room, Dashboard, Set a
reminder…, Mute/Unmute with a live-state label) — ranked by
the pure palette_candidates with his persisted usage history
(ui_prefs command_history) boosting what he actually picks.
Up/Down/Enter/Esc drive it from the keyboard. Selection
dispatches by kind through the existing seams only: apps via
the direct-launch path with its relaunch guard, files via
os.startfile, tabs and actions via dashboard callbacks (the
dashboard exits Shell Home / engages the lock / toggles voice
itself — the panel reaches for none of those widgets).
'Set a reminder…' prefills the bar; the finished text takes
the normal submit path. Text matching nothing still falls
through _submit_command to route_command/chat, unchanged.

V13 (2026-10-31, Alek's order): actionable Today cards. A
compact Today section sits above the Briefing in the rail:
the next calendar event with an Open button (the V12 tab
callback to Calendar), scheduled tasks that are due or due
within 24h with Done / Snooze +1h buttons on 'once' rows
(scheduler.sched_cancel / the new sched_snooze — real ops on
the real table), and the unread mail count with an Open
button to Inbox. Tasks read live on the tick (local sqlite);
calendar and mail are network, so they arrive through a
throttled off-thread fetch like the weather's. Every input
fails soft and the section hides when there is nothing to
act on. No pause/resume control exists: scheduled_tasks has
no enabled flag and no pause seam — state is shown, never a
fake button. The tall Briefing card below is untouched.

V14 (2026-10-31, Alek's order): pinned groups + drag
reorder. A pin may carry a group name (ui_prefs' pin_groups
map — additive beside the V8 ordered path list, so pre-V14
pins migrate by absence: ungrouped, order untouched). When
any pin is grouped, the pinned view renders as a list with
an inert bold header per group; otherwise it is exactly the
V8 icon grid. The pinned view supports internal drag-drop:
dropping persists the new order through the same seams, and
an app dropped under another group's header joins that
group (the order capture walks headers). The right-click
menu gains Move up / Move down (the no-drag path to the same
result) and Move to group ▸ — existing groups, Ungrouped,
and New group… via a name prompt. Search and All Apps are
unchanged and drag-free.

V16 (2026-10-31, Alek's live witness on bcd52ef5: "briefing
area needs to be bigger on home shell so i can read it
better"): his screenshot showed the Briefing card squashed
to a few lines with dead space above and below the panel.
Measured cause: the rail was never the constraint — the
PANEL was. ShellPanel carried Qt's default Preferred size
policy, so in the dashboard's centerpiece grid cell it sat
at its size-hint height, vertically centred, while the
cell (and the window) offered far more; the rail's stretch
only ever divided that hint height. The panel now claims
Expanding/Expanding, like the dashboard's own full-bleed
centerpieces: the cell's full height reaches the rail, the
Briefing card's stretch takes everything the compact Today
card (now Maximum-height policy, natural size only) leaves,
and the briefing body carries a 140px minimum so it can
never be squeezed back to a sliver. The body type stays at
the stylesheet's 16px — the ask was area. Same screenshot:
Today's Open buttons rendered with their text cut at the
button's edge. Row layout hardened in _today_row: the label
is Ignored-policy with a zero minimum (it absorbs ALL
horizontal compression, wrapping as it goes) and every row
button is Fixed-policy at its full size-hint size, so a
button can never be shrunk into its own text again.

V17 (2026-10-31, Alek's asks): two fixes. (A) Missing apps —
the launcher also enumerates the Windows AppsFolder
(Get-StartApps), so Store / per-user apps with no Start Menu
.lnk (his example: Spotify) appear, launch through
shell:AppsFolder/<AppID>, and pin under the key appid:<AppID>;
pin identity everywhere in this panel is now the app KEY
(app_launcher.app_key) — for .lnk apps the key is the path,
so existing pins are untouched. (B) Grouped pins render as
horizontal SHELVES, not the V14 vertical list (see the shelf
section below); ungrouped is still exactly the V8 grid.

The card never fabricates: no briefing composed yet yields
report_text's own honest placeholder line.

All routing logic lives in aria/shell_home.py and enumeration in
aria/app_launcher.py (both pure); this file is only the paint.
Callbacks are injected: on_launch(app), on_command(text) — the
dashboard wires them; tests and screenshots wire recorders.
There is NO exit button in the panel (V5, Alek 2026-10-30:
"I only need one Dashboard button") — the dashboard's own
center-controls button turns into the Dashboard button while
the shell is open. Card providers are injected the same way
and default to the real seams, lazily and fail-soft.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime

from aria import app_launcher, shell_home
from aria.resonance import ResonanceOrb

try:
    from PyQt6.QtCore import (QEvent, QFileInfo, QSize, Qt, QTimer,
                              pyqtSignal)
    from PyQt6.QtGui import (QColor, QFont, QIcon, QPainter, QPixmap,
                             QTextOption)
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QFileIconProvider,
        QFrame,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except Exception:  # pragma: no cover - Qt absent (headless tools)
    QWidget = object
    pyqtSignal = None

#: Same-app relaunch guard (V3): a second launch of the SAME app
#: inside this window is treated as signal bounce, not intent —
#: the belt-and-braces half of the double-launch fix. Legitimate
#: re-opens come seconds apart; the window stays far below that.
LAUNCH_GUARD_SECONDS = 1.5

_STYLE = """
QWidget#shellHome { background: #0b1622; }
QLabel#shellClock { color: #eaf6ff; font-size: 56px; font-weight: 600; }
QLabel#shellGreeting { color: #57c7d4; font-size: 22px; }
QLabel#shellDate { color: #8fb8c9; font-size: 15px; }
QLabel#shellWeather { color: #8fb8c9; font-size: 15px; }
QLabel#shellWeatherSymbol { color: #57c7d4; font-size: 27px; }
QLabel#shellCount { color: #8fb8c9; font-size: 13px; }
QLineEdit#shellSearch { background: #101f30; color: #eaf6ff;
            border: 1px solid #1b3042; border-radius: 6px;
            padding: 9px 12px; font-size: 15px; }
QListWidget#shellAppList { background: #0e1c2c; color: #dceef7;
              border: 1px solid #1b3042; border-radius: 10px;
              font-size: 14px; }
QListWidget#shellAppList::item { padding: 8px 4px; }
QListWidget#shellAppList::item:selected { background: #17435a;
              border-radius: 8px; }
QFrame#shellCard { background: #0e1c2c; border: 1px solid #1b3042;
              border-radius: 10px; }
QLabel#shellCardTitle { color: #57c7d4; font-size: 14px;
              font-weight: 700; }
QPlainTextEdit#shellBriefingText { background: #0e1c2c;
              color: #dceef7; border: none; font-size: 16px; }
QScrollBar:vertical { background: #0e1c2c; width: 10px; }
QScrollBar::handle:vertical { background: #2b5873;
              border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
              height: 0; }
QFrame#shellDock { background: #101f30; border: 1px solid #2b5873;
              border-radius: 22px; }
QLineEdit#shellCommand { background: transparent; color: #eaf6ff;
            border: none; padding: 10px 6px; font-size: 15px; }
QPushButton#shellViewToggle { background: #101f30; color: #dceef7;
            border: 1px solid #1b3042; border-radius: 6px;
            padding: 9px 14px; font-size: 14px; }
QPushButton#shellViewToggle:hover { background: #17435a; }
QLabel#shellEmpty { color: #8fb8c9; font-size: 16px; }
QMenu { background: #101f30; color: #eaf6ff;
            border: 1px solid #1b3042; }
QMenu::item { padding: 6px 22px; }
QMenu::item:selected { background: #17435a; }
QFrame#shellActionCard { background: #0e1c2c;
              border: 1px solid #57c7d4; border-radius: 10px; }
QLabel#shellActionTitle { color: #57c7d4; font-size: 14px;
              font-weight: 700; }
QLabel#shellActionText { color: #eaf6ff; font-size: 15px; }
QPushButton#shellActionBtn { background: #101f30; color: #dceef7;
            border: 1px solid #1b3042; border-radius: 6px;
            padding: 8px 18px; font-size: 14px; }
QPushButton#shellActionBtn:hover { background: #17435a; }
QListWidget#shellPalette { background: #101f30; color: #eaf6ff;
              border: 1px solid #2b5873; border-radius: 10px;
              font-size: 14px; }
QListWidget#shellPalette::item { padding: 7px 12px; }
QListWidget#shellPalette::item:selected { background: #17435a;
              border-radius: 6px; }
QLabel#shellGroupHeader { color: #57c7d4; font-size: 14px;
              font-weight: 700; }
QScrollArea#shellShelfScroll { background: #0e1c2c;
              border: 1px solid #1b3042; border-radius: 10px; }
QWidget#shellShelves { background: #0e1c2c; }
"""


#: The shell's accent family (V4 addendum; V10 completed it):
#: EVERY hex in the stylesheet — surfaces and text tones, not
#: just the accent hues — hue-shifted as one family by
#: aria.hue_wheel.shift_palette, the dashboard's own mechanism,
#: whenever Alek's Customize accent is in force. V10 is Alek's
#: witnessed fix: the V4 set held only six accent hues (two of
#: them not even in the sheet anymore), so the navy SURFACES
#: (#0b1622 / #0e1c2c / #101f30) never moved and clashed with
#: the shifted dashboard frame around the shell. The wheel
#: preserves each colour's saturation and value, so surfaces
#: stay exactly as dark — only their hue family moves; the
#: near-white text tones shift only subtly, which is correct.
#: The set is DERIVED from _STYLE, so a future rule cannot
#: silently introduce an unshifted hex. The dock orb's heart
#: hex (#bdf3fa) is not in the sheet but rides the same map
#: via _ORB_STOCK.
_ACCENT_BASE = "#57c7d4"
_STYLE_HEXES = tuple(sorted(set(re.findall(r"#[0-9a-f]{6}",
                                           _STYLE))))
_ORB_STOCK = ("#2b5873", "#57c7d4", "#bdf3fa")  # ring, core, heart
_SHIFT_HEXES = tuple(sorted(set(_STYLE_HEXES) | set(_ORB_STOCK)))


def _valid_accent(accent):
    """Normalize a '#rrggbb' accent, or None when it is not one."""
    try:
        s = str(accent or "").strip().lower()
        if len(s) == 7 and s.startswith("#"):
            int(s[1:], 16)
            return s
    except Exception:
        pass
    return None


def _shift_map(accent):
    """{stock hex: shifted hex} for the shell's WHOLE palette —
    every hex in the stylesheet plus the dock orb's — or None
    when *accent* is unset/invalid (stock palette)."""
    accent = _valid_accent(accent)
    if not accent:
        return None
    try:
        from aria.hue_wheel import shift_palette
        return shift_palette({h: h for h in _SHIFT_HEXES},
                             _ACCENT_BASE, accent)
    except Exception:
        return None


def shell_style(accent=None):
    """The panel stylesheet for *accent*: the stock sheet with
    EVERY hex hue-shifted to the accent family (dashboard
    parity — V10: surfaces and text tones included, so with a
    non-default accent no stock hex survives in the sheet).
    Unset/invalid accent returns the stock sheet unchanged —
    the default path can never lose its theme."""
    shifted = _shift_map(accent)
    if not shifted:
        return _STYLE
    return re.sub("|".join(re.escape(h) for h in _STYLE_HEXES),
                  lambda m: shifted[m.group(0)], _STYLE)


def _tile_ink(accent):
    """The letter-tile glyph ink (V10 painted-surface audit):
    the shell background hex, taken from the SAME shifted map
    as everything else the panel paints — never a stock hex
    left behind when an accent is in force. (The tile FACE
    colours are the intentional multi-hue icon stand-ins and
    stay exempt.)"""
    shifted = _shift_map(accent)
    if shifted:
        return shifted.get("#0b1622", "#0b1622")
    return "#0b1622"


def _orb_colors(accent=None):
    """(ring, core, heart) hexes for the orb under *accent*."""
    shifted = _shift_map(accent)
    if not shifted:
        return _ORB_STOCK
    return tuple(shifted[h] for h in _ORB_STOCK)


def _resolve_accent():
    """The accent currently SET, in the dashboard's own order
    (ui_prefs Customize first, then settings 'ui_accent').
    Reimplemented fail-soft here — shell_panel must never import
    the dashboard (it imports us). None = nothing set = stock."""
    try:
        from aria import ui_prefs
        found = _valid_accent(ui_prefs.get_accent())
        if found:
            return found
    except Exception:
        pass
    try:
        from aria import config
        return _valid_accent(config.get_setting("ui_accent", None))
    except Exception:
        return None


class OrbDisc(QWidget):
    """ARIA's presence on the home: a simple orb disc drawn in
    Qt (the brief's sanctioned lightweight stand-in until a
    heavier orb widget earns a seam here). Its colours follow
    the shell's accent family via set_colors."""

    def __init__(self, parent=None, diameter=84, colors=None):
        super().__init__(parent)
        self._d = diameter
        self.setFixedSize(diameter, diameter)
        ring, core, heart = colors or _ORB_STOCK
        self._ring = QColor(ring)
        self._core_color = QColor(core)
        self._heart = QColor(heart)

    def set_colors(self, ring, core, heart):
        self._ring = QColor(ring)
        self._core_color = QColor(core)
        self._heart = QColor(heart)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._d
        # Outer ring.
        p.setPen(self._ring)
        p.setBrush(Qt.GlobalColor.transparent)
        p.drawEllipse(2, 2, d - 4, d - 4)
        # Glowing core.
        core = int(d * 0.52)
        off = (d - core) // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._core_color)
        p.drawEllipse(off, off, core, core)
        # Bright heart.
        heart = int(d * 0.22)
        hoff = (d - heart) // 2
        p.setBrush(self._heart)
        p.drawEllipse(hoff, hoff, heart, heart)
        p.end()


def _letter_icon(tile, size=48, ink="#0b1622"):
    """Paint the fallback letter tile as an icon. *ink* is the
    glyph colour — the caller passes the accent-shifted shell
    background (see _tile_ink), never a frozen stock hex."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tile["color"]))
    p.drawRoundedRect(2, 2, size - 4, size - 4, 10, 10)
    p.setPen(QColor(ink))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.5))
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, tile["letter"])
    p.end()
    return QIcon(pm)


# -- V17-B: one group's shelf -----------------------------------
# A shelf is an IconMode tile list like the V8 grid, scoped to
# one group. Within-shelf drags are Qt's own model moves (the
# panel persists off rowsMoved, the V14 pattern). A drag from
# ANOTHER shelf carries the app's pin key in a custom mime
# format; the drop is handed to the panel, which moves the
# entry in the pure model and repaints — widget-level drag
# bookkeeping never becomes state.
if pyqtSignal is not None:

    class _ShelfList(QListWidget):
        """One group's horizontal shelf (V17-B)."""

        APP_MIME = "application/x-aria-shell-app-key"

        def __init__(self, parent=None):
            super().__init__(parent)
            self.group = ""
            self.on_cross_drop = None  # (key, group, row)

        def supportedDropActions(self):
            return (Qt.DropAction.CopyAction
                    | Qt.DropAction.MoveAction)

        def mimeTypes(self):
            return list(super().mimeTypes()) + [self.APP_MIME]

        def mimeData(self, items):
            data = super().mimeData(items)
            for item in items or []:
                app = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(app, dict):
                    key = app_launcher.app_key(app)
                    if key:
                        data.setData(
                            self.APP_MIME,
                            key.encode("utf-8"))
                    break
            return data

        def dragEnterEvent(self, event):
            if event.source() is self or event.mimeData() \
                    .hasFormat(self.APP_MIME):
                event.acceptProposedAction()
            else:
                super().dragEnterEvent(event)

        def dragMoveEvent(self, event):
            if event.source() is self or event.mimeData() \
                    .hasFormat(self.APP_MIME):
                event.acceptProposedAction()
            else:
                super().dragMoveEvent(event)

        def dropEvent(self, event):
            md = event.mimeData()
            if event.source() is not self and md is not None \
                    and md.hasFormat(self.APP_MIME):
                key = bytes(md.data(self.APP_MIME)).decode(
                    "utf-8", "replace")
                pos = event.position().toPoint()
                idx = self.indexAt(pos)
                row = idx.row() if idx.isValid() else self.count()
                try:
                    if idx.isValid() and \
                            self.dropIndicatorPosition() == \
                            QAbstractItemView.DropIndicatorPosition.BelowItem:
                        row += 1
                except Exception:
                    pass
                if key and self.on_cross_drop is not None:
                    self.on_cross_drop(key, self.group, row)
                # CopyAction: the panel repaints every shelf
                # from the persisted model, so the source list
                # must NOT also remove its row itself.
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                return
            super().dropEvent(event)

else:  # pragma: no cover - Qt absent (headless tools)
    _ShelfList = None


# -- default card providers: the real seams, lazy + fail-soft ---

def _default_briefing():
    try:
        from aria import briefing
        return briefing.latest_briefing()
    except Exception:
        return None


def _default_research():
    """The research section for the Briefing card, guarded
    exactly like the dashboard's Briefing-tab load: research can
    never break the render, so any failure is an empty string."""
    try:
        from aria.research import research_section_text
        return research_section_text() or ""
    except Exception:
        return ""


#: Weather refetch throttle (V6): the header readout refreshes
#: at most once per ~15 minutes — weather does not move faster
#: than that, and the shell's 20s clock tick must never turn
#: into a network fetch per tick.
WEATHER_REFETCH_SECONDS = 900.0


def _default_weather():
    """The header weather dict from ARIA's existing source:
    aria.scheduler._weather_full() (wttr.in, Dundalk) — the
    same fetch her briefings use; no new service, no key. The
    scheduler is imported lazily and any failure reads as None
    (the panel then simply shows no weather). This BLOCKS on
    the network — callers must run it off the UI thread."""
    try:
        from aria.scheduler import _weather_full
        return _weather_full()
    except Exception:
        return None


def _default_pins():
    """The persisted pin list (V8): aria.ui_prefs'
    get_pinned_apps — ordered .lnk paths. Fail-soft to []
    (no pins = the empty state, never a broken home)."""
    try:
        from aria import ui_prefs
        return ui_prefs.get_pinned_apps()
    except Exception:
        return []


def _default_save_pins(paths):
    """Persist the pin list (V8) through aria.ui_prefs;
    fail-soft — a prefs write can never break the shell."""
    try:
        from aria import ui_prefs
        ui_prefs.set_pinned_apps(paths)
    except Exception:
        pass


# -- V11 action-card providers: the real seams, lazy + fail-soft -

def _default_approvals():
    """Pending-approval snapshot: aria.approval.pending_entries
    — the read its own docstring reserves for UI surfaces."""
    try:
        from aria import approval
        return approval.pending_entries()
    except Exception:
        return []


def _default_resolve_approval(token, ok):
    """Resolve an approval EXACTLY as the dashboard's confirm
    banner does: deny drops it inline; approve runs
    approve_token — which EXECUTES the stashed call and can
    take seconds — on a daemon thread, never the UI thread."""
    try:
        from aria import approval
        if not ok:
            approval.deny_token(token)
            return

        def work():
            try:
                approval.approve_token(token)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True,
                         name="shell-approve").start()
    except Exception:
        pass


def _default_reminders():
    """Delivered-but-unacknowledged reminder cards:
    reminder_gate.pending_cards over the scheduler's own gate
    store — the same store the scheduler's tick writes."""
    try:
        from aria import reminder_gate
        from aria.scheduler import _gate_store
        return reminder_gate.pending_cards(_gate_store())
    except Exception:
        return []


def _default_pin_groups():
    """The persisted pin-group map (V14): ui_prefs'
    get_pin_groups — {pinned path: group name}. Fail-soft
    to {} (all ungrouped)."""
    try:
        from aria import ui_prefs
        return ui_prefs.get_pin_groups()
    except Exception:
        return {}


def _default_save_pin_groups(groups):
    """Persist the pin-group map (V14) through ui_prefs;
    fail-soft like every prefs write here."""
    try:
        from aria import ui_prefs
        ui_prefs.set_pin_groups(groups)
    except Exception:
        pass


def _default_ack_reminders():
    """Acknowledge through scheduler.tool_acknowledge_reminders
    — literally the voice tool's code path (it clears the gate
    store's delivered list AND deletes the inbox card files)."""
    try:
        from aria.scheduler import tool_acknowledge_reminders
        tool_acknowledge_reminders()
    except Exception:
        pass


# -- V13 Today providers: the real seams, lazy + fail-soft -----

def _default_tasks():
    """Scheduled tasks for Today: scheduler.sched_list() —
    (id, kind, prompt, interval_s, next_run) rows straight
    from the scheduled_tasks table the scheduler itself runs."""
    try:
        from aria import scheduler
        return scheduler.sched_list()
    except Exception:
        return []


def _default_task_action(tid, action):
    """Today's task buttons through the scheduler's own ops:
    Done -> sched_cancel (the row is finished, it will not
    fire), Snooze -> sched_snooze (+1h, 'once' rows only)."""
    try:
        from aria import scheduler
        if action == "done":
            scheduler.sched_cancel(tid)
        elif action == "snooze":
            scheduler.sched_snooze(tid, 3600)
    except Exception:
        pass


def _default_today_remote():
    """The network half of Today — {event, unread} — gathered
    OFF the UI thread (the panel throttles + signals it like
    the weather fetch). Event: the next not-yet-ended event
    from the same iCal feed tool_check_calendar reads.
    Unread: the [uid= line count of tool_read_email(
    unread_only=True) — the exact parse the briefing's email
    section uses; 'No messages match' is a real zero, while a
    credentials/fetch failure stays None (unknown, row hides).
    """
    event = None
    unread = None
    try:
        from aria.tools import builtins
        url = builtins._ical_url()
        if url:
            from aria import ical
            now = datetime.now()
            for e in ical.upcoming_from_feed(url, days=7):
                if e["end"] >= now:
                    event = {"summary": e["summary"],
                             "start": e["start"],
                             "all_day": e["all_day"]}
                    break
    except Exception:
        event = None
    try:
        from aria.tools import builtins
        res = builtins.tool_read_email(unread_only=True, limit=99)
        if res and "No messages match" in res:
            unread = 0
        elif res and "No Gmail credentials" not in res:
            lines = [l for l in res.split("\n")
                     if l.strip().startswith("[uid=")]
            if lines:
                unread = len(lines)
    except Exception:
        unread = None
    return {"event": event, "unread": unread}


# -- V12 palette providers: the real seams, lazy + fail-soft ----

def _default_files():
    """The palette's bounded file enumeration (shell_home's
    file_roots + enumerate_files: shallow, capped, his folders
    only — never a disk scan)."""
    try:
        return shell_home.enumerate_files(shell_home.file_roots())
    except Exception:
        return []


def _default_history():
    """Palette usage history from aria.ui_prefs (V12)."""
    try:
        from aria import ui_prefs
        return ui_prefs.get_command_history()
    except Exception:
        return {}


def _default_record_history(label):
    """Persist one palette pick through aria.ui_prefs (V12)."""
    try:
        from aria import ui_prefs
        ui_prefs.record_command_history(label)
    except Exception:
        pass


def _default_file_opener(path):
    """Open a file/folder the way Windows opens it (os.startfile
    — the launcher's own mechanism for app shortcuts). Absent
    off Windows; fail-soft everywhere."""
    try:
        opener = getattr(os, "startfile", None)
        if opener is not None and path:
            opener(path)
    except Exception:
        pass


def _default_muted():
    """Current voice-muted state for the palette's Mute/Unmute
    label: the negation of aria.speech.VOICE_ENABLED — the same
    flag the dashboard's render loop paints."""
    try:
        from aria import speech
        return not getattr(speech, "VOICE_ENABLED", True)
    except Exception:
        return False


class ShellPanel(QWidget):
    """The Shell Home screen. See the module docstring."""

    # V6: (weather_line, weather_symbol) fetched off-thread,
    # delivered back to the UI thread through this signal.
    if pyqtSignal is not None:
        _weather_ready = pyqtSignal(str, str)
        # V13: the Today section's network half (next calendar
        # event + unread count), same off-thread pattern.
        _today_ready = pyqtSignal(object)

    def __init__(self, parent=None, apps=None, apps_provider=None,
                 on_launch=None, on_command=None,
                 now_provider=None, briefing_provider=None,
                 research_provider=None, accent=None,
                 time_provider=None, weather_provider=None,
                 pinned_provider=None, pinned_writer=None,
                 approval_provider=None, approval_resolver=None,
                 reminder_provider=None, reminder_ack=None,
                 files_provider=None, history_provider=None,
                 history_writer=None, file_opener=None,
                 muted_provider=None, on_open_tab=None,
                 on_shell_action=None, tasks_provider=None,
                 task_actor=None, today_remote_provider=None,
                 pin_groups_provider=None, pin_groups_writer=None):
        super().__init__(parent)
        self.setObjectName("shellHome")
        # V16: claim the dashboard centerpiece cell's full
        # size. With the default Preferred policy the panel
        # sat at its size-hint height in the grid cell and the
        # rail (Briefing included) was squeezed into it —
        # Alek's 2026-10-31 screenshot. Expanding both ways,
        # the same policy the dashboard's own full-bleed
        # centerpieces set explicitly.
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        # Accent (V4 addendum): an explicit accent wins; otherwise
        # resolve the one currently set (Customize / Settings),
        # exactly like the dashboard does at boot. None anywhere
        # means the stock cyan family, unchanged.
        self._accent = (_valid_accent(accent) if accent is not None
                        else _resolve_accent())
        self.setStyleSheet(shell_style(self._accent))
        self._apps_provider = apps_provider
        self._apps = list(apps) if apps is not None else []
        self._on_launch = on_launch
        self._on_command = on_command
        self._now_provider = now_provider or datetime.now
        # Monotonic clock for the relaunch guard — injectable so
        # the guard window is testable without real waiting.
        self._time_provider = time_provider or time.monotonic
        self._last_launch_key = None
        self._last_launch_at = 0.0
        self.briefing_text = None  # set by the Briefing card
        self._briefing_provider = briefing_provider or _default_briefing
        self._research_provider = research_provider or _default_research
        self._icon_provider = None
        # Weather (V6): injectable provider returning a
        # _weather_full()-shaped dict or None; default is her
        # existing wttr.in seam. Fetches run on a daemon thread
        # and are timestamp-gated through _time_provider, so the
        # throttle window is testable like the launch guard's.
        self._weather_provider = weather_provider or _default_weather
        self._weather_fetched_at = None
        self._weather_thread = None
        if pyqtSignal is not None:
            self._weather_ready.connect(self._apply_weather)
        # Pins (V8): injectable provider/writer over the
        # persisted pin list (ordered .lnk paths); defaults are
        # the real ui_prefs seams. The home grid OPENS on the
        # pinned view — the full list hides behind the toggle.
        self._pinned_provider = pinned_provider or _default_pins
        self._pinned_writer = pinned_writer or _default_save_pins
        self._view_all = False
        # Action cards (V11): injectable seams over the REAL
        # approval store and reminder gate — the defaults read
        # and resolve through the same functions the dashboard
        # banner and the voice tools use. Nothing here keeps a
        # second copy of pending state; the panel holds only
        # the token of the card currently painted.
        self._approval_provider = approval_provider or \
            _default_approvals
        self._approval_resolver = approval_resolver or \
            _default_resolve_approval
        self._reminder_provider = reminder_provider or \
            _default_reminders
        self._reminder_ack = reminder_ack or _default_ack_reminders
        self._approval_token = None
        # Command palette (V12): injectable seams — bounded file
        # enumeration (cached after first build), usage history
        # over ui_prefs, the file opener, the mute state, and
        # the two dashboard callbacks (tab jumps and ARIA
        # actions the panel cannot perform alone). The panel
        # holds no candidate state beyond the painted list.
        self._files_provider = files_provider or _default_files
        self._files_cache = None
        self._history_provider = history_provider or \
            _default_history
        self._history_writer = history_writer or \
            _default_record_history
        self._file_opener = file_opener or _default_file_opener
        self._muted_provider = muted_provider or _default_muted
        self._on_open_tab = on_open_tab
        self._on_shell_action = on_shell_action
        self._palette_suppress = False
        # Today cards (V13): injectable seams — scheduled tasks
        # + their actor (local sqlite, read on the tick), and
        # the network half (calendar + mail) through a throttled
        # off-thread provider like the weather's.
        self._tasks_provider = tasks_provider or _default_tasks
        self._task_actor = task_actor or _default_task_action
        self._today_remote_provider = today_remote_provider or \
            _default_today_remote
        self._today_remote = {"event": None, "unread": None}
        self._today_fetched_at = None
        self._today_thread = None
        self._today_model = None
        if pyqtSignal is not None:
            self._today_ready.connect(self._apply_today_remote)
        # Pin groups (V14): a parallel seam pair over ui_prefs'
        # {path: group} map — the V8 paths seam is untouched,
        # so pin/unpin behaves exactly as before and groups
        # layer on top. _rebuilding guards the rowsMoved
        # persistence hook while _refilter repopulates.
        self._pin_groups_provider = pin_groups_provider or \
            _default_pin_groups
        self._pin_groups_writer = pin_groups_writer or \
            _default_save_pin_groups
        self._rebuilding = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 24)
        root.setSpacing(14)

        # -- header: orb + clock/greeting/date ------------------
        header = QHBoxLayout()
        # V9 (Alek, 2026-10-30): the header orb is her REAL
        # waveform orb (aria.resonance.ResonanceOrb), not the
        # static painted disc — the dashboard forwards its
        # (state, level, muted) triple here while Shell Home is
        # active (see advance_orb), so the home screen breathes
        # with her voice. Transparent, so the shell background
        # shows through; fixed 96px in the old disc's slot. Its
        # colours are the resonance module palette's, which the
        # dashboard's painted-accent path already shifts — the
        # Customize accent reaches it with no panel wiring.
        # (The 18px dock dot stays an OrbDisc: a waveform at
        # that size is mush; it's a status dot.)
        self.orb = ResonanceOrb(self, transparent_background=True)
        self.orb.setFixedSize(96, 96)
        header.addWidget(self.orb)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.clock_label = QLabel("--:--")
        self.clock_label.setObjectName("shellClock")
        titles.addWidget(self.clock_label)
        self.greeting_label = QLabel("")
        self.greeting_label.setObjectName("shellGreeting")
        titles.addWidget(self.greeting_label)
        self.date_label = QLabel("")
        self.date_label.setObjectName("shellDate")
        titles.addWidget(self.date_label)
        # V6: the weather row — condition symbol + one short
        # line under the date. Both labels start empty and stay
        # empty until a fetch delivers real data (fail-soft).
        weather_row = QHBoxLayout()
        weather_row.setSpacing(8)
        self.weather_symbol_label = QLabel("")
        self.weather_symbol_label.setObjectName("shellWeatherSymbol")
        weather_row.addWidget(self.weather_symbol_label)
        self.weather_label = QLabel("")
        self.weather_label.setObjectName("shellWeather")
        weather_row.addWidget(self.weather_label)
        weather_row.addStretch(1)
        titles.addLayout(weather_row)
        header.addLayout(titles)
        header.addStretch(1)
        root.addLayout(header)

        # -- main: apps column + right rail ----------------------
        main = QHBoxLayout()
        main.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(10)
        # V8: the search row carries the view toggle — the grid
        # opens on Pinned; this button reveals All Apps (and
        # reads "Pinned" while the full list is up, naming where
        # a click goes, like the dashboard's one button does).
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setObjectName("shellSearch")
        self.search.textChanged.connect(self._refilter)
        search_row.addWidget(self.search, 1)
        self.view_toggle = QPushButton("All apps")
        self.view_toggle.setObjectName("shellViewToggle")
        self.view_toggle.clicked.connect(self._toggle_view)
        search_row.addWidget(self.view_toggle)
        left.addLayout(search_row)

        self.app_list = QListWidget()
        self.app_list.setObjectName("shellAppList")
        self.app_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.app_list.setIconSize(QSize(48, 48))
        # V3: cell height carries a TWO-line name — IconMode
        # elides a long name that cannot fit its cell, and the
        # squeezed V2 render showed names sliced to one line.
        self.app_list.setGridSize(QSize(120, 122))
        self.app_list.setWordWrap(True)
        self.app_list.setResizeMode(
            QListWidget.ResizeMode.Adjust)
        self.app_list.setMovement(QListWidget.Movement.Static)
        self.app_list.setUniformItemSizes(True)
        # ONE launch signal path (V3 double-launch fix): V2 also
        # connected itemDoubleClicked here, and Qt fires BOTH
        # signals for a single double-click — every double-click
        # launched the app twice on Alek's machine. itemActivated
        # alone already covers double-click AND Enter.
        self.app_list.itemActivated.connect(self._activate_item)
        # V8: right-click pins/unpins — the menu is custom, so
        # left-click activation above is untouched.
        self.app_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.app_list.customContextMenuRequested.connect(
            self._on_context_menu)
        # V14: drag reorder in the pinned view persists through
        # the model's rowsMoved — connected once here; the
        # handler ignores programmatic rebuilds (_rebuilding)
        # and non-pinned views (drag is disabled there anyway).
        try:
            self.app_list.model().rowsMoved.connect(
                self._on_rows_moved)
        except Exception:
            pass
        # The grid area is a two-page stack (V8): the tile grid,
        # or the pinned-empty guidance note when there is
        # nothing pinned — never an empty void.
        self.grid_stack = QStackedWidget()
        self.grid_stack.addWidget(self.app_list)
        self.empty_label = QLabel(
            "No pinned apps yet — open All Apps and right-click "
            "an app to pin it here.")
        self.empty_label.setObjectName("shellEmpty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid_stack.addWidget(self.empty_label)
        # V17-B: the grouped pinned view's page — a scroll
        # area of group SHELVES (header + horizontal tile
        # shelf per group), built by _paint_shelves. It is a
        # stack page of its own so the ungrouped home keeps
        # rendering through app_list, exactly the V8 grid.
        self._shelf_lists = []
        self.shelf_scroll = QScrollArea()
        self.shelf_scroll.setObjectName("shellShelfScroll")
        self.shelf_scroll.setWidgetResizable(True)
        self.shelf_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shelves_host = QWidget()
        self.shelves_host.setObjectName("shellShelves")
        self.shelves_layout = QVBoxLayout(self.shelves_host)
        self.shelves_layout.setContentsMargins(12, 10, 12, 10)
        self.shelves_layout.setSpacing(10)
        self.shelf_scroll.setWidget(self.shelves_host)
        self.grid_stack.addWidget(self.shelf_scroll)
        left.addWidget(self.grid_stack, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("shellCount")
        left.addWidget(self.count_label)
        main.addLayout(left, 2)

        rail = QVBoxLayout()
        rail.setSpacing(10)
        # V13: the Today section sits above the Briefing in the
        # rail — a compact card of actionable rows (next event,
        # due tasks, unread mail) that hides entirely when
        # there is nothing to act on. Rows are rebuilt by
        # refresh_today; the frame is built once here.
        self.today_frame = QFrame()
        self.today_frame.setObjectName("shellCard")
        # V16: Today stays compact by policy — Maximum
        # vertical policy means natural height only, so every
        # spare pixel in the rail belongs to the Briefing.
        self.today_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum)
        today_lay = QVBoxLayout(self.today_frame)
        today_lay.setContentsMargins(14, 10, 14, 10)
        today_lay.setSpacing(6)
        today_head = QLabel("TODAY")
        today_head.setObjectName("shellCardTitle")
        today_lay.addWidget(today_head)
        self.today_rows = QVBoxLayout()
        self.today_rows.setSpacing(6)
        today_lay.addLayout(self.today_rows)
        self.today_frame.hide()
        rail.addWidget(self.today_frame)
        # V4: the Briefing card IS the rail — it takes all the
        # vertical space the three V2/V3 cards used, and its body
        # is the Briefing tab's full text in a view that fills
        # the card. Now Playing and Status are gone entirely.
        self.briefing_frame, self.briefing_body = \
            self._make_briefing_card()
        rail.addWidget(self.briefing_frame, 1)
        # The rail is Briefing ONLY (V4 addendum): the Shell Mode
        # boot toggle moved to Settings → General, its one home —
        # no checkbox here anymore.
        main.addLayout(rail, 1)
        root.addLayout(main, 1)

        # -- action cards (V11): approvals + reminders ----------
        # Painted only while something is actually pending; the
        # frames start hidden and refresh_actions (on the tick)
        # owns their visibility — nothing pending, no cards,
        # zero visual change from V10.
        self.approval_frame = self._make_action_card(
            "APPROVAL NEEDED")
        self.approval_text = self.approval_frame.findChild(
            QLabel, "shellActionText")
        approve_row = QHBoxLayout()
        approve_row.addStretch(1)
        self.approve_btn = QPushButton("Approve")
        self.approve_btn.setObjectName("shellActionBtn")
        self.approve_btn.clicked.connect(
            lambda: self._resolve_approval(True))
        approve_row.addWidget(self.approve_btn)
        self.deny_btn = QPushButton("Deny")
        self.deny_btn.setObjectName("shellActionBtn")
        self.deny_btn.clicked.connect(
            lambda: self._resolve_approval(False))
        approve_row.addWidget(self.deny_btn)
        self.approval_frame.layout().addLayout(approve_row)
        root.addWidget(self.approval_frame)

        self.reminder_frame = self._make_action_card("REMINDER")
        self.reminder_text = self.reminder_frame.findChild(
            QLabel, "shellActionText")
        gotit_row = QHBoxLayout()
        gotit_row.addStretch(1)
        self.gotit_btn = QPushButton("Got it")
        self.gotit_btn.setObjectName("shellActionBtn")
        self.gotit_btn.clicked.connect(self._ack_reminder)
        gotit_row.addWidget(self.gotit_btn)
        self.reminder_frame.layout().addLayout(gotit_row)
        root.addWidget(self.reminder_frame)

        # -- command palette dropdown (V12) ----------------------
        # An in-flow list directly above the dock: candidates
        # for what he's typing (apps / files / tabs / actions),
        # hidden unless there is something to offer.
        self.palette = QListWidget()
        self.palette.setObjectName("shellPalette")
        self.palette.setMaximumHeight(264)
        self.palette.itemClicked.connect(
            self._activate_palette_item)
        self.palette.hide()
        root.addWidget(self.palette)

        # -- bottom dock: the command pill, full width -----------
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.dock = QFrame()
        self.dock.setObjectName("shellDock")
        dock_lay = QHBoxLayout(self.dock)
        dock_lay.setContentsMargins(16, 2, 16, 2)
        self.dock_orb = OrbDisc(self.dock, diameter=18,
                                colors=_orb_colors(self._accent))
        dock_lay.addWidget(self.dock_orb)
        self.command = QLineEdit()
        self.command.setPlaceholderText(
            "Tell ARIA something, or type an app name to launch it…")
        self.command.setObjectName("shellCommand")
        self.command.returnPressed.connect(self._submit_command)
        # V12: typing refreshes the palette; an event filter
        # gives it Up/Down/Enter/Esc ahead of the line edit.
        self.command.textChanged.connect(self._refresh_palette)
        self.command.installEventFilter(self)
        dock_lay.addWidget(self.command, 1)
        bottom.addWidget(self.dock, 1)
        root.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(20000)
        if self._apps_provider is not None and apps is None:
            self.refresh_apps()
        self.refresh_clock()
        self.refresh_cards()
        self.refresh_actions()
        self.refresh_today()
        self.refresh_today_remote()
        self.refresh_weather()
        self._refilter()

    # -- construction helpers -----------------------------------
    def _make_briefing_card(self):
        """The rail's one card (V4): title + a read-only text
        view that FILLS the card. Same widget family as the
        Workspace Briefing tab (a plain-text view), word wrap on,
        card-coloured background via its stylesheet rule — the
        view owns its surface, so the V3 white-slab failure mode
        (a label inheriting the light app palette) cannot recur.
        A scrollbar appears only when a briefing outgrows even
        this full-rail card; a typical one fits whole."""
        frame = QFrame()
        frame.setObjectName("shellCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        head = QLabel("BRIEFING")
        head.setObjectName("shellCardTitle")
        lay.addWidget(head)
        body = QPlainTextEdit()
        body.setObjectName("shellBriefingText")
        # V16: a floor under the reading area — however tight
        # the window gets, the briefing never shrinks below a
        # readable block again.
        body.setMinimumHeight(140)
        body.setReadOnly(True)
        body.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        body.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        lay.addWidget(body, 1)
        frame.setSizePolicy(QSizePolicy.Policy.Preferred,
                            QSizePolicy.Policy.Expanding)
        self.briefing_text = body
        return frame, body

    def _make_action_card(self, title):
        """One V11 action card frame: a title + a wrapping text
        label (objectName shellActionText, found by the caller
        to paint the live content). Starts hidden — cards exist
        on screen only while their content does."""
        frame = QFrame()
        frame.setObjectName("shellActionCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("shellActionTitle")
        lay.addWidget(head)
        text = QLabel("")
        text.setObjectName("shellActionText")
        text.setWordWrap(True)
        lay.addWidget(text)
        frame.hide()
        return frame

    def _icon_for(self, app):
        tile = shell_home.tile_model(app)
        if sys.platform == "win32" and tile["path"]:
            try:
                if self._icon_provider is None:
                    self._icon_provider = QFileIconProvider()
                icon = self._icon_provider.icon(QFileInfo(tile["path"]))
                if icon is not None and not icon.isNull():
                    return icon
            except Exception:
                pass
        return _letter_icon(tile, ink=_tile_ink(self._accent))

    # -- accent -------------------------------------------------
    def set_accent(self, accent):
        """Follow the dashboard's accent live (V4 addendum):
        rebuild the stylesheet from the accent family and repaint
        the dock dot. (The header waveform orb follows through
        the resonance palette instead — see __init__.) Invalid
        accents are ignored — a bad colour can never cost the
        shell its theme. Called by the dashboard's apply_accent
        (Customize) and on Shell Home open, so a colour changed
        while the shell was closed shows the moment it opens,
        no restart."""
        accent = _valid_accent(accent)
        if not accent:
            return
        self._accent = accent
        self.setStyleSheet(shell_style(accent))
        # V9: only the dock dot (still an OrbDisc) takes the
        # shell family's colours here. The header orb is the
        # ResonanceOrb — its palette lives in aria.resonance
        # and is shifted by the dashboard's
        # _apply_painted_accent on this same Customize path,
        # so there is nothing panel-side to repaint for it.
        self.dock_orb.set_colors(*_orb_colors(accent))
        # V10: repaint the grid too — the letter tiles' glyph
        # ink comes from the shifted map, so a live accent
        # change re-inks them with everything else.
        try:
            self._refilter()
        except Exception:
            pass

    # -- the header waveform orb (V9) ----------------------------
    def advance_orb(self, state, level, muted=False):
        """Drive the header waveform orb: the dashboard's
        render_once forwards the SAME (state, level, muted)
        triple it computed for its own face, and only while
        Shell Home is active — one source, two faces, no second
        timer or level computation here. Fail-soft: a bad tick
        can never cost the shell its frame loop."""
        try:
            self.orb.advance(state, level, muted)
        except Exception:
            pass

    # -- data -------------------------------------------------
    def refresh_apps(self):
        """Re-enumerate through the provider (if any) and repaint."""
        if self._apps_provider is not None:
            try:
                self._apps = list(self._apps_provider() or [])
            except Exception:
                self._apps = []
        self._refilter()

    def refresh_clock(self):
        model = shell_home.home_model(self._apps,
                                      now=self._now_provider())
        self.clock_label.setText(model["clock"])
        self.greeting_label.setText(model["greeting"])
        self.date_label.setText(model["date_line"])
        # The count line is NOT set here (V8): it belongs to the
        # grid's current view (pinned / all / search), which
        # _refilter owns — a clock tick must not repaint it with
        # the full-list total.

    def refresh_cards(self):
        """Repaint the rail's one card (V4): the Briefing tab's
        exact text — report_text of the latest briefing plus the
        guarded research append, composed by the pure helper.
        Fail-soft: a broken briefing seam reads as the None
        report (report_text's placeholder), and the research
        seam is guarded inside its provider."""
        try:
            report = self._briefing_provider()
        except Exception:
            report = None
        try:
            research = self._research_provider() or ""
        except Exception:
            research = ""
        self.briefing_body.setPlainText(
            shell_home.briefing_full_text(report, research))

    # -- action cards (V11) --------------------------------------
    def refresh_actions(self):
        """Repaint the action cards from the real seams: the
        oldest pending approval (same one the dashboard banner
        shows) and any delivered reminder awaiting
        acknowledgement. Pure helpers decide card content; this
        only paints and toggles visibility. Fail-soft: a broken
        seam reads as nothing pending — the cards hide rather
        than guess."""
        try:
            entries = self._approval_provider() or []
        except Exception:
            entries = []
        card = shell_home.approval_card(entries)
        self._approval_token = card["token"] if card else None
        if card:
            text = card["desc"] or f"Run {card['tool']}?"
            if card["pending"] > 1:
                text += f"  (+{card['pending'] - 1} more waiting)"
            self.approval_text.setText(text)
            self.approval_frame.show()
        else:
            self.approval_frame.hide()
        try:
            cards = self._reminder_provider() or []
        except Exception:
            cards = []
        rcard = shell_home.reminder_card(cards)
        if rcard:
            text = rcard["message"]
            if rcard["count"] > 1:
                text += f"  (+{rcard['count'] - 1} more)"
            self.reminder_text.setText(text)
            self.reminder_frame.show()
        else:
            self.reminder_frame.hide()

    def _resolve_approval(self, ok):
        """Approve/Deny for the painted card: the token captured
        at paint time goes to the resolver seam (default: the
        dashboard banner's own approve_token / deny_token path).
        A card whose request already resolved elsewhere is a
        no-op there — approve_token/deny_token report unknown
        tokens without executing anything."""
        token = self._approval_token
        if token is None:
            return
        try:
            self._approval_resolver(token, bool(ok))
        except Exception:
            pass
        self.refresh_actions()

    def _ack_reminder(self):
        """The reminder card's 'Got it': the acknowledgement
        seam (default: scheduler.tool_acknowledge_reminders,
        the voice tool's path — clears the gate store and the
        inbox card files together)."""
        try:
            self._reminder_ack()
        except Exception:
            pass
        self.refresh_actions()

    # -- Today cards (V13) ---------------------------------------
    def refresh_today(self):
        """Rebuild the Today rows from the seams: tasks read
        live (local), event/unread from the last off-thread
        remote fetch. The pure today_cards helper decides what
        exists; an empty model hides the whole frame. Fail-soft
        throughout — a broken seam shrinks Today, never breaks
        the shell."""
        try:
            tasks = self._tasks_provider() or []
        except Exception:
            tasks = []
        model = shell_home.today_cards(
            self._now_provider(), tasks,
            self._today_remote.get("event"),
            self._today_remote.get("unread"))
        self._today_model = model
        while self.today_rows.count():
            item = self.today_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if model["event"]:
            ev = model["event"]
            self.today_rows.addWidget(self._today_row(
                f"{ev['when']} — {ev['summary']}",
                [("Open", lambda: self._jump_tab("Calendar"))]))
        for task in model["tasks"]:
            buttons = []
            # clicked(bool) would otherwise land in the lambda's
            # first slot and eat the captured id — the dashboard's
            # own checked=False idiom guards it.
            if "done" in task["actions"]:
                buttons.append(
                    ("Done", lambda _c=False, tid=task["id"]:
                     self._task_clicked(tid, "done")))
            if "snooze" in task["actions"]:
                buttons.append(
                    ("Snooze +1h", lambda _c=False, tid=task["id"]:
                     self._task_clicked(tid, "snooze")))
            self.today_rows.addWidget(self._today_row(
                f"{task['prompt']} · {task['when']}", buttons))
        if model["unread"]:
            n = model["unread"]
            self.today_rows.addWidget(self._today_row(
                f"{n} unread email{'s' if n != 1 else ''}",
                [("Open", lambda: self._jump_tab("Inbox"))]))
        self.today_frame.setVisible(not model["empty"])

    def _today_row(self, text, buttons):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        label = QLabel(text)
        label.setObjectName("shellActionText")
        label.setWordWrap(True)
        # V16: the label absorbs ALL horizontal compression —
        # Ignored policy + zero minimum, wrapping as it goes —
        # so a tight rail squeezes the text, never the buttons
        # (Alek's screenshot: Open buttons cut mid-word).
        label.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        label.setMinimumWidth(0)
        lay.addWidget(label, 1)
        for caption, fn in buttons:
            btn = QPushButton(caption)
            btn.setObjectName("shellActionBtn")
            # V16: full fixed size — the layout may not shrink
            # a button below its size hint into its own text.
            btn.setSizePolicy(QSizePolicy.Policy.Fixed,
                              QSizePolicy.Policy.Fixed)
            btn.clicked.connect(fn)
            lay.addWidget(btn)
        return row

    def _jump_tab(self, name):
        """Today's Open buttons ride the V12 tab callback —
        the dashboard exits Shell Home and selects the tab."""
        if self._on_open_tab is not None:
            try:
                self._on_open_tab(name)
            except Exception:
                pass

    def _task_clicked(self, tid, action):
        try:
            self._task_actor(tid, action)
        except Exception:
            pass
        self.refresh_today()

    def refresh_today_remote(self):
        """Kick the throttled off-thread Today fetch (calendar
        + mail — both network), same pattern and throttle as
        the weather fetch: at most once per
        WEATHER_REFETCH_SECONDS, never two in flight."""
        now = self._time_provider()
        if self._today_fetched_at is not None and \
                now - self._today_fetched_at < \
                WEATHER_REFETCH_SECONDS:
            return False
        if self._today_thread is not None and \
                self._today_thread.is_alive():
            return False
        self._today_fetched_at = now

        def work():
            try:
                data = self._today_remote_provider()
            except Exception:
                data = None
            if pyqtSignal is not None:
                try:
                    self._today_ready.emit(data or {})
                except Exception:
                    pass

        self._today_thread = threading.Thread(
            target=work, daemon=True, name="shell-today")
        self._today_thread.start()
        return True

    def _apply_today_remote(self, data):
        """UI-thread slot for the Today fetch: keep whatever
        arrived (missing keys keep their previous values only
        when the fetch itself failed wholesale — a successful
        empty fetch honestly clears), then repaint."""
        if isinstance(data, dict) and data:
            self._today_remote = {
                "event": data.get("event"),
                "unread": data.get("unread")}
        self.refresh_today()

    # -- weather (V6) -------------------------------------------
    def refresh_weather(self):
        """Kick an off-thread weather fetch, throttled: at most
        one fetch per WEATHER_REFETCH_SECONDS, and never a
        second while one is already in flight. The gate stamps
        the fetch START, so a slow or failed fetch also waits
        out the window before retrying. Returns True when a
        fetch was started. The provider runs on a daemon thread
        (it blocks on the network); the result re-enters the UI
        thread through the _weather_ready signal."""
        now = self._time_provider()
        if self._weather_fetched_at is not None and \
                now - self._weather_fetched_at < \
                WEATHER_REFETCH_SECONDS:
            return False
        if self._weather_thread is not None and \
                self._weather_thread.is_alive():
            return False
        self._weather_fetched_at = now

        def work():
            try:
                data = self._weather_provider()
            except Exception:
                data = None
            line = shell_home.weather_line(data)
            symbol = shell_home.weather_symbol(
                (data or {}).get("desc"))
            if pyqtSignal is not None:
                try:
                    self._weather_ready.emit(line, symbol)
                except Exception:
                    pass  # panel already gone; nobody to paint

        self._weather_thread = threading.Thread(
            target=work, daemon=True, name="shell-weather")
        self._weather_thread.start()
        return True

    def _apply_weather(self, line, symbol):
        """UI-thread slot for the fetched weather: paint the
        line + symbol, or emptiness when there is no data —
        the labels carry no placeholder text, ever."""
        self.weather_label.setText(line or "")
        self.weather_symbol_label.setText(symbol or "")

    def _tick(self):
        self.refresh_clock()
        self.refresh_cards()
        self.refresh_actions()
        self.refresh_today()
        self.refresh_today_remote()
        self.refresh_weather()

    # -- pins (V8) ----------------------------------------------
    def _pinned_paths(self):
        """The current pin list through the provider seam,
        fail-soft to [] — a broken prefs read shows the empty
        state, never a crash."""
        try:
            return list(self._pinned_provider() or [])
        except Exception:
            return []

    def pin_action_label(self, app):
        """The right-click menu's verb for *app*: a pinned app
        offers removal, an unpinned one offers the pin."""
        if shell_home.is_pinned(self._pinned_paths(),
                                app_launcher.app_key(app or {})):
            return "Remove from Home"
        return "Pin to Home"

    def toggle_pin(self, app):
        """Pin an unpinned app / unpin a pinned one (the menu's
        one action): the pure toggle decides the new list, the
        writer seam persists it, and the grid re-renders — in
        the pinned view an unpin removes the tile at once, in
        All Apps / search the tile stays (it's enumerated).
        Pin identity is the app KEY (V17): the .lnk path, or
        appid:<AppID> for an AppsFolder app."""
        path = app_launcher.app_key(app or {})
        if not path:
            return
        new_paths = shell_home.toggle_pin(self._pinned_paths(), path)
        try:
            self._pinned_writer(new_paths)
        except Exception:
            pass
        self._refilter()

    def _pin_menu(self, app):
        """The tile's right-click menu: Pin/Remove (V8), plus
        — for a pinned app — the arrangement actions (Move
        left / Move right — V17-B names for the V14 moves —
        and Move to group ▸ with the existing groups,
        Ungrouped, and New group…). Built separately from the
        exec so tests can inspect and trigger it without a
        modal grab."""
        menu = QMenu(self)
        action = menu.addAction(self.pin_action_label(app))
        action.triggered.connect(lambda: self.toggle_pin(app))
        path = app_launcher.app_key(app or {})
        if path and shell_home.is_pinned(self._pinned_paths(),
                                         path):
            menu.addSeparator()
            # V17-B: the shelves (and the grid) flow
            # left-to-right, so the moves are named for the
            # direction they move the tile.
            left = menu.addAction("Move left")
            left.triggered.connect(
                lambda _c=False: self.move_pin(app, -1))
            right = menu.addAction("Move right")
            right.triggered.connect(
                lambda _c=False: self.move_pin(app, 1))
            sub = menu.addMenu("Move to group")
            for name in self._group_names():
                act = sub.addAction(name or "Ungrouped")
                act.triggered.connect(
                    lambda _c=False, g=name:
                    self.move_pin_to_group(app, g))
            sub.addSeparator()
            newg = sub.addAction("New group…")
            newg.triggered.connect(
                lambda _c=False: self.move_pin_to_new_group(app))
        return menu

    def _on_context_menu(self, pos):
        item = self.app_list.itemAt(pos)
        if item is None:
            return
        app = item.data(Qt.ItemDataRole.UserRole)
        if app is None or (isinstance(app, dict)
                           and "group_header" in app):
            return  # group headers are inert — no menu
        self.app_list.setCurrentItem(item)
        self._pin_menu(app).exec(
            self.app_list.viewport().mapToGlobal(pos))

    # -- pin groups + reorder (V14) ------------------------------
    def _pin_groups(self):
        """The current {path: group} map through the provider
        seam, fail-soft to {} — a broken prefs read means
        ungrouped, never a crash."""
        try:
            return dict(self._pin_groups_provider() or {})
        except Exception:
            return {}

    def _pin_entries(self):
        return shell_home.pin_entries(self._pinned_paths(),
                                      self._pin_groups())

    def _group_names(self):
        """Group names in display order for the menu, with ''
        (Ungrouped) always present exactly once."""
        names = [g for g, _m in
                 shell_home.grouped_entries(self._pin_entries())]
        if "" not in names:
            names.insert(0, "")
        return names

    def _write_entries(self, entries):
        """Persist an entry list through BOTH seams: order via
        the V8 paths writer, groups via the groups writer
        (set_pinned_apps also prunes orphaned groups)."""
        try:
            self._pinned_writer([e["path"] for e in entries])
        except Exception:
            pass
        try:
            self._pin_groups_writer(
                {e["path"]: e["group"] for e in entries
                 if e["group"]})
        except Exception:
            pass

    def move_pin(self, app, delta):
        """Move left / Move right (V17-B; was Move up/down):
        the pure IN-GROUP move over the current entries —
        a tile slides along its shelf and never crosses into
        the neighbouring group — persisted, repainted."""
        path = app_launcher.app_key(app or {})
        if not path:
            return
        self._write_entries(shell_home.move_entry_in_group(
            self._pin_entries(), path, delta))
        self._refilter()

    def move_pin_to_group(self, app, group):
        """Move to group ▸: the pure regroup, persisted,
        repainted."""
        path = app_launcher.app_key(app or {})
        if not path:
            return
        self._write_entries(shell_home.set_entry_group(
            self._pin_entries(), path, group))
        self._refilter()

    def move_pin_to_new_group(self, app):
        """New group…: prompt for the name, then the same move.
        An empty/cancelled prompt changes nothing."""
        name, ok = QInputDialog.getText(
            self, "New group", "Group name:")
        if ok and str(name or "").strip():
            self.move_pin_to_group(app, str(name).strip())

    def _on_rows_moved(self, *_args):
        """A drag finished in the pinned view: capture the new
        order (and group membership, from header positions)
        off the painted items and persist it. Programmatic
        rebuilds are guarded out by _rebuilding."""
        if self._rebuilding or self._view_all or \
                (self.search.text() or "").strip():
            return
        walk = []
        for i in range(self.app_list.count()):
            data = self.app_list.item(i).data(
                Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and "group_header" in data:
                walk.append(("header", data["group_header"]))
            elif isinstance(data, dict):
                walk.append(("app", app_launcher.app_key(data)))
        self._write_entries(shell_home.entries_from_grid(walk))

    def set_view_all(self, show_all):
        """Switch the grid's base view (Pinned <-> All Apps)
        and re-render. Search, while text is present, overrides
        the base view either way."""
        self._view_all = bool(show_all)
        self._refilter()

    def _toggle_view(self):
        self.set_view_all(not self._view_all)

    def _sync_view_toggle(self):
        self.view_toggle.setText(
            "Pinned" if self._view_all else "All apps")

    # -- interactions ------------------------------------------
    def _paint_grid(self, view, results, entries, grouped):
        """Populate the grid area for one _refilter pass.

        Ungrouped (every view except a grouped pinned home):
        the V8 icon grid in app_list, byte-for-byte behaviour.
        Grouped pinned home (V17-B): group SHELVES in the
        scroll page — one shelf per group, tiles flowing
        left-to-right (see _paint_shelves). V14's vertical
        list rendering is gone; Alek's words were that the
        groups should run across, not down. The pinned view
        (either rendering) supports drag reorder; search and
        All Apps do not — arrangement is a home-view act.
        """
        if grouped:
            self._paint_shelves(results, entries)
            return
        lst = self.app_list
        lst.clear()
        if view == "pinned":
            lst.setDragDropMode(
                QAbstractItemView.DragDropMode.InternalMove)
            lst.setMovement(QListWidget.Movement.Snap)
        else:
            lst.setDragDropMode(
                QAbstractItemView.DragDropMode.NoDragDrop)
            lst.setMovement(QListWidget.Movement.Static)
        lst.setViewMode(QListWidget.ViewMode.IconMode)
        lst.setUniformItemSizes(True)
        lst.setGridSize(QSize(120, 122))
        for app in results:
            item = QListWidgetItem(self._icon_for(app),
                                   app["name"])
            item.setData(Qt.ItemDataRole.UserRole, app)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # V8 capture probe: with uniformItemSizes and no
            # explicit hint, a cell's rect came out sized by
            # the FIRST item's content (81px in one view,
            # 99px in another) — narrow enough that two-line
            # names elided instead of wrapping. Pinning every
            # item's hint to the grid size makes cell geometry
            # fixed, independent of which app a view happens
            # to lead with (the pinned view's leader is pin
            # order, so this mattered most exactly there).
            item.setSizeHint(lst.gridSize())
            lst.addItem(item)

    # -- group shelves (V17-B) -----------------------------------
    def _make_shelf_list(self, group):
        """One group's tile shelf: the V8 grid's own geometry
        and styling (objectName shellAppList — the same sheet
        rules), scoped to the group. Drag is DragDrop: model
        moves within the shelf (rowsMoved persists), drops
        from sibling shelves arrive through on_cross_drop."""
        lst = _ShelfList(self.shelves_host)
        lst.group = group
        lst.setObjectName("shellAppList")
        lst.setViewMode(QListWidget.ViewMode.IconMode)
        lst.setIconSize(QSize(48, 48))
        lst.setGridSize(QSize(120, 122))
        lst.setWordWrap(True)
        lst.setResizeMode(QListWidget.ResizeMode.Adjust)
        lst.setMovement(QListWidget.Movement.Snap)
        lst.setUniformItemSizes(True)
        lst.setDragDropMode(
            QAbstractItemView.DragDropMode.DragDrop)
        lst.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.itemActivated.connect(self._activate_item)
        lst.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        lst.customContextMenuRequested.connect(
            lambda pos, l=lst: self._on_shelf_context_menu(l, pos))
        try:
            lst.model().rowsMoved.connect(
                lambda *_a: self._on_shelf_rows_moved())
        except Exception:
            pass
        lst.on_cross_drop = self._cross_shelf_drop
        return lst

    def _paint_shelves(self, results, entries):
        """Rebuild the shelf page from the pin entries: a
        header label + tile shelf per group, in group order.
        The shelves are rebuilt per render (like the grid's
        items) — drag state lives in the persisted model,
        never in the widgets."""
        while self.shelves_layout.count():
            item = self.shelves_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent IMMEDIATELY, not just deleteLater:
                # a deferred delete left the previous render's
                # shelves alive as orphan children at their
                # first-render geometry, painting through the
                # new shelves (caught in the V17 magenta
                # capture — a ghost column of stale tiles).
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._shelf_lists = []
        by_key = {app_launcher.app_key(a): a for a in results}
        for group, members in shell_home.pin_sections(entries):
            head = QLabel(group or "Pinned")
            head.setObjectName("shellGroupHeader")
            self.shelves_layout.addWidget(head)
            lst = self._make_shelf_list(group)
            for entry in members:
                app = by_key.get(entry["path"])
                if app is None:
                    continue
                item = QListWidgetItem(self._icon_for(app),
                                       app["name"])
                item.setData(Qt.ItemDataRole.UserRole, app)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setSizeHint(lst.gridSize())
                lst.addItem(item)
            self.shelves_layout.addWidget(lst)
            self._shelf_lists.append((group, lst))
        self.shelves_layout.addStretch(1)
        QTimer.singleShot(0, self._fit_shelf_heights)

    def _fit_shelf_heights(self):
        """Each shelf is exactly as tall as its wrapped rows:
        rows = ceil(tiles / per-row), per-row from the shelf's
        live viewport width and the fixed cell width. The
        scroll area owns any overflow — a shelf never grows a
        scrollbar of its own.

        The width comes from the SCROLL AREA's viewport (the
        shelf lists' own widths lag a layout pass behind on a
        fresh render — fitting against them once froze a
        100px-wide measurement in as a 4-row shelf)."""
        try:
            width = self.shelf_scroll.viewport().width() - 24
        except Exception:
            width = 0
        for _group, lst in self._shelf_lists:
            try:
                n = lst.count()
                cell = lst.gridSize()
                w = width or lst.viewport().width() \
                    or lst.width()
                per_row = max(1, w // max(1, cell.width())) \
                    if w else 1
                rows = max(1, -(-n // per_row))
                lst.setFixedHeight(rows * cell.height() + 4)
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_shelf_heights()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_shelf_heights()

    def _shelf_sections_walk(self):
        """[(group, [pin keys in painted shelf order])] — the
        shelf twin of the V14 grid walk, feeding the pure
        entries_from_shelves rebuild."""
        sections = []
        for group, lst in self._shelf_lists:
            keys = []
            for i in range(lst.count()):
                app = lst.item(i).data(Qt.ItemDataRole.UserRole)
                if isinstance(app, dict):
                    keys.append(app_launcher.app_key(app))
            sections.append((group, keys))
        return sections

    def _on_shelf_rows_moved(self, *_args):
        """A within-shelf drag finished: persist the painted
        order (groups are the shelves themselves, so only
        order can have changed). Same guards as the V14 hook:
        no persistence during rebuilds or outside the pinned
        home view."""
        if self._rebuilding or self._view_all or \
                (self.search.text() or "").strip():
            return
        self._write_entries(shell_home.entries_from_shelves(
            self._shelf_sections_walk()))

    def _cross_shelf_drop(self, key, group, row):
        """A tile dropped onto another group's shelf (or a
        different spot of its own via the mime path): the pure
        section move decides the new entries, both seams
        persist them, and the shelves repaint from the model."""
        if self._view_all or (self.search.text() or "").strip():
            return
        self._write_entries(shell_home.move_entry_to_section(
            self._pin_entries(), key, group, row))
        self._refilter()

    def _on_shelf_context_menu(self, lst, pos):
        """Right-click on a shelf tile: the same pin menu as
        the grid's (Pin/Remove, Move left/right, Move to
        group) — arrangement acts on the model, so it does
        not matter which list painted the tile."""
        item = lst.itemAt(pos)
        if item is None:
            return
        app = item.data(Qt.ItemDataRole.UserRole)
        if app is None:
            return
        lst.setCurrentItem(item)
        self._pin_menu(app).exec(
            lst.viewport().mapToGlobal(pos))

    def _refilter(self):
        query = self.search.text() if hasattr(self, "search") else ""
        query = (query or "").strip()
        entries = []
        grouped = False
        if query:
            # Search ALWAYS searches everything (V8) — even
            # from the pinned view — and clearing the box
            # returns to whichever base view is current.
            view = "search"
            results = app_launcher.search_apps(self._apps, query,
                                               limit=200)
        elif self._view_all:
            view = "all"
            results = list(self._apps)
        else:
            view = "pinned"
            entries = shell_home.pin_entries(
                self._pinned_paths(), self._pin_groups())
            results = shell_home.pinned_view(
                self._apps, [e["path"] for e in entries])
            # V14: group headers exist only when at least one
            # pin actually carries a group — an all-ungrouped
            # home renders exactly the V8 grid.
            grouped = any(e["group"] for e in entries)
        self._rebuilding = True
        try:
            self._paint_grid(view, results, entries, grouped)
        finally:
            self._rebuilding = False
        # Empty state (V8): the pinned view with nothing to show
        # (zero pins, or every pin stale) puts the guidance note
        # in the grid area instead of a void — the All Apps
        # toggle above stays reachable either way. V17-B: a
        # grouped pinned home shows the shelf page instead of
        # the grid.
        if view == "pinned" and not results:
            self.grid_stack.setCurrentWidget(self.empty_label)
        elif grouped:
            self.grid_stack.setCurrentWidget(self.shelf_scroll)
            self._fit_shelf_heights()
        else:
            self.grid_stack.setCurrentWidget(self.app_list)
        self._sync_view_toggle()
        self.refresh_clock()
        self.count_label.setText(shell_home.view_count_line(
            view, len(results), len(self._apps)))

    def _activate_item(self, item):
        app = item.data(Qt.ItemDataRole.UserRole)
        if app is not None and not (isinstance(app, dict)
                                    and "group_header" in app):
            self._launch(app)

    def _launch(self, app):
        # Same-app relaunch guard (V3): one physical gesture can
        # still reach this method twice through paths no signal
        # audit can fully foresee (an OS-level double activation,
        # a command-bar Enter bouncing) — a repeat of the SAME
        # app inside the guard window is bounce, not intent, and
        # is dropped. A different app, or the same app after the
        # window, launches normally.
        key = (str((app or {}).get("name", "")),
               app_launcher.app_key(app or {}))
        now = self._time_provider()
        if key == self._last_launch_key and \
                now - self._last_launch_at < LAUNCH_GUARD_SECONDS:
            return
        self._last_launch_key = key
        self._last_launch_at = now
        if self._on_launch is not None:
            self._on_launch(app)
        else:
            app_launcher.launch_app(app)

    def _submit_command(self):
        intent = shell_home.route_command(self.command.text(),
                                          self._apps)
        self._palette_suppress = True
        try:
            if intent["kind"] == "launch":
                self.command.clear()
                self._record_history(intent["app"].get("name", ""))
                self._launch(intent["app"])
            elif intent["kind"] == "chat":
                text = intent["text"]
                self.command.clear()
                if self._on_command is not None:
                    self._on_command(text)
        finally:
            self._palette_suppress = False
        self.palette.hide()

    # -- command palette (V12) -----------------------------------
    def _files(self):
        """The palette's file candidates, enumerated once and
        cached — the bounded scan runs on first use, not per
        keystroke."""
        if self._files_cache is None:
            try:
                self._files_cache = list(self._files_provider()
                                         or [])
            except Exception:
                self._files_cache = []
        return self._files_cache

    def _history(self):
        try:
            return dict(self._history_provider() or {})
        except Exception:
            return {}

    def _record_history(self, label):
        label = str(label or "").strip()
        if not label:
            return
        try:
            self._history_writer(label)
        except Exception:
            pass

    def _refresh_palette(self):
        """Rebuild the dropdown for the command bar's current
        text (pure palette_candidates decides content + order).
        Hidden when there is nothing to offer; the first
        candidate is preselected so Enter means the top pick."""
        if self._palette_suppress:
            return
        try:
            muted = bool(self._muted_provider())
        except Exception:
            muted = False
        candidates = shell_home.palette_candidates(
            self.command.text(), self._apps, self._files(),
            shell_home.DASHBOARD_TABS,
            shell_home.palette_actions(muted=muted),
            history=self._history())
        self.palette.clear()
        for cand in candidates:
            item = QListWidgetItem(
                f"{cand['label']}    · {cand['detail']}")
            item.setData(Qt.ItemDataRole.UserRole, cand)
            self.palette.addItem(item)
        if candidates:
            self.palette.setCurrentRow(0)
            self.palette.show()
        else:
            self.palette.hide()

    def _activate_palette_item(self, item):
        if item is not None:
            self._activate_candidate(
                item.data(Qt.ItemDataRole.UserRole))

    def _activate_candidate(self, cand):
        """Dispatch one palette pick by kind. App -> the
        existing direct-launch seam (relaunch guard included);
        file/folder -> the OS opener; tab -> the dashboard
        callback; action -> the dashboard action callback,
        except 'reminder', which is panel-local: it prefills
        the command bar and the finished text flows through
        the normal submit path (chat lane, gates unchanged).
        Every pick is recorded in the usage history."""
        if not isinstance(cand, dict):
            return
        self._record_history(cand.get("label", ""))
        kind = cand.get("kind")
        self._palette_suppress = True
        try:
            if kind == "app":
                self.command.clear()
                self.palette.hide()
                self._launch(cand["ref"])
            elif kind == "file":
                self.command.clear()
                self.palette.hide()
                self._file_opener(cand["ref"])
            elif kind == "tab":
                self.command.clear()
                self.palette.hide()
                if self._on_open_tab is not None:
                    self._on_open_tab(cand["ref"])
            elif kind == "action":
                if cand["ref"] == "reminder":
                    self.command.setText("Remind me to ")
                    self.palette.hide()
                    self.command.setFocus()
                else:
                    self.command.clear()
                    self.palette.hide()
                    if self._on_shell_action is not None:
                        self._on_shell_action(cand["ref"])
        finally:
            self._palette_suppress = False

    def eventFilter(self, obj, event):
        """Palette keyboard control on the command bar:
        Up/Down move the selection, Enter activates the
        highlighted candidate (consuming the key so the plain
        submit does not ALSO fire), Esc closes the dropdown.
        Anything else — and Enter with no dropdown — passes
        through untouched, so V1–V11 Enter semantics are
        exactly preserved when the palette is closed."""
        if obj is self.command and \
                event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape and \
                    not self.palette.isHidden():
                self.palette.hide()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.palette.isHidden():
                    self._refresh_palette()
                if not self.palette.isHidden() and \
                        self.palette.count():
                    row = self.palette.currentRow()
                    row += 1 if key == Qt.Key.Key_Down else -1
                    row = max(0, min(self.palette.count() - 1, row))
                    self.palette.setCurrentRow(row)
                    return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    not self.palette.isHidden():
                item = self.palette.currentItem()
                if item is not None:
                    self._activate_palette_item(item)
                    return True
        return super().eventFilter(obj, event)


