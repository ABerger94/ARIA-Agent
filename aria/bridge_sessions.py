"""Remembered phone sessions. Only token hashes are persisted on the laptop."""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import tempfile
import threading
import time

SESSION_SECONDS = 30 * 86400


class BridgeSessions:
    def __init__(self, path, clock=time.time):
        self.path = os.fspath(path)
        self.clock = clock
        self.lock = threading.Lock()
        self.entries = {}
        self.last_seen = 0.0
        try:
            with open(self.path, encoding='utf-8') as stream:
                raw = json.load(stream)
            if isinstance(raw, dict):
                self.entries = {k: v for k, v in raw.items()
                                if isinstance(k, str) and len(k) == 64
                                and isinstance(v, (int, float)) and math.isfinite(v)
                                and v > clock()}
        except (OSError, ValueError, TypeError):
            pass  # Missing or invalid session data never grants access.

    def _write(self, entries):
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.bridge-sessions-', dir=parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(entries, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def issue(self):
        with self.lock:
            token = secrets.token_urlsafe(32)
            entries = {k: v for k, v in self.entries.items() if v > self.clock()}
            entries[hashlib.sha256(token.encode()).hexdigest()] = self.clock() + SESSION_SECONDS
            self._write(entries)  # Don't grant a cookie if persistence failed.
            self.entries = entries
            self.last_seen = self.clock()
            return token

    def valid(self, token):
        if not token or not isinstance(token, str) or len(token) > 256:
            return False
        with self.lock:
            digest = hashlib.sha256(token.encode()).hexdigest()
            if self.entries.get(digest, 0) <= self.clock():
                return False
            self.last_seen = self.clock()
            return True

    def revoke(self, token=None):
        with self.lock:
            entries = dict(self.entries)
            if token:
                entries.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            else:
                entries.clear()
            self._write(entries)
            self.entries = entries
            if not entries:
                self.last_seen = 0.0

    def status(self):
        with self.lock:
            return {'paired_devices': sum(v > self.clock() for v in self.entries.values()),
                    'recent_connection': bool(self.last_seen and self.clock() - self.last_seen < 15)}
