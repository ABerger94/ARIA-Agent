"""Unit tests for aria/tools/fileproc.py — Merger Phase 5 (blueprint
SS2.4 #1, SS5 Phase 5). Fixtures are generated in-test (text, code,
JSON, CSV, a hand-built FlateDecode PDF, zip-based docx/xlsx/pptx, a
1x1 PNG, a wave-module WAV, a zip archive, raw binary). The pool and
the transcriber are injected fakes — no network, no speech stack.

Run:  python3 tests/_run_suite.py tests/test_fileproc.py
"""
import base64
import os
import sys
import tempfile
import types
import wave
import zipfile
import zlib

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

import importlib

config = importlib.import_module("aria.config")
fileproc = importlib.import_module("aria.tools.fileproc")

# ---------------------------------------------------------------- fixtures
FIX = tempfile.mkdtemp(prefix="aria_fileproc_")


def _w(name, data, mode="wb"):
    if isinstance(data, str):
        data = data.encode("utf-8")
    p = os.path.join(FIX, name)
    with open(p, mode) as f:
        f.write(data)
    return p


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")

P_TEXT = _w("notes.txt",
            "Aria merger notes.\nThe file organ reads text directly.\n" * 3)
P_CODE = _w("script.py", "def main():\n    return 42\n\nmain()\n")
P_JSON = _w("data.json", '{"name": "ARIA", "count": 3, "tags": ["a", "b"]}')
P_BADJSON = _w("bad.json", "{not json at all")
P_CSV = _w("table.csv", "name,score\nalpha,10\nbeta,20\ngamma,30\n")
P_PNG = _w("tiny.png", PNG_1X1)
P_BIN = _w("blob.bin", bytes(range(256)) * 4)


def _make_pdf():
    stream = b"BT /F1 12 Tf (Hello PDF world) Tj ET"
    comp = zlib.compress(stream)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 5 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Length " + str(len(comp)).encode() +
        b" /Filter /FlateDecode >>\nstream\n" + comp + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    for i, body in enumerate(objs, 1):
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    out += b"trailer << /Root 1 0 R >>\n%%EOF"
    return bytes(out)


P_PDF = _w("doc.pdf", _make_pdf())

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOC_XML = (
    f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>'
    "<w:p><w:r><w:t>Quarterly results</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>Revenue grew steadily.</w:t></w:r></w:p>"
    "</w:body></w:document>")
P_DOCX = os.path.join(FIX, "report.docx")
with zipfile.ZipFile(P_DOCX, "w") as z:
    z.writestr("word/document.xml", _DOC_XML)

_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P_XLSX = os.path.join(FIX, "book.xlsx")
with zipfile.ZipFile(P_XLSX, "w") as z:
    z.writestr("xl/workbook.xml",
               f'<workbook xmlns="{_SS}"><sheets>'
               '<sheet name="Data" sheetId="1"/></sheets></workbook>')
    z.writestr("xl/sharedStrings.xml",
               f'<sst xmlns="{_SS}"><si><t>name</t></si>'
               "<si><t>alpha</t></si></sst>")
    z.writestr("xl/worksheets/sheet1.xml",
               f'<worksheet xmlns="{_SS}"><sheetData>'
               '<row><c t="s"><v>0</v></c><c><v>42</v></c></row>'
               '<row><c t="s"><v>1</v></c><c><v>7</v></c></row>'
               "</sheetData></worksheet>")

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_PPTX = os.path.join(FIX, "slides.pptx")
with zipfile.ZipFile(P_PPTX, "w") as z:
    z.writestr("ppt/slides/slide1.xml",
               f'<p:sld xmlns:a="{_A}" '
               'xmlns:p="http://schemas.openxmlformats.org/'
               'presentationml/2006/main"><p:cSld><p:spTree>'
               "<p:sp><p:txBody><a:p><a:r><a:t>Intro slide</a:t></a:r>"
               "</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>")

P_ZIP = os.path.join(FIX, "pack.zip")
with zipfile.ZipFile(P_ZIP, "w") as z:
    z.writestr("a.txt", "aaa")
    z.writestr("b.txt", "bbb")

P_WAV = os.path.join(FIX, "song.wav")
with wave.open(P_WAV, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(b"\x00\x00" * 4000)  # 0.5 s of silence


# ------------------------------------------------------------------- fakes
class FakePool:
    def __init__(self, chat_text="A concise model summary.",
                 vision_text="A tiny red square.", available=True,
                 raise_on=None):
        self._chat_text = chat_text
        self._vision_text = vision_text
        self._available = available
        self._raise_on = raise_on
        self.chat_prompts = []
        self.vision_calls = []

    def available(self):
        return self._available

    def chat(self, prompt, **kw):
        self.chat_prompts.append(prompt)
        if self._raise_on == "chat":
            raise RuntimeError("pool boom")
        return self._chat_text

    def vision_from_file(self, prompt, path, **kw):
        self.vision_calls.append((prompt, path))
        if self._raise_on == "vision":
            raise RuntimeError("vision boom")
        return self._vision_text


NO_POOL = FakePool(available=False)


def _fake_transcriber(data, mime):
    return "hello transcript world"


# -------------------------------------------------------------------- tests
def test_detect_types():
    cases = {
        "a.png": "image", "a.mp4": "video", "a.mp3": "audio",
        "a.pdf": "pdf", "a.docx": "docx", "a.txt": "text",
        "a.csv": "csv", "a.xlsx": "excel", "a.json": "json",
        "a.xml": "xml", "a.py": "code", "a.zip": "archive",
        "a.pptx": "pptx", "a.bin": "unknown",
    }
    for name, want in cases.items():
        got = fileproc.detect_file_type(name)
        assert got == want, (name, got, want)
    print("ok detect_types")


def test_text_with_pool_summary():
    pool = FakePool()
    r = fileproc.process_file(P_TEXT, pool=pool)
    assert r["ok"] and r["type"] == "text", r
    assert "A concise model summary." in r["summary"], r["summary"]
    assert r["detail"]["words"] > 10, r["detail"]
    assert pool.chat_prompts and "file organ reads text" in \
        pool.chat_prompts[0]
    assert r["detail"].get("summarized") is True
    print("ok text_with_pool_summary")


def test_text_no_pool_excerpt():
    r = fileproc.process_file(P_TEXT, pool=NO_POOL)
    assert "Excerpt:" in r["summary"], r["summary"]
    assert "file organ reads text" in r["summary"]
    print("ok text_no_pool_excerpt")


def test_code_file():
    r = fileproc.process_file(P_CODE, pool=NO_POOL)
    assert r["type"] == "code", r
    assert "return 42" in r["summary"], r["summary"]
    print("ok code_file")


def test_json_digest_and_bad_json():
    r = fileproc.process_file(P_JSON, pool=NO_POOL)
    assert r["detail"]["key_count"] == 3, r["detail"]
    assert "name" in r["detail"]["keys"]
    bad = fileproc.process_file(P_BADJSON, pool=NO_POOL)
    assert "json_error" in bad["detail"], bad["detail"]
    assert bad["ok"]
    print("ok json_digest_and_bad_json")


def test_csv_stats():
    r = fileproc.process_file(P_CSV, pool=NO_POOL)
    assert r["detail"]["rows"] == 3, r["detail"]
    assert r["detail"]["columns"] == ["name", "score"]
    st = r["detail"]["numeric_stats"]["score"]
    assert st["min"] == 10.0 and st["max"] == 30.0 and st["mean"] == 20.0, st
    print("ok csv_stats")


def test_pdf_stdlib_extract():
    r = fileproc.process_file(P_PDF, pool=NO_POOL)
    assert r["detail"]["pages"] == 2, r["detail"]
    assert r["detail"]["extractor"] == "stdlib-streams", r["detail"]
    assert "Hello PDF world" in r["summary"], r["summary"]
    print("ok pdf_stdlib_extract")


def test_docx_extract():
    r = fileproc.process_file(P_DOCX, pool=NO_POOL)
    assert r["detail"]["paragraphs"] == 2, r["detail"]
    assert "Quarterly results" in r["summary"], r["summary"]
    print("ok docx_extract")


def test_xlsx_extract():
    r = fileproc.process_file(P_XLSX, pool=NO_POOL)
    assert r["detail"]["sheets"] == ["Data"], r["detail"]
    assert r["detail"]["rows_first_sheet"] == 2, r["detail"]
    assert "42" in r["summary"], r["summary"]
    print("ok xlsx_extract")


def test_pptx_extract():
    r = fileproc.process_file(P_PPTX, pool=NO_POOL)
    assert r["detail"]["slides"] == 1, r["detail"]
    assert "Intro slide" in r["summary"], r["summary"]
    print("ok pptx_extract")


def test_archive_list():
    r = fileproc.process_file(P_ZIP, pool=NO_POOL)
    assert r["detail"]["entries"] == 2, r["detail"]
    assert "a.txt" in r["summary"], r["summary"]
    print("ok archive_list")


def test_image_vision():
    pool = FakePool()
    r = fileproc.process_file(P_PNG, pool=pool)
    assert r["detail"]["dimensions"] == "1x1", r["detail"]
    assert r["detail"]["format"] == "PNG", r["detail"]
    assert "A tiny red square." in r["summary"], r["summary"]
    assert pool.vision_calls and pool.vision_calls[0][1] == P_PNG
    print("ok image_vision")


def test_image_pool_down_metadata_only():
    r = fileproc.process_file(P_PNG, pool=NO_POOL)
    assert "vision pool unavailable" in r["detail"]["vision_note"], r["detail"]
    assert "1x1" in r["summary"], r["summary"]
    print("ok image_pool_down_metadata_only")


def test_image_vision_raises_degrades():
    pool = FakePool(raise_on="vision")
    r = fileproc.process_file(P_PNG, pool=pool)
    assert "vision_error" in r["detail"], r["detail"]
    assert "1x1" in r["summary"], r["summary"]
    print("ok image_vision_raises_degrades")


def test_audio_transcribe():
    r = fileproc.process_file(P_WAV, transcriber=_fake_transcriber)
    assert r["detail"]["duration_s"] == 0.5, r["detail"]
    assert "Transcript: hello transcript world" in r["summary"], r["summary"]
    print("ok audio_transcribe")


def test_audio_transcribe_failure_honest():
    def boom(data, mime):
        raise RuntimeError("no ffmpeg here")

    r = fileproc.process_file(P_WAV, transcriber=boom)
    assert "transcribe_error" in r["detail"], r["detail"]
    assert "Duration about 0.5 seconds." in r["summary"], r["summary"]
    print("ok audio_transcribe_failure_honest")


def test_binary_unknown_metadata_only():
    r = fileproc.process_file(P_BIN, pool=NO_POOL)
    assert r["type"] == "unknown", r
    assert "metadata only" in r["summary"], r["summary"]
    print("ok binary_unknown_metadata_only")


def test_size_cap_metadata_only():
    old = config.FILEPROC_MAX_FILE_BYTES
    config.FILEPROC_MAX_FILE_BYTES = 10
    try:
        r = fileproc.process_file(P_TEXT, pool=NO_POOL)
    finally:
        config.FILEPROC_MAX_FILE_BYTES = old
    assert r["detail"].get("capped") is True, r["detail"]
    assert "metadata only" in r["summary"], r["summary"]
    print("ok size_cap_metadata_only")


def test_missing_file_raises():
    try:
        fileproc.process_file(os.path.join(FIX, "nope.txt"))
    except FileNotFoundError:
        print("ok missing_file_raises")
        return
    raise AssertionError("expected FileNotFoundError")


def test_tool_process_file_and_staged_default():
    out = fileproc.tool_process_file(P_TEXT, "")
    assert "notes.txt" in out, out
    out = fileproc.tool_process_file(os.path.join(FIX, "nope.txt"), "")
    assert out.startswith("[File not found:"), out
    fake_frame = types.ModuleType("aria.frame")
    fake_frame.staged_file = lambda: P_TEXT
    sys.modules["aria.frame"] = fake_frame
    try:
        out = fileproc.tool_process_file("", "")
        assert "notes.txt" in out, out
    finally:
        del sys.modules["aria.frame"]
    print("ok tool_process_file_and_staged_default")


def test_process_and_report_channels():
    config._EVENT_RING.clear()
    r = fileproc.process_and_report(P_TEXT, pool=NO_POOL)
    assert r is not None and r["type"] == "text", r
    msgs = [m for _, _, m, _ in config.recent_events(50)]
    assert any("File processed: notes.txt" in m for m in msgs), msgs
    config._EVENT_RING.clear()
    r = fileproc.process_and_report(os.path.join(FIX, "nope.txt"))
    assert r is None
    msgs = [m for _, _, m, _ in config.recent_events(50)]
    assert any("File processing failed" in m for m in msgs), msgs
    print("ok process_and_report_channels")
