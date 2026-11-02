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
            prompt TEXT, interval_s INTEGER, next_run TEXT, created TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS autonomous_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            priority INTEGER DEFAULT 5,
            interval_s INTEGER DEFAULT 0,
            next_run REAL,
            status TEXT DEFAULT 'pending',
            last_result TEXT,
            created_at REAL,
            updated_at REAL)''')
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


def _embed_uncached(text):
    """Ollama Cloud embedding (768-dim, same as the retired model).
    Returns list[float] or None."""
    if not OLLAMA_CLOUD_API_KEY or OLLAMA_CLOUD_API_KEY == "INSERT":
        add_log("Embed err: no OLLAMA_API_KEY configured")
        return None
    payload = {"model": EMBED_MODEL, "prompt": text[:2000]}
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
        with usage.attempt('ollama_cloud', EMBED_MODEL, {**payload, 'max_tokens':0}, 'memory embedding'):
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


def _cached_embedding(text):
    cache_key = (workspaces.current(), EMBED_MODEL, text[:2000])
    with _EMBED_LOCK:
        hit = _EMBED_CACHE.get(cache_key)
        if hit and time.monotonic() - hit[0] < 3600:
            _EMBED_CACHE.move_to_end(cache_key)
            return list(hit[1])
    return None


def _embed(text):
    cached = _cached_embedding(text)
    if cached is not None:
        return cached
    cache_key = (workspaces.current(), EMBED_MODEL, text[:2000])
    vector = _embed_uncached(text)
    if vector:
        with _EMBED_LOCK:
            _EMBED_CACHE[cache_key] = (time.monotonic(), tuple(vector))
            while len(_EMBED_CACHE) > 256:
                _EMBED_CACHE.popitem(last=False)
    return vector


def _pack_embedding(vec):
    if not vec:
        return None
    return np.array(vec, dtype=np.float32).tobytes()


def memory_save(category: str, key: str, value: str, *, source="conversation", confidence=1.0):
    # every save joins the spine; the key feeds the resume card.
    spine_append("memory_save", {"category": category, "key": key,
                                 "value": value})
    _SPINE_BOOT_KEYS.append(key)
    vec = _embed(f"{key}: {value}")
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
             max(0.0, min(1.0, float(confidence))), EMBED_MODEL if vec else None))
        conn.commit()
        conn.close()
    add_log(f"Memory saved: [{key}]")
    _queue_backup()


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
        return "Memory bank is currently empty."

    selected = {}
    # 1. Always prioritize core identity & preference memories
    core_keys = {"user_name", "user_timezone", "user_style"}
    for cat, k, v in all_rows:
        if k in core_keys:
            selected[k] = (cat, v)

    # 2. Add semantically relevant memories if query exists
    if query:
        qvec = _cached_embedding(query) if local_only else _embed(query)
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

    return "\n".join(f"• [{cat}] {k}: {v}" for k, (cat, v) in selected.items())





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
    """Delete non-system memories matching keyword. Returns count of deleted rows."""
    q = (query or "").strip().lower()
    if not q:
        return 0
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("SELECT id FROM memory WHERE category != 'system' AND (lower(key) LIKE ? OR lower(value) LIKE ?)",
                    (f"%{q}%", f"%{q}%"))
        ids = [r[0] for r in cur.fetchall()]
        for rid in ids:
            cur.execute("DELETE FROM memory WHERE id = ?", (rid,))
        conn.commit()
        conn.close()
    return len(ids)


# --- Autonomous Goals, Incidents, & Background Jobs Database Helpers ---

def goal_db_create(title: str, description: str, interval_s: int = 0, priority: int = 5, delay_s: int = 0) -> int:
    now = time.time()
    next_run = now + max(0, delay_s)
    with DB_LOCK:
        conn = sqlite3.connect(db_path())
        cur = conn.cursor()
        cur.execute("""INSERT INTO autonomous_goals
                   (title, description, priority, interval_s, next_run, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (title, description, priority, interval_s, next_run, now, now))
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
