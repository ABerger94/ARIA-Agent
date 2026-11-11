"""Pass 36 #02: memory embedding egress is gated on PROVIDER_MODE.

- local_only with NO local embedding model configured: _embed
  returns cached vectors only and never calls _embed_uncached
  (no POST to ollama.com); semantic search falls back to lexical.
- local_only WITH OLLAMA_LOCAL_EMBED_MODEL: vectors come from the
  local Ollama server (backend "local"), never the cloud.
- The embedding cache is keyed by model identity: a vector cached
  under the cloud model is not returned for a local-model lookup.
- auto mode: the existing Ollama Cloud path, unchanged.
"""
import importlib
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

config = importlib.import_module("aria.config")  # noqa: F401
memory = importlib.import_module("aria.memory")

VEC = [0.5, 0.5]


def _set_mode(monkeypatch, local_only, local_model=""):
    monkeypatch.setattr(memory, "_local_only", lambda: local_only)
    monkeypatch.setattr(memory, "_local_embed_model", lambda: local_model)


def test_local_only_no_local_model_uses_cache_only(monkeypatch):
    _set_mode(monkeypatch, True, "")
    calls = []
    monkeypatch.setattr(
        memory, "_embed_uncached",
        lambda *a, **k: calls.append((a, k)) or VEC)
    # Nothing cached: no vector, and the network seam never runs.
    assert memory._embed("fresh text") is None
    assert calls == []
    # Seed the cache under the cloud model, then read it back.
    import time as _time
    memory._EMBED_CACHE[("default", memory.EMBED_MODEL, "cached text")] = (
        _time.monotonic(), tuple(VEC))
    vec, model = memory._embed_with_model("cached text")
    assert vec == VEC and model == memory.EMBED_MODEL
    assert calls == [], "cached-only must never reach the network"


def test_local_only_with_local_model_uses_local_backend(monkeypatch):
    _set_mode(monkeypatch, True, "mxbai-embed-large")
    seen = {}

    def fake_uncached(text, model, backend):
        seen.update(text=text, model=model, backend=backend)
        return VEC

    monkeypatch.setattr(memory, "_embed_uncached", fake_uncached)
    vec, model = memory._embed_with_model("hello")
    assert vec == VEC and model == "mxbai-embed-large"
    assert seen["backend"] == "local"
    assert seen["model"] == "mxbai-embed-large"


def test_cache_is_keyed_by_model_identity(monkeypatch):
    _set_mode(monkeypatch, True, "mxbai-embed-large")
    calls = []
    monkeypatch.setattr(
        memory, "_embed_uncached",
        lambda *a, **k: calls.append((a, k)) or VEC)
    import time as _time
    # A vector cached under the CLOUD model must not satisfy a
    # local-model lookup — different model, different space.
    memory._EMBED_CACHE[("default", memory.EMBED_MODEL, "same text")] = (
        _time.monotonic(), tuple([9.9, 9.9]))
    vec, model = memory._embed_with_model("same text")
    assert vec == VEC and model == "mxbai-embed-large"
    assert len(calls) == 1, "cache miss must call the local backend"


def test_auto_mode_still_uses_cloud_backend(monkeypatch):
    _set_mode(monkeypatch, False)
    seen = {}

    def fake_uncached(text, model, backend):
        seen.update(model=model, backend=backend)
        return VEC

    monkeypatch.setattr(memory, "_embed_uncached", fake_uncached)
    vec, model = memory._embed_with_model("hello-auto")
    assert vec == VEC and model == memory.EMBED_MODEL
    assert seen == {"model": memory.EMBED_MODEL, "backend": "cloud"}


def test_local_only_semantic_search_falls_back_to_keyword(
        tmp_path, monkeypatch):
    import sqlite3
    _set_mode(monkeypatch, True, "")
    monkeypatch.setattr(memory, "_embed_uncached",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no egress in local-only")))
    db = str(tmp_path / "aria_memory.db")
    monkeypatch.setattr(memory, "DB_PATH", db)
    memory.init_databases()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO memory (category, key, value, embedding) "
        "VALUES (?, ?, ?, ?)",
        ("fact", "dog_name", "the dog is called Biscuit",
         memory._pack_embedding([0.98, 0.20])))
    conn.commit()
    conn.close()
    out = memory.memory_search_semantic("biscuit")
    assert "Biscuit" in out, out
