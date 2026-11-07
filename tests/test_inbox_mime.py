"""Pass 28 #41: WebP inbox images must be sent as image/webp.

The MIME table key was "webp" (no dot) while the lookup uses
os.path.splitext(name)[1], which returns ".webp" — so every WebP
photo was sent to the vision backend as image/jpeg.
"""
import base64
import sys
import types

from aria.inbox import _MIME_BY_EXT, describe_inbox_image
import aria.inbox as inbox_mod


def test_webp_extension_maps_to_image_webp():
    assert _MIME_BY_EXT.get(".webp") == "image/webp"


def test_other_image_mappings_unchanged():
    assert _MIME_BY_EXT.get(".jpg") == "image/jpeg"
    assert _MIME_BY_EXT.get(".jpeg") == "image/jpeg"
    assert _MIME_BY_EXT.get(".png") == "image/png"
    assert _MIME_BY_EXT.get(".gif") == "image/gif"
    assert _MIME_BY_EXT.get(".bmp") == "image/bmp"


def test_unknown_extension_falls_back_to_jpeg():
    assert _MIME_BY_EXT.get(".tiff", "image/jpeg") == "image/jpeg"


def _run_describe(tmp_path, monkeypatch, filename, payload):
    (tmp_path / filename).write_bytes(payload)
    monkeypatch.setattr(inbox_mod, "INBOX_DIR", str(tmp_path))
    seen = {}

    def fake_vision(sys_prompt, contents):
        seen["contents"] = contents
        return "a test photo"

    fake_mod = types.ModuleType("aria.vision")
    fake_mod._VISION_TEXT_CALL = fake_vision
    monkeypatch.setitem(sys.modules, "aria.vision", fake_mod)
    result = describe_inbox_image(filename)
    assert result == "a test photo"
    part = seen["contents"][0]["parts"][1]["inline_data"]
    return part


def test_describe_webp_sends_webp_mime(tmp_path, monkeypatch):
    # Minimal RIFF/WEBP header bytes; the mapping must come from the
    # extension, not from sniffing.
    part = _run_describe(tmp_path, monkeypatch, "photo.webp",
                         b"RIFF\x10\x00\x00\x00WEBPVP8 ")
    assert part["mime_type"] == "image/webp"
    assert base64.b64decode(part["data"]).startswith(b"RIFF")


def test_describe_jpg_still_sends_jpeg(tmp_path, monkeypatch):
    part = _run_describe(tmp_path, monkeypatch, "photo.jpg",
                         b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    assert part["mime_type"] == "image/jpeg"
