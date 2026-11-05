"""ConfirmBanner renders approval text as plain text, never rich text.

ChatGPT x ARIA loop pass 22: a shell command under review can itself
contain HTML-like markup. Qt's QLabel default (AutoText) interprets
such markup, so an HTML comment inside a command hides the code it
surrounds from the banner while approve_token still replays the full
stored arguments — the user approves code they cannot see.

This suite pins the presentation boundary: title and detail labels
carry Qt.TextFormat.PlainText and their text is preserved verbatim,
so markup renders literally. PyQt6 is absent on the Linux host, so
when the real Qt import fails a minimal stub stands in for it — the
stub's AutoText path mimics Qt's tag/comment stripping, and the
PlainText path returns the text literally, so the test exercises the
real ConfirmBanner.__init__ code either way. When PyQt6 IS present
(Alek's Windows machine) the real Qt classes are used instead.
"""
import os
import re
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PyQt6  # noqa: F401
    _HAS_QT = True
except ImportError:
    _HAS_QT = False

if not _HAS_QT:
    # ---- Minimal PyQt6 stub (Linux host only) ----
    class _TextFormat:
        PlainText = 0
        RichText = 1
        AutoText = 2
        MarkdownText = 3

    class _WidgetAttribute:
        WA_StyledBackground = 1

    class _ScrollBarPolicy:
        ScrollBarAlwaysOff = 1

    class _CursorShape:
        PointingHandCursor = 1

    class _Qt:
        TextFormat = _TextFormat
        WidgetAttribute = _WidgetAttribute
        ScrollBarPolicy = _ScrollBarPolicy
        CursorShape = _CursorShape

    class _Signal:
        def __init__(self, *a):
            self._slots = []

        def connect(self, fn):
            self._slots.append(fn)

        def emit(self, *a):
            for fn in self._slots:
                fn(*a)

    def _pyqtSignal(*a):
        return _Signal()

    class _Font:
        class Weight:
            Bold = 75

        def __init__(self, *a):
            pass

    class _Widget:
        def __init__(self, parent=None):
            self._parent = parent
            self._w = 0
            self._h = 0

        def width(self):
            return self._w

        def height(self):
            return self._h

        def resize(self, w, h):
            self._w, self._h = w, h

        def show(self):
            pass

        def close(self):
            pass

        def update(self, *a):
            pass

        def parentWidget(self):
            return self._parent

        def geometry(self):
            return None

        def setAttribute(self, *a):
            pass

        def setStyleSheet(self, *a):
            pass

        def setFixedWidth(self, w):
            self._w = w

        def setMaximumHeight(self, h):
            self._h = h

        def adjustSize(self):
            pass

    class _Layout:
        def __init__(self, parent=None):
            pass

        def addWidget(self, *a):
            pass

        def addLayout(self, *a):
            pass

        def setContentsMargins(self, *a):
            pass

        def setSpacing(self, *a):
            pass

    class _Label(_Widget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self._text = text
            self._format = _TextFormat.AutoText

        def setText(self, text):
            self._text = text

        def text(self):
            return self._text

        def setTextFormat(self, fmt):
            self._format = fmt

        def textFormat(self):
            return self._format

        def setWordWrap(self, *a):
            pass

        def setFont(self, *a):
            pass

    class _Button(_Widget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self.clicked = _Signal()

        def setFixedHeight(self, *a):
            pass

        def setMinimumWidth(self, *a):
            pass

        def setFont(self, *a):
            pass

        def setCursor(self, *a):
            pass

        def setDefault(self, *a):
            pass

        def setFocus(self, *a):
            pass

        def click(self):
            self.clicked.emit()

    class _Frame(_Widget):
        class Shape:
            NoFrame = 0

    class _ScrollArea(_Widget):
        def setWidgetResizable(self, *a):
            pass

        def setFrameShape(self, *a):
            pass

        def setHorizontalScrollBarPolicy(self, *a):
            pass

        def setWidget(self, *a):
            pass

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    sys.modules["PyQt6"] = _mod("PyQt6")
    sys.modules["PyQt6.QtCore"] = _mod(
        "PyQt6.QtCore", Qt=_Qt, pyqtSignal=_pyqtSignal)
    sys.modules["PyQt6.QtGui"] = _mod("PyQt6.QtGui", QFont=_Font)
    sys.modules["PyQt6.QtWidgets"] = _mod(
        "PyQt6.QtWidgets", QFrame=_Frame, QHBoxLayout=_Layout,
        QLabel=_Label, QPushButton=_Button, QScrollArea=_ScrollArea,
        QVBoxLayout=_Layout, QWidget=_Widget)

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules["aria"] = pkg


def _rendered(label):
    """What the user actually sees, mimicking Qt's text formats.

    PlainText: literal. AutoText with markup: Qt renders it as rich
    text, so tags and comments vanish from the display.
    """
    from PyQt6.QtCore import Qt
    text = label.text()
    if label.textFormat() == Qt.TextFormat.PlainText:
        return text
    if "<" in text:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        text = re.sub(r"<[^>]+>", "", text)
    return text


def _banner(title, detail):
    from aria.confirm_banner import ConfirmBanner
    parent = None
    if _HAS_QT:
        from PyQt6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        parent.resize(1440, 900)
        parent.show()
    return ConfirmBanner(title, detail, parent)


HIDDEN_DETAIL = (
    "run shell command 'python -c \"print('<b>Check status</b><!--'); "
    "print('ARIA_REVIEW_HIDDEN'); print('-->')\"' in ''")


def test_banner_detail_renders_hidden_command_literally():
    from PyQt6.QtCore import Qt
    banner = _banner("Approve run_shell_command?", HIDDEN_DETAIL)
    assert banner._detail_label.textFormat() == Qt.TextFormat.PlainText
    assert banner._detail_label.text() == HIDDEN_DETAIL
    rendered = _rendered(banner._detail_label)
    assert "ARIA_REVIEW_HIDDEN" in rendered
    assert "<b>Check status</b>" in rendered


def test_banner_title_is_plain_text_too():
    from PyQt6.QtCore import Qt
    title = "Approve run_shell_command? <b>hidden</b><!--x-->"
    banner = _banner(title, "some detail")
    assert banner._title_label.textFormat() == Qt.TextFormat.PlainText
    assert banner._title_label.text() == title
    assert "<b>hidden</b>" in _rendered(banner._title_label)


def test_banner_preserves_plain_detail_verbatim():
    banner = _banner("Approve write_file?", "write file x")
    assert banner._detail_label.text() == "write file x"
    assert _rendered(banner._detail_label) == "write file x"
