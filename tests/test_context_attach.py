"""Clipboard context attachment (pass 6): dashboard-staged text
rides the next turn as a delimited block, then clears.

Pure-module suite: the staging module imports nothing from the
package and no Qt; state is reset between tests."""
import importlib
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

attach = importlib.import_module("aria.context_attach")


def setup_function():
    attach.clear_pending()


def test_stage_and_summary():
    staged = attach.set_pending("TypeError: cannot read property 'x'")
    assert staged["chars"] == len("TypeError: cannot read property 'x'")
    assert staged["truncated"] is False
    summary = attach.pending_summary()
    assert summary["chars"] == staged["chars"]
    assert "TypeError" in summary["preview"]


def test_empty_clipboard_stages_nothing():
    assert attach.set_pending("") is None
    assert attach.set_pending("   \n  ") is None
    assert attach.pending_summary() is None
    assert attach.consume_block() is None


def test_empty_clipboard_keeps_existing_staging():
    attach.set_pending("real text")
    assert attach.set_pending("") is None
    assert attach.pending_summary()["chars"] == len("real text")


def test_consume_block_format_and_clear():
    attach.set_pending("some copied text")
    block = attach.consume_block()
    assert block.startswith("[Attached clipboard text")
    assert "some copied text" in block
    assert block.rstrip().endswith("[End of attached clipboard text]")
    # Consumed: a second consume finds nothing.
    assert attach.consume_block() is None
    assert attach.pending_summary() is None


def test_oversized_text_truncated_and_flagged():
    big = "x" * (attach.MAX_CHARS + 500)
    staged = attach.set_pending(big)
    assert staged["chars"] == attach.MAX_CHARS
    assert staged["truncated"] is True
    block = attach.consume_block()
    assert "truncated" in block
    body = block.split("\n")[1]
    assert len(body) == attach.MAX_CHARS and set(body) == {"x"}


def test_clear_pending():
    attach.set_pending("text")
    attach.clear_pending()
    assert attach.pending_summary() is None


def test_preview_flattens_and_truncates():
    attach.set_pending("line one\nline two " + "y" * 200)
    preview = attach.pending_summary()["preview"]
    assert "\n" not in preview
    assert preview.endswith("…")
    assert len(preview) == attach.PREVIEW_CHARS + 1
