"""ARIA Qt host window (Merger Phase 4e).

Why this exists: the Phase 4 frame renders onto an OpenCV/numpy canvas
that the classic host shows with cv2.imshow — and cv2 windows can NEVER
receive OS file-drop events, in any version. Mark-XXXIX-OR's window is
PyQt, which receives drops natively. Phase 4e therefore changes ONLY the
window host: a PyQt6 QMainWindow displays the very same 1280x800 canvas
hud.draw_hud() produces, a QTimer drives the same per-frame update the
cv2 loop performs, and a drop lands on the same single file-entry point
(aria.frame.receive_file) the drop zone's click-to-browse already uses.
Rendering, face/bridge publishing, subsystems, and input semantics are
untouched — every mouse/key event routes to the same dispatch functions
in aria.main that the cv2 callbacks call.

Host selection (aria.config.UI_HOST / env ARIA_UI_HOST): "qt" (default)
or "cv2" (rollback). If PyQt6 is missing or Qt fails to initialise, the
caller (aria.main) falls back to the cv2 loop and surfaces the fallback
via Phase 1 — this module never swallows that decision silently:
run_qt_host raises QtHostError and select_host() makes the
missing-Qt case explicit and testable.

Callbacks interface (duck-typed, supplied by aria.main):
    render_frame() -> np.ndarray (BGR canvas)
    housekeeping() -> None          (per-frame, post-render)
    key_press(key, key_raw, is_ctrl, is_shift, is_alt,
              is_left, is_right, is_up, is_down) -> bool (True = quit)
    mouse_press(x, y, button)       button in ("left", "right")
    mouse_release(x, y)
    mouse_drag(x, y)
    mouse_wheel(x, y, up)
    is_running() -> bool
    request_stop() -> None          (clean shutdown request)

PyQt6 is imported lazily inside the Qt builders so importing this
module — and testing its pure logic — never requires Qt.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence

WINDOW_TITLE = "A.R.I.A. // OS"
FRAME_W, FRAME_H = 1280, 800
TICK_MS = 30  # matches the cv2 loop's waitKeyEx(30) cadence


class QtHostError(RuntimeError):
    """The Qt host could not start; the caller falls back to cv2."""


# ---------------------------------------------------------------- pure logic
def requested_host() -> str:
    """The configured host request (aria.config.UI_HOST)."""
    try:
        from aria import config as _config
        return str(getattr(_config, "UI_HOST", "qt") or "qt")
    except Exception:
        return "qt"


def normalize_host_request(requested: Any) -> str:
    """Only "qt" and "cv2" are real hosts; anything else means the
    default ("qt"), so a typo can never strand ARIA without a window."""
    req = str(requested or "qt").strip().lower()
    return req if req in ("qt", "cv2") else "qt"


def qt_available() -> bool:
    """True when PyQt6 imports. (Import success is not a guarantee the
    platform plugin initialises — run_qt_host covers that failure too.)"""
    try:
        import PyQt6  # noqa: F401
        return True
    except Exception:
        return False


def select_host(requested: Any, available: bool) -> str:
    """Decide the host: an explicit cv2 request always wins; qt is used
    only when requested (or defaulted) AND PyQt6 is importable."""
    if normalize_host_request(requested) == "cv2":
        return "cv2"
    return "qt" if available else "cv2"


def urls_to_local_paths(urls: Optional[Sequence[Any]]) -> List[str]:
    """QUrl-like objects (or plain strings) -> local file paths.
    Non-local URLs (empty toLocalFile()) are skipped, never guessed."""
    paths: List[str] = []
    for u in urls or []:
        p = ""
        if isinstance(u, str):
            p = u
        else:
            to_local = getattr(u, "toLocalFile", None)
            if callable(to_local):
                try:
                    p = to_local() or ""
                except Exception:
                    p = ""
        if p:
            paths.append(str(p))
    return paths


def _display_name(path: str) -> str:
    """Basename that also understands Windows separators (drops come
    from a Windows shell) — mirrors aria.frame._basename."""
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or str(path)


def _default_receive(path: str) -> str:
    from aria import frame as _frame
    return _frame.receive_file(path)


def _default_log(msg: str) -> None:
    from aria.config import add_log
    add_log(msg)


def handle_dropped_urls(urls: Optional[Sequence[Any]],
                        receive: Optional[Callable[[str], Any]] = None,
                        log: Optional[Callable[[str], None]] = None) -> List[str]:
    """Route one drop event: the first file goes to frame.receive_file
    (the exact entry point click-to-browse uses); every additional file
    is logged by name — received, named, but not staged (one staged
    file at a time), so nothing is ever silently dropped."""
    paths = urls_to_local_paths(urls)
    if not paths:
        return []
    receive = receive or _default_receive
    log = log or _default_log
    receive(paths[0])
    for extra in paths[1:]:
        log(f"File received: {_display_name(extra)} "
            "(extra file in drop — not staged)")
    return paths


# ---------------------------------------------------------------- Qt builders
def _qt_symbols():
    """Import the PyQt6 symbols the host needs, or raise QtHostError."""
    try:
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QImage, QPixmap
        from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow
    except Exception as e:
        raise QtHostError(f"PyQt6 import failed: {e}") from e
    return Qt, QTimer, QImage, QPixmap, QApplication, QLabel, QMainWindow


def create_window(callbacks):
    """Build (but do not show) the Qt host window around `callbacks`.
    Returns the QMainWindow; the canvas label is `window.canvas_label`
    and one manual frame tick is `window.render_once()` (used by the
    offscreen acceptance render). Raises QtHostError without Qt."""
    Qt, QTimer, QImage, QPixmap, _QApp, QLabel, QMainWindow = _qt_symbols()

    class _CanvasLabel(QLabel):
        """Displays the canvas; owns mouse, wheel, and drop events
        (it covers the whole window, so it is the drop target)."""

        def __init__(self):
            super().__init__()
            self.setFixedSize(FRAME_W, FRAME_H)
            self.setAcceptDrops(True)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # -- mouse: same semantic handlers the cv2 callback calls ----
        def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
            pos = event.position().toPoint()
            x, y = int(pos.x()), int(pos.y())
            if event.button() == Qt.MouseButton.LeftButton:
                callbacks.mouse_press(x, y, "left")
            elif event.button() == Qt.MouseButton.RightButton:
                callbacks.mouse_press(x, y, "right")
            self.setFocus()

        def mouseReleaseEvent(self, event):  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                callbacks.mouse_release(int(pos.x()), int(pos.y()))

        def mouseMoveEvent(self, event):  # noqa: N802
            if event.buttons() & Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                callbacks.mouse_drag(int(pos.x()), int(pos.y()))

        def wheelEvent(self, event):  # noqa: N802
            pos = event.position().toPoint()
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.pixelDelta().y()
            callbacks.mouse_wheel(int(pos.x()), int(pos.y()), delta > 0)
            event.accept()

        # -- drag & drop ----------------------------------------------
        def dragEnterEvent(self, event):  # noqa: N802
            if event.mimeData().hasUrls():
                event.acceptProposedAction()

        def dragMoveEvent(self, event):  # noqa: N802
            if event.mimeData().hasUrls():
                event.acceptProposedAction()

        def dropEvent(self, event):  # noqa: N802
            urls = event.mimeData().urls()
            if urls:
                self.route_drop(urls)
                event.acceptProposedAction()

        def route_drop(self, urls) -> List[str]:
            """The drop routing proper, callable without a QDropEvent
            (tests drive it with real QUrls)."""
            return handle_dropped_urls(urls)

    class _HostWindow(QMainWindow):
        def closeEvent(self, event):  # noqa: N802
            # Same clean shutdown as the cv2 loop's quit path: main's
            # request_shutdown flips RUNNING and logs; main's finally
            # does the teardown (resume card, window cleanup).
            try:
                callbacks.request_stop()
            finally:
                super().closeEvent(event)

        def keyPressEvent(self, event):  # noqa: N802
            if _dispatch_key(callbacks, event, Qt):
                self.close()

    window = _HostWindow()
    window.setWindowTitle(WINDOW_TITLE)
    label = _CanvasLabel()
    window.setCentralWidget(label)
    window.setFixedSize(FRAME_W, FRAME_H)
    window.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    window.canvas_label = label

    def render_once() -> None:
        """One frame tick: shutdown check, render, housekeeping, blit.
        Identical order to the cv2 loop body."""
        if not callbacks.is_running():
            window.close()
            return
        canvas = callbacks.render_frame()
        callbacks.housekeeping()
        h, w = int(canvas.shape[0]), int(canvas.shape[1])
        img = QImage(canvas.data, w, h, int(canvas.strides[0]),
                     QImage.Format.Format_BGR888)
        label.setPixmap(QPixmap.fromImage(img))

    window.render_once = render_once
    return window


def _dispatch_key(callbacks, event, Qt) -> bool:
    """Translate one QKeyEvent into main's host-agnostic key dispatch
    (the exact chain the cv2 loop feeds from waitKeyEx). Returns the
    chain's quit flag. Modifier state comes from the event itself —
    the cv2 host polls GetAsyncKeyState for the same three booleans."""
    mods = event.modifiers()
    is_ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    is_shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    is_alt = bool(mods & Qt.KeyboardModifier.AltModifier)
    qkey = event.key()
    is_left = qkey == Qt.Key.Key_Left
    is_right = qkey == Qt.Key.Key_Right
    is_up = qkey == Qt.Key.Key_Up
    is_down = qkey == Qt.Key.Key_Down

    key = 0
    key_raw = 0
    if qkey in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        key = key_raw = 13
    elif qkey == Qt.Key.Key_Escape:
        key = key_raw = 27
    elif qkey == Qt.Key.Key_Backspace:
        key = key_raw = 8
    elif qkey == Qt.Key.Key_Insert and is_shift:
        key_raw = 45  # Shift+Insert: the chain's paste clause reads raw 45
        key = 45
    elif is_ctrl and qkey == Qt.Key.Key_U:
        key = key_raw = 21  # Ctrl+U: clear typing buffer (chain reads 21)
    else:
        text = event.text() or ""
        if len(text) == 1 and text.isprintable():
            key = key_raw = ord(text)
    return bool(callbacks.key_press(key, key_raw, is_ctrl, is_shift, is_alt,
                                    is_left, is_right, is_up, is_down))


def run_qt_host(callbacks) -> int:
    """Run the Qt host until the window closes. Blocks like the cv2
    loop does. Raises QtHostError if Qt cannot initialise (no display,
    broken platform plugin) so the caller can fall back."""
    _Qt, QTimer, _QI, _QP, QApplication, _QL, _QW = _qt_symbols()
    try:
        app = QApplication.instance() or QApplication([])
        app.setApplicationName("A.R.I.A.")
        from aria.dashboard import create_dashboard, dashboard_enabled
        is_dashboard = dashboard_enabled()
        window = create_dashboard(callbacks) if is_dashboard else create_window(callbacks)
        timer = QTimer(window)
        timer.timeout.connect(window.render_once)
        timer.start(TICK_MS)
        if is_dashboard:
            # Alek, 2026-10-27: she opens FULL WINDOW — maximized
            # from the first frame, on a cold start and on every
            # self-restart alike (a plain show() left her small /
            # minimized and he had to maximize her by hand).
            # ARIA OS (2026-10-30): Shell Mode boots her as the
            # shell instead — Shell Home in front, true fullscreen.
            # Explorer is never replaced; the panel's Exit to
            # Windows drops back to this maximized dashboard over
            # the ordinary desktop.
            _shell_boot = False
            try:
                from aria import ui_prefs as _prefs
                _shell_boot = _prefs.get_shell_mode() \
                    and hasattr(window, 'open_shell_home')
            except Exception:
                _shell_boot = False
            if _shell_boot:
                window.open_shell_home()
                window.showFullScreen()
            else:
                window.showMaximized()
        else:
            window.show()
        window.raise_()
        window.activateWindow()
        return int(app.exec())
    except QtHostError:
        raise
    except Exception as e:
        raise QtHostError(f"Qt host failed to initialise: {e}") from e
