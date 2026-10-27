"""App lock (Alek, 2026-10-27): a Lock button in Settings ->
General grays out the dashboard and refuses local voice until
unlocked — typed on the lock overlay, or by voice: "Hey Aria"
and then the password.

Scope, his word: the DASHBOARD and the laptop VOICE lock. The
phone bridge deliberately keeps working — he runs ARIA from his
phone while the laptop sits at home, and the lock exists for the
machine left running in the house, not for him.

The password is never stored: only a salted PBKDF2-HMAC-SHA256
hash in the settings store. Passwords are normalized (trimmed,
case-folded, trailing punctuation dropped) because the same
password has to survive speech recognition — a spoken password
has no case. The lock itself is per-session state: restarting
ARIA starts her unlocked; the password persists.
"""

import base64
import hashlib
import hmac
import os

_SETTING_KEY = "lock_password_hash"
_ITERATIONS = 200_000

_LOCKED = False


# ------------------------------------------------------------ password

def _normalize(candidate) -> str:
    text = str(candidate or "").strip().casefold()
    return text.rstrip(" .!?,;:'\"")


def password_is_set() -> bool:
    try:
        from aria import config
        return bool(config.get_setting(_SETTING_KEY))
    except Exception:
        return False


def set_password(password) -> bool:
    """Store a fresh salted hash for `password`. False on an
    empty password or a failed save."""
    normalized = _normalize(password)
    if not normalized:
        return False
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", normalized.encode("utf-8"), salt, _ITERATIONS)
    stored = "{}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"))
    try:
        from aria import config
        setter = getattr(config, "set_setting", None)
        if callable(setter):
            return bool(setter(_SETTING_KEY, stored))
        data = config.settings_load()
        data[_SETTING_KEY] = stored
        return bool(config.settings_save(data))
    except Exception:
        return False


def verify_password(candidate) -> bool:
    """Constant-time check of `candidate` against the stored
    hash. False when no password is set or anything is malformed
    — a broken store must fail closed, never open."""
    normalized = _normalize(candidate)
    if not normalized:
        return False
    try:
        from aria import config
        stored = config.get_setting(_SETTING_KEY)
        salt_b64, digest_b64 = str(stored).split("$", 1)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", normalized.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(digest, expected)


# ------------------------------------------------------------ state

def is_locked() -> bool:
    return _LOCKED


def set_locked(value) -> None:
    global _LOCKED
    _LOCKED = bool(value)


def lock() -> None:
    set_locked(True)


def unlock_with(candidate) -> bool:
    """Verify and, on success, clear the lock. The one call the
    overlay and the wake-word loop both use."""
    if verify_password(candidate):
        set_locked(False)
        return True
    return False
