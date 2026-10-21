"""
Shared cooldown registry (Merger Phase 2 — blueprint SS3.1).

ONE mechanism, two scopes. Fork A's A5 gave the conversation
provider chain a per-provider failure cooldown (a provider that just
failed is skipped for a window instead of being re-walked at full
timeout cost every turn); Mark-XXXIX-OR's or_client has the same
idea per-model for his OpenRouter pool. The blueprint's ruling:
extract the mechanism, share it — do not duplicate it a third time.

Scopes served by this class:
  * aria.agent.providers — per-PROVIDER cooldown for the live chain
    (60 s; the HTTP-400 carve-out lives in providers, not here: a
    400 is our malformed payload, not a sick provider).
  * aria.agent.orpool — per-MODEL 429 cooldown for the background
    OpenRouter pool (60 s).

The pattern itself — timestamp registry, skip while cooling, clear
on success — is Mark's (FatihMakes, Mark-XXXIX-OR, CC BY-NC 4.0),
reimplemented once here instead of copied twice.

Deliberately NOT folded in: providers' role-model parking registry
(900 s) predates A5, has different semantics (park a model TAG, not
a provider), and is exercised by tests through its dict shape.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional


class CooldownRegistry:
    """Thread-safe key -> cooldown-expiry registry.

    mark_failed(key) parks the key for duration_s (or an explicit
    duration); in_cooldown(key) is True until the expiry passes;
    mark_ok(key) clears it immediately (success heals).
    """

    def __init__(self, duration_s: float,
                 time_fn: Optional[Callable[[], float]] = None):
        self.duration_s = float(duration_s)
        self._time = time_fn or time.time
        self._until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def in_cooldown(self, key: str) -> bool:
        with self._lock:
            return self._until.get(key, 0.0) > self._time()

    def mark_failed(self, key: str, duration_s: Optional[float] = None) -> None:
        with self._lock:
            self._until[key] = self._time() + (
                self.duration_s if duration_s is None else float(duration_s))

    def mark_ok(self, key: str) -> None:
        with self._lock:
            self._until.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._until.clear()

    def until(self, key: str) -> float:
        """Absolute expiry epoch for key (0 when not cooling)."""
        with self._lock:
            return self._until.get(key, 0.0)

    def cooling_keys(self) -> List[str]:
        """Keys currently inside their cooldown window."""
        now = self._time()
        with self._lock:
            return [k for k, u in self._until.items() if u > now]
