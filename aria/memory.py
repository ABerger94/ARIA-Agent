import time
"""
ARIA Memory and Spine subsystem.
Handles SQLite persistent storage, vector embeddings, semantic retrieval,
continuous JSONL spine logging, and shutdown resume card generation.
"""

import os
import json
import re
import sqlite3
import threading
import urllib.request
import urllib.error
import numpy as np
from typing import Optional, List, Dict, Any
from datetime import datetime
from collections import OrderedDict
from aria import workspaces

from aria.config import (
    WORKSPACE_DIR, SPINE_PATH, RESUME_PATH, EMBED_MODEL,
    OLLAMA_CLOUD_API_KEY, redact, quarantine_key
)
from aria import config as _config_silent

DB_PATH = os.path.join(WORKSPACE_DIR, "aria_memory.db")
CHAT_LOG_FILE = os.path.join(WORKSPACE_DIR, "chat_history.md")
_EMBED_CACHE = OrderedDict()
_EMBED_LOCK = threading.Lock()
_SPINE_LOCK = threading.Lock()


def db_path():
    return workspaces.path("aria_memory.db", DB_PATH)


def _project_file(name, legacy):
    return workspaces.path(name, legacy)


def _queue_backup():
    try:
        from aria.backup_mirror import queue_snapshot
        queue_snapshot()
    except Exception as exc:
        _config_silent.log_silent('memory_mirror_queue', exc)
DB_LOCK = threading.Lock()

_SPINE_BOOT_KEYS = []
_LOG_CALLBACK = None

def set_log_callback(fn):
    global _LOG_CALLBACK
    _LOG_CALLBACK = fn

def add_log(msg):
    if _LOG_CALLBACK:
        try:
            _LOG_CALLBACK(msg)
        except Exception as _e_silent:
            _config_silent.log_silent("add_log", _e_silent)

def spine_append(event_type, payload):
    """Append one redacted event to the spine. Never raises."""
    try:
        evt = {"ts": datetime.now().isoformat(timespec="seconds"),
               "type": event_type}
        evt.update(_config_silent.redact_data(payload or {}))
        with open(_project_file("memory_spine.jsonl", SPINE_PATH), "a", encoding="utf-8") as f:
            with _SPINE_LOCK:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
                f.flush()
    except Exception as e:
        add_log("Spine append failed: %s" % e)


def _spine_tail(n=40):
    """Last n spine events, oldest first. Empty list if no spine yet."""
    try:
        if n <= 0:
            return []
        with open(_project_file("memory_spine.jsonl", SPINE_PATH), "rb") as f:
            f.seek(0, os.SEEK_END)
            position, chunks, count = f.tell(), [], 0
            while position > 0 and count <= n:
                size = min(position, 8192)
                position -= size
                f.seek(position)
                chunk = f.read(size)
                chunks.append(chunk)
                count += chunk.count(b"\n")
            lines = b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _spine_digest(events):
    """One compact line per event, for prompts and consolidation."""
    lines = []
    for e in events:
        t = e.get("type", "?")
        if t == "chat":
            lines.append("chat %s: %s" % (e.get("sender", "?"),
                                          str(e.get("message", ""))[:160]))
        elif t == "memory_save":
            lines.append("saved [%s] %s: %s" % (e.get("category", ""),
                                                e.get("key", ""),
                                                str(e.get("value", ""))[:120]))
        elif t == "memory_forget":
            lines.append("forgot: %s" % e.get("query", ""))
        elif t == "journal":
            lines.append("journal: %s" % str(e.get("entry", ""))[:160])
        elif t == "tool":
            lines.append("tool %s" % e.get("name", ""))
        elif t == "restart":
            lines.append("restart")
        elif t == "ptt":
            lines.append("push-to-talk %.1fs" % float(e.get("seconds") or 0))
        elif t == "autonomous_goal":
            lines.append("goal [%s]: %s" % (e.get("title", ""), str(e.get("result", ""))[:100]))
        elif t == "self_heal":
            lines.append("self-healed [%s] %s" % (e.get("category", ""), e.get("action", "")))
        elif t == "job_complete":
            lines.append("job done #%s (%s, code %s)" % (e.get("job_id", ""), e.get("name", ""), e.get("exit_code", "")))
        elif t == "download_complete":
            lines.append("downloaded: %s" % e.get("name", ""))
    return lines


def _spine_unbroken_thread(limit_chars=1500):
    """Compact resume of the spine + resume card, for the system prompt."""
    try:
        text = "\n".join(_spine_digest(_spine_tail(40)))[:1200]
        card = ""
        try:
            with open(_project_file("where_we_left_off.md", RESUME_PATH), encoding="utf-8") as f:
                card = f.read(600)
        except Exception as _e_silent:
            _config_silent.log_silent("_spine_unbroken_thread", _e_silent)
        out = text + ("\n--- where we left off ---\n" + card if card else "")
        return out[:limit_chars] or "(thread just starting)"
    except Exception:
        return "(thread just starting)"


def _spine_write_resume_card():
    """Shutdown handoff: where we left off. Instant, no LLM, never fails."""
    try:
        head = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        lines = ["# Where we left off \u2014 %s" % head, ""]
        chats = [e for e in _spine_tail(200) if e.get("type") == "chat"][-30:]
        lines.append("## Last conversation")
        if chats:
            for e in chats:
                lines.append("- **%s**: %s" % (e.get("sender", "?"),
                                              str(e.get("message", ""))[:400]))
        else:
            lines.append("(no chat this session)")
        lines.append("")
        lines.append("## Memories saved this session")
        if _SPINE_BOOT_KEYS:
            for k in _SPINE_BOOT_KEYS[-30:]:
                lines.append("- %s" % k)
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("## Pending scheduled tasks")
        rows = []
        try:
            with DB_LOCK:
                conn = sqlite3.connect(db_path())
                rows = conn.execute(
                    "SELECT id, kind, prompt, next_run FROM scheduled_tasks"
                    " ORDER BY next_run").fetchall()
                conn.close()
        except Exception:
            rows = []
        if rows:
            for rid, kind, prompt, nxt in rows:
                lines.append("- #%s [%s] %s: %s"
                             % (rid, kind, nxt, str(prompt or "")[:160]))
        else:
            lines.append("(none)")
        lines.append("")
        with open(_project_file("where_we_left_off.md", RESUME_PATH), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        try:
            add_log("Resume card failed: %s" % e)
        except Exception:
            pass



def init_databases():
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, key TEXT,
            value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        if 'embedding' not in {r[1] for r in cur.execute('PRAGMA table_info(memory)')}:
            cur.execute('ALTER TABLE memory ADD COLUMN embedding BLOB')
        columns = {r[1] for r in cur.execute("PRAGMA table_info(memory)")}
        for name, sql_type in (("source", "TEXT"), ("confidence", "REAL"),
                               ("embedding_model", "TEXT")):
            if name not in columns:
                cur.execute(f"ALTER TABLE memory ADD COLUMN {name} {sql_type}")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory(updated_at DESC)")
        # Ensure memory keys are unique and deduplicated
        try:
            cur.execute("DELETE FROM memory WHERE rowid NOT IN (SELECT MIN(rowid) FROM memory GROUP BY key)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_key ON memory(key)")
        except Exception as _e_silent:
            _config_silent.log_silent("init_databases", _e_silent)
        cur.execute('''CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            sender TEXT, message TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT,
            prompt TEXT, interval_s INTEGER, next_run TEXT, created TEXT,
            policy TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS autonomous_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            priority INTEGER DEFAULT 5,
            interval_s INTEGER DEFAULT 0,
            next_run REAL,
            status TEXT DEFAULT 'pending',
            last_result TEXT,
            policy TEXT,
            created_at REAL,
            updated_at REAL)''')
        # Pass 27 #16: origin-policy columns on pre-existing tables.
        for _table in ("scheduled_tasks", "autonomous_goals"):
            if "policy" not in {r[1] for r in cur.execute(
                    f"PRAGMA table_info({_table})")}:
                cur.execute(
                    f"ALTER TABLE {_table} ADD COLUMN policy TEXT")
        cur.execute('''CREATE TABLE IF NOT EXISTS autonomous_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            category TEXT,
            source TEXT,
            error_text TEXT,
            diagnosis TEXT,
            action_taken TEXT,
            resolved INTEGER DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS background_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            command TEXT,
            pid INTEGER,
            status TEXT DEFAULT 'running',
            exit_code INTEGER,
            started_at REAL,
            finished_at REAL,
            log_file TEXT)''')
        # pruning retired — the spine keeps the full thread, and the
        # DB now keeps every chat row, like the markdown log always did.
        # cur.execute("DELETE FROM chat_history WHERE timestamp < datetime('now', ?)",
        #             (f"-{CHAT_PRUNE_DAYS} days",))
        conn.commit()
        conn.close()


def _provider_mode_live() -> str:
    """The live PROVIDER_MODE, read fresh at call time (the
    Settings toggle rebinds it without a restart — an import-time
    snapshot would miss a flip). providers.provider_mode() is the
    authoritative source; config's global is the fallback."""
    try:
        from aria.agent.providers import provider_mode
        return str(provider_mode() or "auto").strip().lower()
    except Exception:
        pass
    try:
        from aria import config as _cfg
        return str(getattr(_cfg, "PROVIDER_MODE", "auto")
                   or "auto").strip().lower()
    except Exception:
        return "auto"


def _local_only() -> bool:
    return _provider_mode_live() == "local_only"


def _local_embed_model() -> str:
    """An explicitly configured LOCAL embedding model
    (OLLAMA_LOCAL_EMBED_MODEL in aria_keys.json / env), or ""
    when none is configured. Empty means local-only embedding has
    no egress at all — cached vectors only (see _embed)."""
    try:
        from aria.config import key_get
        value, _src = key_get("OLLAMA_LOCAL_EMBED_MODEL", "")
        return str(value or "").strip()
    except Exception:
        return ""


def _effective_embed_model():
    """(model, backend) _embed will use right now.

    local_only + a configured local model -> ("<model>", "local"):
    vectors come from the on-machine Ollama server. local_only
    with no local model -> (None, "none"): cached vectors only,
    never a POST to ollama.com. auto -> (EMBED_MODEL, "cloud"):
    the existing Ollama Cloud path, unchanged.
    """
    if _local_only():
        local = _local_embed_model()
        if local:
            return local, "local"
        return None, "none"
    return EMBED_MODEL, "cloud"


def _embed_uncached(text, model, backend):
    """One embedding from the given backend. Returns list[float]
    or None. The cloud path is the old _embed_uncached body
    (768-dim Ollama Cloud model, 5s timeout); the local path
    POSTs to the on-machine Ollama server with the explicitly
    configured local model — no API key, no usage metering (a
    local call has no price to reserve against)."""
    if backend == "local":
        try:
            from aria.config import OLLAMA_HOST
            base = str(OLLAMA_HOST or "http://localhost:11434").rstrip("/")
        except Exception:
            base = "http://localhost:11434"
        payload = {"model": model, "prompt": text[:2000]}
        body = json.dumps(payload).encode()
        try:
            req = urllib.request.Request(
                f"{base}/api/embeddings", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())["embedding"]
        except Exception as e:
            add_log(f"Embed err (local {model}): {e}")
            return None
    if not OLLAMA_CLOUD_API_KEY or OLLAMA_CLOUD_API_KEY == "INSERT":
        add_log("Embed err: no OLLAMA_API_KEY configured")
        return None
    payload = {"model": model, "prompt": text[:2000]}
    body = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(
            "https://ollama.com/api/embeddings", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {OLLAMA_CLOUD_API_KEY}"})
        # A5 (audit §1C #3): timeout cut 20s -> 5s. _embed feeds
        # build_prompt_memories, which builds the CURRENT turn's
        # system prompt — moving it async would silently change this
        # turn's retrieval to stale/no matches, a behaviour change
        # worse than the toll. So it stays synchronous and the toll is
        # capped instead: on timeout the turn proceeds with core
        # memories only (the graceful path that already existed).
        from aria import usage
        with usage.attempt('ollama_cloud', model, {**payload, 'max_tokens':0}, 'memory embedding'):
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())["embedding"]
    except urllib.error.HTTPError as e:
        if e.code == 429:
            quarantine_key(OLLAMA_CLOUD_API_KEY, 60, 429)
        elif e.code in (401, 403):
            quarantine_key(OLLAMA_CLOUD_API_KEY, code=e.code)
        else:
            add_log(f"Embed err: {e}")
        return None
    except Exception as e:
        add_log(f"Embed err: {e}")
        return None


def _cached_embedding(text, model):
    """A cached vector for (project, model, text), or None. The
    cache is keyed by MODEL identity, so vectors from different
    embedding models never mix silently — even when their
    dimensions happen to match."""
    cache_key = (workspaces.current(), model, text[:2000])
    with _EMBED_LOCK:
        hit = _EMBED_CACHE.get(cache_key)
        if hit and time.monotonic() - hit[0] < 3600:
            _EMBED_CACHE.move_to_end(cache_key)
            return list(hit[1])
    return None


def _embed_with_model(text):
    """(vector, model): the vector for text and the embedding
    model that produced it (None when there is none). In
    local_only with no configured local model this returns cached
    vectors only — no network egress at all — and callers fall
    back to lexical retrieval."""
    model, backend = _effective_embed_model()
    if backend == "none":
        # Cached vectors only. The cache key is the model that
        # produced the stored vectors (the configured local model
        # when there is one — there is not, in this branch — else
        # the cloud model from earlier saves).
        cache_model = _local_embed_model() or EMBED_MODEL
        vec = _cached_embedding(text, cache_model)
        return vec, (cache_model if vec else None)
    cached = _cached_embedding(text, model)
    if cached is not None:
        return cached, model
    vector = _embed_uncached(text, model, backend)
    if vector:
        cache_key = (workspaces.current(), model, text[:2000])
        with _EMBED_LOCK:
            _EMBED_CACHE[cache_key] = (time.monotonic(), tuple(vector))
            while len(_EMBED_CACHE) > 256:
                _EMBED_CACHE.popitem(last=False)
        return vector, model
    return None, None


def _embed(text):
    vec, _model = _embed_with_model(text)
    return vec


def _pack_embedding(vec):
    if not vec:
        return None
    return np.array(vec, dtype=np.float32).tobytes()


def memory_save(category: str, key: str, value: str, *, source="conversation", confidence=1.0):
    # every save joins the spine; the key feeds the resume card.
    spine_append("memory_save", {"category": category, "key": key,
                                 "value": value})
    _SPINE_BOOT_KEYS.append(key)
    vec, vec_model = _embed_with_model(f"{key}: {value}")
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute('''INSERT INTO memory (category, key, value, embedding, source, confidence, embedding_model, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                category=excluded.category,
                value=excluded.value,
                embedding=excluded.embedding,
                source=excluded.source, confidence=excluded.confidence,
                embedding_model=excluded.embedding_model,
                updated_at=excluded.updated_at''',
            (category, key, value, _pack_embedding(vec), source,
             max(0.0, min(1.0, float(confidence))), vec_model))
        conn.commit()
        conn.close()
    add_log(f"Memory saved: [{key}]")
    _queue_backup()


def memory_save_if_value(category: str, key: str, value: str,
                         expected_value, *, source="conversation",
                         confidence=1.0) -> bool:
    """Conditional save (pass 29 #50): replace the row only when
    its current value equals `expected_value`, or insert when no
    row exists and `expected_value` is None. Returns True when
    the save landed, False when a different live value (or a row
    that appeared since the caller looked) blocked it — nothing
    is written, nothing is archived, the caller keeps the
    contradiction pending instead of overwriting the newer
    value. The embedding is computed BEFORE DB_LOCK is taken, so
    the lock is never held across the provider call; the compare
    and the upsert run in one transaction under the lock, so no
    writer can slip between them."""
    vec, vec_model = _embed_with_model(f"{key}: {value}")
    packed = _pack_embedding(vec)
    clamped = max(0.0, min(1.0, float(confidence)))
    model = vec_model
    landed = False
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        try:
            cur = conn.cursor()
            if expected_value is None:
                row = cur.execute(
                    "SELECT value FROM memory WHERE key = ?",
                    (key,)).fetchone()
                if row is None:
                    cur.execute(
                        """INSERT INTO memory (category, key, value,
                           embedding, source, confidence,
                           embedding_model, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?,
                           CURRENT_TIMESTAMP)""",
                        (category, key, value, packed, source,
                         clamped, model))
                    landed = True
            else:
                cur.execute(
                    """UPDATE memory SET category=?, value=?,
                       embedding=?, source=?, confidence=?,
                       embedding_model=?, updated_at=CURRENT_TIMESTAMP
                       WHERE key=? AND value=?""",
                    (category, value, packed, source, clamped,
                     model, key, expected_value))
                landed = cur.rowcount > 0
            if landed:
                conn.commit()
        finally:
            conn.close()
    if not landed:
        return False
    # Same bookkeeping as memory_save, but only on a landed save.
    spine_append("memory_save", {"category": category, "key": key,
                                 "value": value})
    _SPINE_BOOT_KEYS.append(key)
    add_log(f"Memory saved: [{key}]")
    _queue_backup()
    return True


def memory_get(category: str, key: str) -> str:
    """Read a single memory value by category+key. Returns "" when absent."""
    try:
        with DB_LOCK:
            conn = sqlite3.connect(db_path())
            cur = conn.cursor()
            r = cur.execute("SELECT value FROM memory WHERE category=? AND key=?",
                            (category, key)).fetchone()
            conn.close()
        return r[0] if r else ""
    except Exception:
        return ""


# --- Relevance grading for semantic recall (Self-RAG "IsRel") ---
# The 0.35 cosine cutoff in memory_search_semantic is a weak relevance
# proxy: a vaguely-related memory over the line enters the agent's
# context unexamined, and a useful one under it silently vanishes.
# After the hit list is formed, ONE light-tier model call (the Jev
# chain, started at its light leg via resolve_tier_chain_start) judges
# which hits are actually relevant to the query. The grader can only
# REMOVE hits — never add, never reorder — and every failure mode
# (grader raising, no provider answering, unparseable reply, kill
# switch) returns the ungraded list untouched, so a broken grader is
# indistinguishable from no grader. Kill switch:
# usage.settings()['memory_grading'] = {'enabled': False}.
# Deviation from the ideal shape: the provider chain exposes no
# per-call max_tokens knob (payloads take usage.max_output_tokens()),
# so the reply size is bounded by instruction instead — the grader is
# told to answer with bare numbers only. Per-attempt timeouts are the
# chain's own (8s foreground / 30s otherwise).

_GRADER_SYSTEM = (
    "You filter recalled memories for a personal assistant. Given a "
    "query and a numbered list of candidate memories, reply with ONLY "
    "the numbers of the memories that are genuinely relevant to "
    "answering the query, comma-separated (example: 1, 3). If none are "
    "relevant, reply with exactly: NONE. No explanation, no other text."
)


def _memory_grade_call(query: str, hits) -> Optional[str]:
    """One light-tier call asking which numbered hits are relevant to
    the query. Returns the raw reply text, or None when no provider
    answered (the caller treats that as fail-open)."""
    from aria.agent.providers import provider_call, resolve_tier_chain_start
    listing = "\n".join(
        f"{i}. [{c}] {k}: {v}" for i, (c, k, v) in enumerate(hits, 1))
    user = (f"Query: {query}\n\nCandidate memories:\n{listing}\n\n"
            "Relevant numbers:")
    data = provider_call(_GRADER_SYSTEM,
                         [{"role": "user", "parts": [{"text": user}]}],
                         chain_start=resolve_tier_chain_start("light"))
    if not data or not data.get("candidates"):
        return None
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return None
    text = "".join(p.get("text", "") for p in parts
                   if isinstance(p, dict)).strip()
    return text or None


def _parse_grade_reply(raw: Optional[str], count: int):
    """Map a grader reply to the set of 0-based hit indices to KEEP.
    Returns None when the reply is unusable (the fail-open signal);
    an empty set is a real verdict: nothing was relevant."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    nums = [int(n) for n in re.findall(r"\d+", text)]
    keep = {n - 1 for n in nums if 1 <= n <= count}
    if keep:
        return keep
    if nums:
        return None  # numbers, but none pointed at a real hit
    if text.upper().startswith("NONE"):
        return set()
    return None


def _grade_memory_hits(query: str, hits):
    """Grade semantic hits for relevance; fail-open on any problem.
    Returns a subsequence of hits (order preserved) — or hits itself
    whenever grading is disabled or anything goes wrong."""
    try:
        if not hits:
            return hits
        from aria import usage
        cfg = usage.settings().get("memory_grading") or {}
        if cfg.get("enabled") is False:
            return hits
        keep = _parse_grade_reply(_memory_grade_call(query, hits),
                                  len(hits))
        if keep is None:
            return hits
        kept = [h for i, h in enumerate(hits) if i in keep]
        for i, (c, k, v) in enumerate(hits):
            if i not in keep:
                add_log(f"Memory grading dropped: [{k}] "
                        f"(not relevant to '{str(query)[:60]}')")
        return kept
    except Exception as e:
        from aria import runtime as _runtime
        if isinstance(e, _runtime.Cancelled):
            raise
        _config_silent.log_silent("memory_grading", e)
        return hits


def memory_search_semantic(query: str, top_k: int = 5) -> str:
    """Search memories by meaning. Falls back to keyword search."""
    qvec = _embed(query)
    rows = []
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("SELECT category, key, value, embedding FROM memory")
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return "No relevant memories found."
    if qvec:
        q = np.array(qvec, dtype=np.float32)
        q /= (np.linalg.norm(q) or 1)
        scored = []
        qlen = len(q)
        for cat, k, v, emb in rows:
            if not emb:
                continue
            e = np.frombuffer(emb, dtype=np.float32)
            if len(e) != qlen:
                continue  # stored under a retired embedding model; skip until re-saved
            e = e / (np.linalg.norm(e) or 1)
            scored.append((float(np.dot(q, e)), cat, k, v))
        scored.sort(reverse=True)
        hits = [(c, k, v) for s, c, k, v in scored[:top_k] if s > 0.35]
        if hits:
            # IsRel grading (see _grade_memory_hits): the light-tier
            # grader may drop hits it judges irrelevant, and its
            # verdict stands — an all-irrelevant verdict does NOT fall
            # through to the keyword fallback below. Any grader
            # failure returns the ungraded list unchanged.
            hits = _grade_memory_hits(query, hits)
            if not hits:
                return "No relevant memories found."
            return "\n".join(f"• [{c}] {k}: {v}" for c, k, v in hits)
    # fallback: keyword
    ql = query.lower()
    hits = [f"• [{c}] {k}: {v}" for c, k, v, _ in rows
            if ql in k.lower() or ql in v.lower()][:top_k]
    return "\n".join(hits) if hits else "No relevant memories found."


def memory_get_all() -> str:
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("SELECT category, key, value FROM memory WHERE key NOT LIKE '\\_%' ESCAPE '\\' ORDER BY updated_at DESC LIMIT 15")
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return "Memory bank is currently empty."
    return "\n".join(f"• [{c}] {k}: {v}" for c, k, v in rows)


def memory_rows_for_ui() -> List[Dict[str, str]]:
    """Every displayable stored fact for the dashboard Memory tab
    (Alek, 2026-10-27, Mark-LVI parity): category, key, value and
    the date learned, newest first. Same visibility rule as
    memory_get_all (keys starting '_' stay hidden) but no
    15-row cap — the tab is the whole store, inspectable. An
    unreadable store reads as empty, never as an exception."""
    try:
        with DB_LOCK:
            conn = sqlite3.connect(db_path())
            cur = conn.cursor()
            cur.execute("SELECT category, key, value, updated_at, source, confidence, embedding_model FROM memory "
                        "WHERE key NOT LIKE '\\_%' ESCAPE '\\' "
                        "ORDER BY updated_at DESC LIMIT 500")
            rows = cur.fetchall()
            conn.close()
    except Exception:
        return []
    return [{"category": c or "", "key": k, "value": v or "",
             "updated": (u or "")[:10], "source": source or "legacy",
             "confidence": confidence, "embedding_model": model}
            for c, k, v, u, source, confidence, model in rows]


def memory_forget_key(key: str) -> bool:
    """Forget exactly one stored fact by key (the Memory tab's
    per-row ✕). True when a row was actually removed."""
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("DELETE FROM memory WHERE key=?", (key,))
        gone = cur.rowcount
        conn.commit()
        conn.close()
    if gone:
        add_log(f"Memory forgotten: [{key}]")
    return bool(gone)


def memory_map_points() -> List[Dict[str, Any]]:
    """Rows + embedding vectors for the Memory constellation
    (Alek, 2026-10-27). Same visibility rule as the Memory tab;
    'vec' is a float32 numpy array, or None for facts saved
    without one (older rows) — the layout parks those on a
    category ring. An unreadable store reads as empty."""
    try:
        with DB_LOCK:
            conn = sqlite3.connect(db_path())
            cur = conn.cursor()
            cur.execute("SELECT category, key, value, updated_at, embedding "
                        "FROM memory WHERE key NOT LIKE '\\_%' ESCAPE '\\' "
                        "ORDER BY updated_at DESC LIMIT 500")
            rows = cur.fetchall()
            conn.close()
    except Exception:
        return []
    out = []
    for c, k, v, u, emb in rows:
        vec = None
        if emb:
            try:
                arr = np.frombuffer(emb, dtype=np.float32)
                if arr.size:
                    vec = arr
            except Exception:
                vec = None
        out.append({"category": c or "", "key": k, "value": v or "",
                    "updated": (u or "")[:10], "vec": vec})
    return out


def _skills_catalog_suffix() -> str:
    """Skills v1: the learned-skills catalog (names + one-liners +
    status only, capped in skill_store) rides at the end of the
    prompt memory block. Empty when skills are disabled or none
    exist, so legacy prompts stay byte-identical; fail-silent — a
    broken skill store must never break prompt assembly."""
    try:
        from aria.tools.skill_store import catalog_block
        block = catalog_block()
        return f"\n{block}" if block else ""
    except Exception:
        return ""


def build_prompt_memories(query: str = "", top_k: int = 6, *, local_only=False) -> str:
    """Build a compact, highly relevant memory block for LLM system prompt.
    Always includes core identity facts, dynamic semantic search matches,
    and excludes internal ephemeral keys (prefixed with _)."""
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("SELECT category, key, value FROM memory WHERE key NOT LIKE '\\_%' ESCAPE '\\' ORDER BY updated_at DESC, id DESC")
        all_rows = cur.fetchall()
        conn.close()

    if not all_rows:
        return "Memory bank is currently empty." + _skills_catalog_suffix()

    selected = {}
    # 1. Always prioritize core identity & preference memories
    core_keys = {"user_name", "user_timezone", "user_style"}
    for cat, k, v in all_rows:
        if k in core_keys:
            selected[k] = (cat, v)

    # 2. Add semantically relevant memories if query exists
    if query:
        # Pass 36 #02: in local_only this is cached vectors only —
        # _embed() itself is gated, and the cache is keyed by the
        # model that produced the vectors.
        qvec = _cached_embedding(
            query, _local_embed_model() or EMBED_MODEL) if local_only else _embed(query)
        if qvec:
            q = np.array(qvec, dtype=np.float32)
            q /= (np.linalg.norm(q) or 1)
            qlen = len(q)
            scored = []
            with DB_LOCK:
                conn = sqlite3.connect(db_path())
                cur = conn.cursor()
                cur.execute("SELECT category, key, value, embedding FROM memory WHERE embedding IS NOT NULL AND key NOT LIKE '\\_%' ESCAPE '\\'")
                rows_with_emb = cur.fetchall()
                conn.close()
            for cat, k, v, emb in rows_with_emb:
                if k in selected or not emb:
                    continue
                e = np.frombuffer(emb, dtype=np.float32)
                if len(e) != qlen:
                    continue
                denom = np.linalg.norm(e) or 1
                sim = float(np.dot(q, e / denom))
                if sim >= 0.45:
                    scored.append((sim, cat, k, v))
            scored.sort(key=lambda x: x[0], reverse=True)
            for _, cat, k, v in scored[:top_k]:
                if len(selected) >= top_k + len(core_keys):
                    break
                selected[k] = (cat, v)

    # Foreground prompts never wait on an embedding service and never insert
    # unrelated recent tasks simply because they were saved most recently.
    if local_only and query:
        stop = {'the', 'and', 'for', 'that', 'this', 'what', 'with', 'you', 'your',
                'can', 'could', 'would', 'please', 'are', 'how', 'does', 'about',
                'do', 'did', 'is', 'it', 'me', 'my', 'we', 'to', 'of', 'in', 'on',
                'an', 'as', 'be', 'by', 'or', 'at', 'am', 'if', 'so', 'not', 'just'}
        terms = set(re.findall(r'[^\W_]{2,}', query.casefold())) - stop
        ranked = []
        for index, (cat, key, value) in enumerate(all_rows):
            if key in selected:
                continue
            labels = set(re.findall(r'[^\W_]{2,}', f'{cat} {key}'.casefold()))
            words = set(re.findall(r'[^\W_]{2,}', str(value).casefold()))
            score = 2 * len(terms & labels) + len(terms & words)
            if score:
                ranked.append((-score, index, cat, key, value))
        remaining = min(top_k, max(0, top_k + len(core_keys) - len(selected)))
        for _, _, cat, key, value in sorted(ranked)[:remaining]:
            selected[key] = (cat, value)
    # Full semantic recall retains the existing recent-fact fallback.
    if not local_only and len(selected) < top_k + len(core_keys):
        for cat, k, v in all_rows[:top_k + len(core_keys)]:
            if k not in selected:
                selected[k] = (cat, v)
            if len(selected) >= top_k + len(core_keys):
                break

    return "\n".join(f"• [{cat}] {k}: {v}" for k, (cat, v) in selected.items()) + _skills_catalog_suffix()





# Public spine aliases
spine_unbroken_thread = _spine_unbroken_thread
spine_tail = _spine_tail
spine_digest = _spine_digest
spine_write_resume_card = _spine_write_resume_card


DISPLAY_CHAT_LOG = []
_CHAT_LOG_LISTENERS = []

def register_chat_listener(fn):
    if fn not in _CHAT_LOG_LISTENERS:
        _CHAT_LOG_LISTENERS.append(fn)

_PROJECT_DISPLAY_LOGS = {}


def get_display_chat_log():
    if workspaces.current() == 'default':
        return DISPLAY_CHAT_LOG
    path = db_path()
    if path not in _PROJECT_DISPLAY_LOGS:
        with DB_LOCK:
            with sqlite3.connect(path) as conn:
                rows = conn.execute('SELECT timestamp,sender,message FROM chat_history ORDER BY id DESC LIMIT 60').fetchall()
            conn.close()
        _PROJECT_DISPLAY_LOGS[path] = [(str(t)[11:19], who, msg) for t, who, msg in reversed(rows)]
    return _PROJECT_DISPLAY_LOGS[path]

def log_conversation(sender: str, message: str):
    redacted_msg = redact(str(message))
    spine_append("chat", {"sender": sender, "message": redacted_msg})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts_short = datetime.now().strftime("%H:%M:%S")
    display = get_display_chat_log()
    display.append((ts_short, sender, redacted_msg))
    del display[:-60]

    for listener in _CHAT_LOG_LISTENERS:
        try:
            listener(ts_short, sender, redacted_msg)
        except Exception as _e_silent:
            _config_silent.log_silent("log_conversation", _e_silent)

    with DB_LOCK:
        try:
            conn = sqlite3.connect(db_path())
            conn.execute("INSERT INTO chat_history (timestamp, sender, message) VALUES (?, ?, ?)",
                         (ts, sender, redacted_msg))
            conn.commit()
            conn.close()
        except Exception as e:
            add_log(f"DB err: {e}")

    try:
        need_header = not os.path.exists(_project_file("chat_history.md", CHAT_LOG_FILE)) or os.path.getsize(_project_file("chat_history.md", CHAT_LOG_FILE)) == 0
        with open(_project_file("chat_history.md", CHAT_LOG_FILE), "a", encoding="utf-8") as f:
            if need_header:
                f.write("# A.R.I.A. Master Conversation & Action Log\n\n---\n")
            f.write(f"\n### [{ts}] {sender.upper()}:\n{redacted_msg}\n")
    except Exception as e:
        add_log(f"File log err: {e}")
    _queue_backup()


_MEMORY_DB_OK = None
_MEMORY_DB_T = 0.0

def memory_db_probe() -> bool:
    """Cached semantic-memory DB probe (at most one probe per 10s)."""
    global _MEMORY_DB_OK, _MEMORY_DB_T
    now = time.time()
    if _MEMORY_DB_OK is None or now - _MEMORY_DB_T > 10:
        try:
            with DB_LOCK:
                conn = sqlite3.connect(db_path())
                conn.execute("SELECT 1").fetchone()
                conn.close()
            _MEMORY_DB_OK = True
        except Exception:
            _MEMORY_DB_OK = False
        _MEMORY_DB_T = now
    return bool(_MEMORY_DB_OK)


def memory_forget_entries(query: str) -> int:
    """Delete non-system memories matching keyword. Returns count of deleted rows.

    The keyword is a LITERAL substring (pass 25): LIKE
    metacharacters in it are escaped, so a query of "%" deletes
    nothing instead of every row."""
    q = (query or "").strip().lower()
    if not q:
        return 0
    escaped = (q.replace("\\", "\\\\").replace("%", "\\%")
                 .replace("_", "\\_"))
    pattern = f"%{escaped}%"
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("SELECT id FROM memory WHERE category != 'system' AND (lower(key) LIKE ? ESCAPE '\\' OR lower(value) LIKE ? ESCAPE '\\')",
                    (pattern, pattern))
        ids = [r[0] for r in cur.fetchall()]
        for rid in ids:
            cur.execute("DELETE FROM memory WHERE id = ?", (rid,))
        conn.commit()
        conn.close()
    return len(ids)


# --- Autonomous Goals, Incidents, & Background Jobs Database Helpers ---

def ensure_policy_columns():
    """Idempotently add the pass-27-#16 origin-policy columns to
    scheduled_tasks and autonomous_goals (same migration as
    init_databases, for callers that touch the tables directly
    without a boot-time init — e.g. the scheduler loop)."""
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        try:
            for table in ("scheduled_tasks", "autonomous_goals"):
                cols = {r[1] for r in conn.execute(
                    f"PRAGMA table_info({table})")}
                if "policy" not in cols:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN policy TEXT")
            conn.commit()
        finally:
            conn.close()


def goal_db_create(title: str, description: str, interval_s: int = 0, priority: int = 5, delay_s: int = 0, policy=None) -> int:
    now = time.time()
    next_run = now + max(0, delay_s)
    # Pass 27 #16: persist the originating policy (mission
    # envelope, budget scope, spend cap) so the goal's later
    # execution restores it instead of running uncapped from
    # the poller context. An explicit policy wins; otherwise
    # snapshot the creating execution.
    if policy is None:
        try:
            from aria import runtime as _runtime
            policy = _runtime.origin_policy_snapshot()
        except Exception:
            policy = {}
    policy_json = json.dumps(policy or {}, default=str)
    ensure_policy_columns()
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("""INSERT INTO autonomous_goals
                   (title, description, priority, interval_s, next_run, status, policy, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (title, description, priority, interval_s, next_run, policy_json, now, now))
        gid = cur.lastrowid
        conn.commit()
        conn.close()
    return gid


def goal_db_list(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if status_filter:
            rows = cur.execute(
                "SELECT * FROM autonomous_goals WHERE status = ? ORDER BY priority ASC, next_run ASC",
                (status_filter,)).fetchall()
        else:
            rows = cur.execute(
                "SELECT * FROM autonomous_goals ORDER BY priority ASC, next_run ASC").fetchall()
        out = [dict(r) for r in rows]
        conn.close()
    return out


def goal_db_get_due(now_ts: float) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT * FROM autonomous_goals WHERE status IN ('pending', 'active') AND next_run <= ? ORDER BY priority ASC LIMIT 5",
            (now_ts,)).fetchall()
        out = [dict(r) for r in rows]
        conn.close()
    return out


def goal_db_update(goal_id: int, status: Optional[str] = None, last_result: Optional[str] = None,
                   next_run: Optional[float] = None) -> bool:
    now = time.time()
    updates = ["updated_at = ?"]
    params: List[Any] = [now]
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if last_result is not None:
        updates.append("last_result = ?")
        params.append(last_result)
    if next_run is not None:
        updates.append("next_run = ?")
        params.append(next_run)
    params.append(goal_id)

    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute(f"UPDATE autonomous_goals SET {', '.join(updates)} WHERE id = ?", params)
        affected = cur.rowcount
        conn.commit()
        conn.close()
    return affected > 0


def goal_db_delete(goal_id: int) -> bool:
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("DELETE FROM autonomous_goals WHERE id = ?", (goal_id,))
        affected = cur.rowcount
        conn.commit()
        conn.close()
    return affected > 0


def incident_db_log(category: str, source: str, error_text: str, diagnosis: str,
                    action_taken: str, resolved: int = 0) -> int:
    now = time.time()
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("""INSERT INTO autonomous_incidents
                   (timestamp, category, source, error_text, diagnosis, action_taken, resolved)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (now, category, source, str(error_text)[:1000], str(diagnosis)[:1000],
                     str(action_taken)[:1000], 1 if resolved else 0))
        iid = cur.lastrowid
        conn.commit()
        conn.close()
    return iid


def incident_db_list(limit: int = 10) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT * FROM autonomous_incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = [dict(r) for r in rows]
        conn.close()
    return out


def job_db_create(name: str, command: str, pid: int, log_file: str) -> int:
    now = time.time()
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("""INSERT INTO background_jobs
                   (name, command, pid, status, started_at, log_file)
                   VALUES (?, ?, ?, 'running', ?, ?)""",
                    (name or command[:30], command, pid, now, log_file))
        jid = cur.lastrowid
        conn.commit()
        conn.close()
    return jid


def job_db_update(job_id: int, status: str, exit_code: Optional[int] = None) -> bool:
    now = time.time()
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("""UPDATE background_jobs
                   SET status = ?, exit_code = ?, finished_at = ?
                   WHERE id = ?""",
                    (status, exit_code, now, job_id))
        affected = cur.rowcount
        conn.commit()
        conn.close()
    return affected > 0


def job_db_list(limit: int = 10) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT * FROM background_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = [dict(r) for r in rows]
        conn.close()
    return out


def job_db_get(job_id) -> Optional[Dict[str, Any]]:
    """One background-job row by id (pass 28 #23): a child-job
    lookup must not depend on a recency window — list-based
    lookups mistake any job older than the window for missing.
    Returns None for an unknown id or an unparsable one."""
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        return None
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute(
            "SELECT * FROM background_jobs WHERE id = ?",
            (jid,)).fetchone()
        conn.close()
    return dict(row) if row is not None else None
