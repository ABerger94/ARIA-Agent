"""Pass 28 #42: the README must describe approval persistence and
Python execution accurately.

The README used to claim the approval store was in-memory (parked
steps die with a restart) and called run_python_code "sandboxed".
Approvals persist to approval_pending.json and survive restarts
subject to the 10-minute expiry; run_python_code is process
isolation only, with ARIA's full interpreter/filesystem access.
"""
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def _text():
    return README.read_text(encoding="utf-8")


def test_no_in_memory_approval_claim():
    assert "store is in-memory" not in _text()
    assert "died with a restart is simply re-parked" not in _text()


def test_approval_persistence_described():
    text = _text()
    assert "persists pending approvals to disk" in text
    assert "10-minute expiry" in text


def test_run_python_code_not_called_sandboxed():
    assert "`run_python_code` (sandboxed)" not in _text()


def test_run_python_code_described_as_process_isolation():
    assert "process isolation only" in _text()
    assert "not an OS sandbox" in _text()
