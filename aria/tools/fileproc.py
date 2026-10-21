"""
ARIA file organ (Merger Phase 5 — blueprint SS2.4 #1, SS5 Phase 5).

Adapted from actions/file_processor.py in Mark-XXXIX-OR by FatihMakes
(https://github.com/FatihMakes/Mark-XXXIX-OR), licensed CC BY-NC 4.0
(https://creativecommons.org/licenses/by-nc/4.0). ARIA is a personal,
non-commercial system; attribution is retained here and in the commit
message. His universal-processor shape — detect the type, dispatch to a
per-type handler, return text — is carried over. Adaptations:

  * Every Gemini call is replaced: image description goes through the
    Phase 2 orpool vision pool, text summarisation through the orpool
    text pool. Nothing here knows a provider exists.
  * Local extraction is stdlib-first (csv / json / zipfile /
    ElementTree / zlib PDF streams) plus Pillow (already pinned).
    His pandas / pdfplumber / python-docx / pydub / ffmpeg action
    matrix (resize / convert / trim / run-code / ...) is NOT ported:
    this organ answers "what is this file, and what's in it" — it
    does not edit media and never executes code.
  * Audio (and video) transcription uses ARIA's own speech organ
    (aria.speech.transcribe_audio — the phone-bridge pipeline), not
    a cloud upload.
  * Failures surface through Phase 1 (failures.surface_failure);
    degraded results say so in their detail dict instead of
    swallowing the cause.

Answers come back as a structured dict:
    {path, name, type, size_bytes, size, ok, summary, detail}
where detail carries the per-type facts (pages / rows / dimensions /
transcript / excerpt / extractor used / degradation notes). The tool
surface (tool_process_file) returns just the summary text.

Imports: stdlib + aria.config only, in the fileops convention — the
pool, the failure store, the speech organ, and the chat log are all
lazy imports inside the functions that use them, so importing this
module can never break startup or create an import cycle.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tarfile
import wave
import zipfile
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aria import config as _config
from aria.config import add_log

# ------------------------------------------------------------------ types
# His extension sets (file_processor.py _detect_type), carried over.
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg", "ico"}
_VIDEO_EXTS = {"mp4", "avi", "mov", "mkv", "wmv", "flv", "webm", "m4v", "3gp"}
_AUDIO_EXTS = {"mp3", "wav", "ogg", "m4a", "aac", "flac", "wma", "opus"}
_CODE_EXTS = {"py", "js", "ts", "jsx", "tsx", "html", "css", "java", "c",
              "cpp", "cs", "go", "rs", "rb", "php", "swift", "kt", "sh",
              "bash", "ps1", "lua", "r", "m", "sql", "yaml", "toml"}
_ARCHIVE_EXTS = {"zip", "rar", "tar", "gz", "7z", "bz2", "xz"}
_TEXT_EXTS = {"txt", "md", "rst", "log"}

_TYPE_LABELS = {
    "image": "image", "video": "video file", "audio": "audio file",
    "pdf": "PDF", "docx": "Word document", "text": "text file",
    "csv": "spreadsheet (CSV)", "excel": "spreadsheet",
    "json": "JSON file", "xml": "XML file", "code": "code file",
    "archive": "archive", "pptx": "presentation", "unknown": "file",
}

_AUDIO_MIME = {
    ".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/opus",
    ".aac": "audio/aac", ".wma": "audio/x-ms-wma",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".avi": "video/x-msvideo",
}


def detect_file_type(path) -> str:
    """His taxonomy: classify by extension (file_processor._detect_type)."""
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _CODE_EXTS:
        return "code"
    if ext in _ARCHIVE_EXTS:
        return "archive"
    if ext == "pdf":
        return "pdf"
    if ext in ("docx", "doc"):
        return "docx"
    if ext in _TEXT_EXTS:
        return "text"
    if ext in ("csv", "tsv"):
        return "csv"
    if ext in ("xlsx", "xls", "ods"):
        return "excel"
    if ext == "json":
        return "json"
    if ext == "xml":
        return "xml"
    if ext in ("pptx", "ppt"):
        return "pptx"
    return "unknown"


def _size_str(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


# ------------------------------------------------------------ lazy organs
def _resolve_pool(pool):
    """The background pool, lazily (Phase 2). None when unimportable."""
    if pool is not None:
        return pool
    try:
        from aria.agent.orpool import get_pool
        return get_pool()
    except Exception:
        return None


def _pool_ready(pool) -> bool:
    try:
        return bool(pool is not None and pool.available())
    except Exception:
        return False


def _resolve_transcriber(fn):
    """ARIA's own transcription organ, lazily. None when unimportable."""
    if fn is not None:
        return fn
    try:
        from aria.speech import transcribe_audio
        return transcribe_audio
    except Exception:
        return None


def _surface(source: str, error, user_message: Optional[str] = None) -> None:
    """Phase 1 surfacing, best-effort — never raises into the organ."""
    try:
        from aria.agent.failures import surface_failure
        surface_failure(source, error, user_message=user_message)
    except Exception:
        pass


# --------------------------------------------------------------- extractors
def _read_text_head(path: Path, max_chars: int):
    """Read a text-ish file's head, capped. Returns (text, truncated)."""
    size = path.stat().st_size
    want = max_chars * 2 + 4096
    with open(path, "rb") as f:
        raw = f.read(want)
    text = raw.decode("utf-8", errors="replace")
    return text[:max_chars], size > len(raw)


def _extract_plain_text(path: Path, max_chars: int, detail: Dict[str, Any]):
    text, truncated = _read_text_head(path, max_chars)
    detail["lines"] = text.count("\n") + (1 if text else 0)
    detail["words"] = len(text.split())
    detail["chars"] = len(text)
    if truncated:
        detail["truncated"] = True
    return text


def _extract_json(path: Path, max_chars: int, detail: Dict[str, Any]):
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except Exception as e:
        detail["json_error"] = str(e)
        return raw[:max_chars]
    detail["json_type"] = type(data).__name__
    if isinstance(data, dict):
        detail["keys"] = list(data.keys())[:25]
        detail["key_count"] = len(data)
    elif isinstance(data, list):
        detail["item_count"] = len(data)
        if data:
            detail["item_type"] = type(data[0]).__name__
    return json.dumps(data, indent=2, ensure_ascii=False)[:max_chars]


def _extract_xml(path: Path, max_chars: int, detail: Dict[str, Any]):
    raw = path.read_bytes()
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        detail["xml_error"] = str(e)
        return raw.decode("utf-8", errors="replace")[:max_chars]
    detail["root_tag"] = root.tag.rsplit("}", 1)[-1]
    detail["elements"] = sum(1 for _ in root.iter())
    texts = [t.strip() for t in root.itertext() if t and t.strip()]
    return " ".join(texts)[:max_chars]


def _extract_csv(path: Path, max_chars: int, detail: Dict[str, Any]):
    sample: List[List[str]] = []
    row_count = 0
    numeric: Dict[int, List[float]] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        head = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(head, delimiters=",\t;|")
        except Exception:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        header: List[str] = []
        for i, row in enumerate(reader):
            if i == 0:
                header = row
                continue
            row_count += 1
            if len(sample) < 5:
                sample.append(row)
            if row_count <= 500:
                for ci, cell in enumerate(row):
                    try:
                        numeric.setdefault(ci, []).append(float(cell))
                    except (TypeError, ValueError):
                        pass
            if row_count > 200000:
                detail["row_count_capped"] = True
                break
    detail["columns"] = header
    detail["rows"] = row_count
    detail["sample"] = sample
    stats: Dict[str, Dict[str, float]] = {}
    for ci, vals in numeric.items():
        if header and ci < len(header) and len(vals) >= 3:
            stats[header[ci]] = {
                "min": min(vals), "max": max(vals),
                "mean": round(sum(vals) / len(vals), 4),
            }
    if stats:
        detail["numeric_stats"] = stats
    lines = [", ".join(header)] if header else []
    lines += [", ".join(r) for r in sample]
    if stats:
        lines.append("Numeric columns: " + "; ".join(
            f"{k}: min {v['min']}, max {v['max']}, mean {v['mean']}"
            for k, v in stats.items()))
    return "\n".join(lines)[:max_chars]


# --- PDF: stdlib stream extractor (no PDF library is pinned; when one
# is importable it is preferred, but this path is the honest default).
def _pdf_unescape(s: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):  # backslash
            nxt = s[i + 1]
            simple = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t",
                      ord("("): b"(", ord(")"): b")", ord("\\"): b"\\"}
            if nxt in simple:
                out += simple[nxt]
                i += 2
                continue
            if 0x30 <= nxt <= 0x37:  # octal, up to 3 digits
                j = i + 1
                oct_digits = b""
                while j < len(s) and len(oct_digits) < 3 and 0x30 <= s[j] <= 0x37:
                    oct_digits += bytes([s[j]])
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return out.decode("latin-1", errors="replace")


def _pdf_stream_text(chunk: bytes) -> str:
    parts: List[str] = []
    token = re.compile(
        rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|')|\[(?:[^\[\]\\]|\((?:\\.|[^\\()])*\))*\]\s*TJ")
    for m in token.finditer(chunk):
        seg = m.group(0)
        for sm in re.finditer(rb"\((?:\\.|[^\\()])*\)", seg):
            parts.append(_pdf_unescape(sm.group(0)[1:-1]))
    return " ".join(p for p in parts if p)


def _pdf_extract(path: Path, max_chars: int, detail: Dict[str, Any]) -> str:
    data = path.read_bytes()
    detail["pages"] = len(re.findall(rb"/Type\s*/Page(?![s\w])", data))
    # Preferred: a real PDF library, if the laptop happens to have one.
    for mod_name in ("pdfplumber", "pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
        except Exception:
            continue
        try:
            texts = []
            if mod_name == "pdfplumber":
                with mod.open(path) as pdf:
                    detail["pages"] = len(pdf.pages)
                    for page in pdf.pages:
                        texts.append(page.extract_text() or "")
            else:
                reader = mod.PdfReader(str(path))
                detail["pages"] = len(reader.pages)
                for page in reader.pages:
                    texts.append(page.extract_text() or "")
            detail["extractor"] = mod_name
            return "\n".join(texts)[:max_chars]
        except Exception:
            break
    detail["extractor"] = "stdlib-streams"
    out: List[str] = []
    total = 0
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1)
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        try:
            chunk = zlib.decompress(raw)
        except Exception:
            continue
        text = _pdf_stream_text(chunk)
        if text:
            out.append(text)
            total += len(text)
        if total > max_chars:
            break
    return "\n".join(out)[:max_chars]


# --- Office formats: all three are zips of XML — stdlib can read them.
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx_extract(path: Path, max_chars: int, detail: Dict[str, Any]) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{_W_NS}p"):
        t = "".join(node.text or "" for node in p.iter(f"{_W_NS}t"))
        if t.strip():
            paras.append(t)
    detail["paragraphs"] = len(paras)
    return "\n".join(paras)[:max_chars]


def _xlsx_extract(path: Path, max_chars: int, detail: Dict[str, Any]) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for si in sroot.iter(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        detail["shared_strings"] = len(shared)
        sheet_names: List[str] = []
        if "xl/workbook.xml" in names:
            wroot = ET.fromstring(z.read("xl/workbook.xml"))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            sheet_names = [s.get("name", "") for s in wroot.iter(f"{ns}sheet")]
        detail["sheets"] = sheet_names
        sheets = sorted(n for n in names
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        preview: List[str] = []
        if sheets:
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            sroot = ET.fromstring(z.read(sheets[0]))
            rows = list(sroot.iter(f"{ns}row"))
            detail["rows_first_sheet"] = len(rows)
            for row in rows[:5]:
                vals = []
                for c in row.iter(f"{ns}c"):
                    v = c.find(f"{ns}v")
                    if v is None or v.text is None:
                        continue
                    if c.get("t") == "s":
                        try:
                            vals.append(shared[int(v.text)])
                            continue
                        except Exception:
                            pass
                    vals.append(v.text)
                if vals:
                    preview.append(", ".join(vals))
        return "\n".join(preview)[:max_chars]


def _pptx_extract(path: Path, max_chars: int, detail: Dict[str, Any]) -> str:
    with zipfile.ZipFile(path) as z:
        slides = sorted(
            (n for n in z.namelist()
             if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        detail["slides"] = len(slides)
        out = []
        for i, name in enumerate(slides, 1):
            root = ET.fromstring(z.read(name))
            texts = [t.text.strip() for t in root.iter(f"{_A_NS}t")
                     if t.text and t.text.strip()]
            if texts:
                out.append(f"Slide {i}: " + " ".join(texts))
        return "\n".join(out)[:max_chars]


def _archive_extract(path: Path, detail: Dict[str, Any]) -> str:
    ext = path.suffix.lower()
    names: List[str] = []
    total = 0
    try:
        if ext == ".zip":
            with zipfile.ZipFile(path) as z:
                infos = z.infolist()
                names = [i.filename for i in infos]
                total = sum(i.file_size for i in infos)
        elif ext in (".tar", ".gz", ".bz2", ".xz"):
            with tarfile.open(path) as t:
                members = t.getmembers()
                names = [m.name for m in members]
                total = sum(m.size for m in members)
        else:
            detail["archive_note"] = f"no stdlib reader for {ext} archives"
            return ""
    except Exception as e:
        detail["archive_error"] = str(e)
        return ""
    detail["entries"] = len(names)
    detail["uncompressed_bytes"] = total
    return "\n".join(names[:40])


# ------------------------------------------------------------------- media
def _media_extract(path: Path, ftype: str, question: str,
                   detail: Dict[str, Any], pool, transcriber) -> str:
    """Audio/video: metadata always; a transcript when her speech
    organ can produce one under the size cap."""
    ext = path.suffix.lower()
    if ext == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                frames, rate = w.getnframes(), w.getframerate()
                detail["duration_s"] = round(frames / rate, 1) if rate else 0
                detail["channels"] = w.getnchannels()
                detail["sample_rate"] = rate
        except Exception as e:
            detail["wav_error"] = str(e)
    size = path.stat().st_size
    cap = int(getattr(_config, "FILEPROC_TRANSCRIBE_MAX_BYTES", 0) or 0)
    if size > cap:
        detail["transcribe_note"] = "file over the transcription size cap"
        return ""
    tr = _resolve_transcriber(transcriber)
    if tr is None:
        detail["transcribe_note"] = "transcription organ unavailable"
        return ""
    try:
        text = tr(path.read_bytes(), _AUDIO_MIME.get(ext, "audio/mpeg"))
    except Exception as e:
        _surface("fileproc", e,
                 user_message="I couldn't transcribe that media file.")
        detail["transcribe_error"] = str(e)
        return ""
    detail["transcript_chars"] = len(text or "")
    return (text or "")[:int(getattr(_config, "FILEPROC_TEXT_MAX_CHARS",
                                     20000) or 20000)]


def _image_extract(path: Path, question: str, detail: Dict[str, Any],
                   pool) -> str:
    """Image: Pillow metadata locally; the description itself is the
    Phase 6 vision path — one orpool vision call, text out."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            detail["format"] = img.format
            detail["dimensions"] = f"{img.size[0]}x{img.size[1]}"
            detail["mode"] = img.mode
    except Exception as e:
        detail["image_error"] = str(e)
    pool = _resolve_pool(pool)
    if not _pool_ready(pool):
        detail["vision_note"] = "vision pool unavailable — metadata only"
        return ""
    prompt = question or ("Describe this image clearly and concisely, "
                          "including any visible text.")
    try:
        desc = pool.vision_from_file(prompt, str(path))
    except Exception as e:
        _surface("fileproc", e,
                 user_message="I couldn't get a description of that image.")
        detail["vision_error"] = str(e)
        return ""
    detail["vision"] = True
    return (desc or "").strip()


# ------------------------------------------------------------- summarising
def _pool_summarize(name: str, ftype: str, text: str, question: str,
                    pool, detail: Dict[str, Any]) -> str:
    """Optional LLM digest of extracted text through the background
    pool. Degrades silently-to-digest (never to a failure record)
    when the pool is simply not configured; a pool that was tried
    and broke is noted in detail."""
    if not text or len(text.strip()) < 40:
        return ""
    pool = _resolve_pool(pool)
    if not _pool_ready(pool):
        return ""
    ask = question or "Summarize what this file contains in 2-4 sentences."
    prompt = (f"A file named {name} ({ftype}) was given to ARIA. "
              f"Extracted content follows.\n\nTask: {ask}\n\n{text}")
    try:
        out = pool.chat(prompt, max_tokens=512, temperature=0.3)
    except Exception as e:
        detail["summarize_error"] = str(e)
        return ""
    detail["summarized"] = True
    return (out or "").strip()


def _digest_line(ftype: str, detail: Dict[str, Any]) -> str:
    """Deterministic one-paragraph digest when no model summary exists."""
    if ftype == "csv" and detail.get("columns"):
        return (f"{detail.get('rows', 0)} data rows; columns: "
                f"{', '.join(detail['columns'][:10])}.")
    if ftype == "json":
        if detail.get("keys") is not None:
            return (f"A JSON object with {detail.get('key_count', 0)} keys: "
                    f"{', '.join(detail['keys'][:10])}.")
        if detail.get("item_count") is not None:
            return f"A JSON array of {detail['item_count']} items."
    if ftype == "archive" and detail.get("entries") is not None:
        return (f"Contains {detail['entries']} entries, "
                f"{_size_str(detail.get('uncompressed_bytes', 0))} uncompressed.")
    if ftype in ("audio", "video"):
        if detail.get("duration_s"):
            return f"Duration about {detail['duration_s']} seconds."
        return "No transcript could be produced for it."
    if ftype == "image":
        return detail.get("vision_note", "Metadata only.")
    return ""


# --------------------------------------------------------------- main entry
def process_file(path, question: str = "", *, pool=None,
                 transcriber: Optional[Callable] = None) -> Dict[str, Any]:
    """Process one file into a structured summary dict.

    Raises FileNotFoundError / ValueError for a missing or non-file
    path (callers turn those into honest user-facing lines); every
    content-level problem degrades into detail notes instead.
    """
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        raise FileNotFoundError(str(p))
    if not p.is_file():
        raise ValueError(f"not a file: {p}")
    size = p.stat().st_size
    ftype = detect_file_type(p)
    max_chars = int(getattr(_config, "FILEPROC_TEXT_MAX_CHARS", 20000) or 20000)
    max_bytes = int(getattr(_config, "FILEPROC_MAX_FILE_BYTES", 0) or 0)
    result: Dict[str, Any] = {
        "path": str(p), "name": p.name, "type": ftype,
        "size_bytes": size, "size": _size_str(size),
        "ok": True, "summary": "", "detail": {},
    }
    detail = result["detail"]
    text = ""

    if max_bytes and size > max_bytes:
        detail["capped"] = True
        detail["cap_note"] = (f"over the {_size_str(max_bytes)} read cap — "
                              f"metadata only")
    else:
        try:
            if ftype in ("text", "code"):
                text = _extract_plain_text(p, max_chars, detail)
            elif ftype == "json":
                text = _extract_json(p, max_chars, detail)
            elif ftype == "xml":
                text = _extract_xml(p, max_chars, detail)
            elif ftype == "csv":
                text = _extract_csv(p, max_chars, detail)
            elif ftype == "pdf":
                text = _pdf_extract(p, max_chars, detail)
            elif ftype == "docx" and p.suffix.lower() == ".docx":
                text = _docx_extract(p, max_chars, detail)
            elif ftype == "docx":
                detail["note"] = "legacy .doc — metadata only"
            elif ftype == "excel" and p.suffix.lower() == ".xlsx":
                text = _xlsx_extract(p, max_chars, detail)
            elif ftype == "excel":
                detail["note"] = f"{p.suffix.lower()} — metadata only"
            elif ftype == "pptx" and p.suffix.lower() == ".pptx":
                text = _pptx_extract(p, max_chars, detail)
            elif ftype == "pptx":
                detail["note"] = "legacy .ppt — metadata only"
            elif ftype == "archive":
                text = _archive_extract(p, detail)
            elif ftype in ("audio", "video"):
                text = _media_extract(p, ftype, question, detail,
                                      pool, transcriber)
            elif ftype == "image":
                text = _image_extract(p, question, detail, pool)
            else:  # unknown: sniff for text, else metadata only
                head = _read_text_head(p, 4096)[0]
                control = sum(1 for c in head if ord(c) < 9)
                if head and control / max(1, len(head)) < 0.02:
                    text = _extract_plain_text(p, max_chars, detail)
                    detail["sniffed_text"] = True
                else:
                    detail["note"] = "binary or unrecognised — metadata only"
        except Exception as e:
            _surface("fileproc", e,
                     user_message="I couldn't read that file's contents.")
            detail["extract_error"] = str(e)
            text = ""

    # ---- compose the summary
    label = _TYPE_LABELS.get(ftype, "file")
    head_bits = [f"{p.name} — {label}, {_size_str(size)}"]
    if detail.get("pages"):
        head_bits.append(f"{detail['pages']} pages")
    if detail.get("dimensions"):
        head_bits.append(str(detail["dimensions"]))
    if detail.get("rows") is not None and ftype == "csv":
        head_bits.append(f"{detail['rows']} rows")
    if detail.get("slides"):
        head_bits.append(f"{detail['slides']} slides")
    head = ", ".join(head_bits) + "."

    body = ""
    if ftype in ("audio", "video") and text:
        body = f"Transcript: {text[:800]}"
    elif ftype == "image" and text:
        body = text[:1200]
    elif text:
        llm = _pool_summarize(p.name, ftype, text, question, pool, detail)
        if llm:
            body = llm[:1200]
        else:
            excerpt = " ".join(text.split())[:700]
            body = f"Excerpt: {excerpt}" if excerpt else ""
    if not body:
        body = _digest_line(ftype, detail)
    if not body and detail.get("note"):
        note = str(detail["note"])
        body = note[:1].upper() + note[1:] + "."
    if detail.get("cap_note"):
        body = (body + " " if body else "") + f"({detail['cap_note']})"
    if not body:
        body = "No readable content could be extracted."
    result["summary"] = f"{head} {body}"[:1800]
    return result


def summarize_file(path, question: str = "", **kw) -> str:
    """Tool-facing convenience: just the summary text."""
    return process_file(path, question, **kw)["summary"]


def tool_process_file(path: str = "", question: str = "") -> str:
    """Dispatch handler body for the process_file tool ('what's in
    that file'). Defaults to the frame's staged (dropped) file."""
    if not getattr(_config, "FILEPROC_ENABLED", True):
        return "[File processing is disabled (ARIA_FILEPROC=0).]"
    p = (path or "").strip()
    if not p:
        try:
            from aria import frame as _frame
            p = _frame.staged_file() or ""
        except Exception:
            p = ""
    if not p:
        return ("[No file given and nothing is staged — drop a file on "
                "the frame first, or name a path.]")
    try:
        return summarize_file(p, question)
    except FileNotFoundError:
        return f"[File not found: {p}]"
    except Exception as e:
        return f"[File processing failed: {e}]"


def process_and_report(path, **kw) -> Optional[Dict[str, Any]]:
    """Drop-zone path (frame.receive_file): process off the UI thread
    and report through the existing channels — the event ring
    (add_log) and a chat-log line carrying the summary. Failures go
    through Phase 1 surfacing; this function never raises."""
    if not getattr(_config, "FILEPROC_ENABLED", True):
        return None
    try:
        result = process_file(path, **kw)
    except Exception as e:
        _surface("fileproc", e,
                 user_message="I couldn't process that file.")
        add_log(f"File processing failed: {Path(str(path)).name} ({e})")
        return None
    add_log(f"File processed: {result['name']} "
            f"({result['type']}, {result['size']})")
    try:
        from aria.memory import log_conversation
        log_conversation("ARIA", result["summary"])
    except Exception:
        pass
    return result
