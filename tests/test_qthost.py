"""Phase 4e Qt host tests: aria.qthost's pure logic + (when PyQt6 is
importable) the real window's drop routing, key translation, and blit.

qthost imports PyQt6 lazily, so the pure surface (host selection,
URL->path extraction, drop routing) is exercised with Qt stubbed by
fakes. The Qt-widget tests no-op when PyQt6 cannot initialise in the
test environment (CI has no display/Qt) — the offscreen acceptance
render covers the real widgets where Qt exists.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_pkg = types.ModuleType("aria")
_pkg.__path__ = [os.path.join(HERE, "..", "aria")]
sys.modules["aria"] = _pkg

_config = _mod("aria.config", UI_HOST="qt")

import importlib  # noqa: E402

qthost = importlib.import_module("aria.qthost")


# ------------------------------------------------------------------- fakes
class _FakeUrl:
    def __init__(self, path):
        self._path = path

    def toLocalFile(self):  # noqa: N802 (QUrl naming)
        return self._path


class _BrokenUrl:
    def toLocalFile(self):  # noqa: N802
        raise RuntimeError("not a local file")


# ------------------------------------------------------------- pure logic
def test_select_host_matrix():
    assert qthost.select_host("qt", True) == "qt"
    assert qthost.select_host("qt", False) == "cv2"
    assert qthost.select_host("cv2", True) == "cv2"
    assert qthost.select_host("cv2", False) == "cv2"
    print("ok select_host_matrix")


def test_select_host_normalizes_request():
    assert qthost.select_host(" QT ", True) == "qt"
    assert qthost.select_host("CV2", True) == "cv2"
    assert qthost.select_host("bogus", True) == "qt"     # typo -> default
    assert qthost.select_host("bogus", False) == "cv2"   # typo, no Qt -> cv2
    assert qthost.select_host(None, True) == "qt"
    assert qthost.select_host("", False) == "cv2"
    assert qthost.normalize_host_request("Qt") == "qt"
    print("ok select_host_normalizes_request")


def test_requested_host_reads_config():
    _config.UI_HOST = "cv2"
    try:
        assert qthost.requested_host() == "cv2"
    finally:
        _config.UI_HOST = "qt"
    assert qthost.requested_host() == "qt"
    print("ok requested_host_reads_config")


def test_urls_to_local_paths():
    urls = [_FakeUrl("/tmp/a.txt"), _FakeUrl(""), _FakeUrl("/tmp/b.pdf"),
            "C:\\Users\\Allen\\c.txt", _BrokenUrl(), None]
    assert qthost.urls_to_local_paths(urls) == [
        "/tmp/a.txt", "/tmp/b.pdf", "C:\\Users\\Allen\\c.txt"]
    assert qthost.urls_to_local_paths([]) == []
    assert qthost.urls_to_local_paths(None) == []
    print("ok urls_to_local_paths")


def test_handle_dropped_urls_first_received_extras_logged():
    received, logged = [], []
    paths = qthost.handle_dropped_urls(
        [_FakeUrl("/home/alek/report.pdf"),
         _FakeUrl("C:\\Users\\Allen\\photo.png"),
         _FakeUrl("/home/alek/notes.txt")],
        receive=lambda p: received.append(p),
        log=lambda m: logged.append(m))
    assert paths == ["/home/alek/report.pdf",
                     "C:\\Users\\Allen\\photo.png",
                     "/home/alek/notes.txt"]
    assert received == ["/home/alek/report.pdf"]  # exactly the first file
    assert len(logged) == 2                        # extras named, not silent
    assert "photo.png" in logged[0] and "notes.txt" in logged[1]
    print("ok handle_dropped_urls_first_received_extras_logged")


def test_handle_dropped_urls_nothing_local():
    received, logged = [], []
    out = qthost.handle_dropped_urls(
        [_FakeUrl(""), _BrokenUrl()],
        receive=lambda p: received.append(p),
        log=lambda m: logged.append(m))
    assert out == [] and received == [] and logged == []
    print("ok handle_dropped_urls_nothing_local")


def test_qt_available_is_bool():
    assert isinstance(qthost.qt_available(), bool)
    print("ok qt_available_is_bool")


# ------------------------------------------------------- Qt widgets (real)
def _qt_ready():
    """(Qt modules) or None when PyQt6 cannot initialise here."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPixmap  # noqa: F401
        from PyQt6.QtWidgets import QApplication  # noqa: F401
        return Qt
    except Exception:
        return None


def _fake_callbacks(record):
    import numpy as np

    def render():
        record["renders"] = record.get("renders", 0) + 1
        return np.zeros((800, 1280, 3), dtype=np.uint8)

    return types.SimpleNamespace(
        render_frame=render,
        housekeeping=lambda: record.__setitem__("housekeeping", True),
        key_press=lambda *a: record.__setitem__("key", a) or False,
        mouse_press=lambda x, y, b: record.__setitem__("press", (x, y, b)),
        mouse_release=lambda x, y: record.__setitem__("release", (x, y)),
        mouse_drag=lambda x, y: record.__setitem__("drag", (x, y)),
        mouse_wheel=lambda x, y, up: record.__setitem__("wheel", (x, y, up)),
        is_running=lambda: True,
        request_stop=lambda: record.__setitem__("stop", True),
    )


def test_qt_window_drop_key_and_blit():
    Qt = _qt_ready()
    if Qt is None:
        print("ok qt_window (skipped: PyQt6 unavailable in this env)")
        return
    from PyQt6.QtCore import QEvent, QPointF, QUrl
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    record = {}
    window = qthost.create_window(_fake_callbacks(record))
    try:
        assert window.windowTitle() == "A.R.I.A. // OS"
        assert window.canvas_label.acceptDrops()

        # Blit: one tick renders through the real QImage/QPixmap path.
        window.render_once()
        assert record.get("renders") == 1 and record.get("housekeeping")
        pix = window.canvas_label.pixmap()
        assert pix is not None and not pix.isNull()
        assert pix.width() == 1280 and pix.height() == 800

        # Drop routing: first file to receive_file, extras logged by name.
        received, logged = [], []
        orig_recv, orig_log = qthost._default_receive, qthost._default_log
        qthost._default_receive = lambda p: received.append(p)
        qthost._default_log = lambda m: logged.append(m)
        try:
            out = window.canvas_label.route_drop(
                [QUrl.fromLocalFile("/tmp/first.txt"),
                 QUrl.fromLocalFile("/tmp/second.txt")])
        finally:
            qthost._default_receive, qthost._default_log = orig_recv, orig_log
        assert out == ["/tmp/first.txt", "/tmp/second.txt"]
        assert received == ["/tmp/first.txt"]
        assert len(logged) == 1 and "second.txt" in logged[0]

        # Key translation: plain char, Enter, Ctrl+U, arrow + quit flag.
        cb = _fake_callbacks(record)
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Q,
                       Qt.KeyboardModifier.NoModifier, "q")
        assert qthost._dispatch_key(cb, ev, Qt) is False
        assert record["key"][0] == ord("q") and record["key"][2] is False

        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                       Qt.KeyboardModifier.NoModifier, "\r")
        qthost._dispatch_key(cb, ev, Qt)
        assert record["key"][0] == 13

        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_U,
                       Qt.KeyboardModifier.ControlModifier, "")
        qthost._dispatch_key(cb, ev, Qt)
        assert record["key"][0] == 21 and record["key"][2] is True

        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down,
                       Qt.KeyboardModifier.NoModifier, "")
        qthost._dispatch_key(cb, ev, Qt)
        assert record["key"][8] is True  # is_down

        cb.key_press = lambda *a: True  # chain says quit
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                       Qt.KeyboardModifier.NoModifier, "")
        assert qthost._dispatch_key(cb, ev, Qt) is True
    finally:
        window.close()
        app.processEvents()
    print("ok qt_window_drop_key_and_blit")
