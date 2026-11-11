"""Pass 36 #41: tool_web_search keeps source URLs, types backend
failures, and reports zero results as an empty set — not an error.

- results render title + snippet + href (compact text, same shape
  as before, now with the source URL for follow-up retrieval);
- missing search library / backend exception return a typed FAILED
  (ResultText), so dispatch's normalizer cannot read them as DONE;
- zero results is plain DONE text naming the query — an explicit
  empty result set, not an error.
"""
import pytest

from aria.tools import builtins
from aria.outcome import ResultText, FAILED, normalize


class _FakeDDGS:
    def __init__(self, results=None, error=None):
        self._results = results
        self._error = error

    def text(self, query, max_results=3):
        if self._error is not None:
            raise self._error
        return self._results


def test_results_include_hrefs(monkeypatch):
    monkeypatch.setattr(builtins, "SEARCH_AVAILABLE", True)
    monkeypatch.setattr(builtins, "DDGS", lambda: _FakeDDGS([
        {"title": "Example", "href": "https://example.com/a",
         "body": "a snippet"},
        {"title": "Second", "href": "https://example.com/b",
         "body": "another snippet"},
    ]), raising=False)
    out = builtins.tool_web_search("example query")
    assert "https://example.com/a" in out
    assert "https://example.com/b" in out
    assert "Example" in out and "a snippet" in out
    assert normalize(out).status == "done"


def test_zero_results_is_explicit_empty_set_not_error(monkeypatch):
    monkeypatch.setattr(builtins, "SEARCH_AVAILABLE", True)
    monkeypatch.setattr(builtins, "DDGS", lambda: _FakeDDGS([]), raising=False)
    out = builtins.tool_web_search("no such thing xyz")
    assert "no such thing xyz" in out
    assert normalize(out).status == "done"


def test_missing_library_is_typed_failure(monkeypatch):
    monkeypatch.setattr(builtins, "SEARCH_AVAILABLE", False)
    out = builtins.tool_web_search("anything")
    assert isinstance(out, ResultText)
    assert out.status == FAILED
    assert normalize(out).status == "failed"


def test_backend_error_is_typed_failure(monkeypatch):
    monkeypatch.setattr(builtins, "SEARCH_AVAILABLE", True)
    monkeypatch.setattr(
        builtins, "DDGS",
        lambda: _FakeDDGS(error=RuntimeError("backend down")),
        raising=False)
    out = builtins.tool_web_search("anything")
    assert isinstance(out, ResultText)
    assert out.status == FAILED
    assert "Search error" in out
    assert normalize(out).status == "failed"
