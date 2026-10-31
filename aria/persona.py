"""Personality engine (groundwork): persona overlays.

A persona is a short style prompt fragment in aria/personas/<name>.md — it is
NEVER an identity replacement (the soul stays the soul). The active persona
name persists in persona.json at the install root; default is none (cleared).

Coordinator wiring (integration pass):
  - brain.build_system_instruction: append the persona overlay under a
    "Persona overlay" header when set. Exact snippet:

        from aria import persona as _persona
        _overlay = _persona.persona_overlay()
        if _overlay:
            prompt += "\n\n## Persona overlay\n" + _overlay

  - tool registry (fold into 'admin' toolkit per spec):
        "set_persona"    -> lambda a: persona.tool_set_persona(a.get("name", ""))
        "list_personas"  -> lambda a: persona.tool_list_personas()
        "get_persona"    -> lambda a: persona.tool_get_persona()

Top-level imports here are stdlib + aria.config only (no brain, no dispatch).
"""

import json
import os

from aria import config as _config

_PERSONAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas")
# Persona storage lives at the install root (V20, aria/data_dir.py;
# pre-V20 ~/ARIA). _config.ROOT_DIR is that root.
_ARIA_HOME = _config.ROOT_DIR
_STORAGE_FILE = os.path.join(_ARIA_HOME, "persona.json")


def _storage_file():
    # indirection so tests can monkeypatch aria.persona._STORAGE_FILE
    return _STORAGE_FILE


def _ensure_migrated():
    """One-time copy of a real legacy ~/ARIA/persona.json to
    the install root (V20), via aria.data_dir — canonical wins,
    legacy left untouched. Only when the storage file has not
    been redirected (tests, embedders). Fail-soft."""
    try:
        if os.path.normpath(_storage_file()) != os.path.normpath(
                _STORAGE_FILE):
            return
        from aria import data_dir
        data_dir.migrate_file("persona.json")
    except Exception:
        pass


def list_personas():
    """Names of available persona overlays (from aria/personas/*.md)."""
    names = []
    try:
        if os.path.isdir(_PERSONAS_DIR):
            for fname in sorted(os.listdir(_PERSONAS_DIR)):
                if fname.endswith(".md") and not fname.startswith("_"):
                    names.append(fname[:-3])
    except Exception as _e:
        _config.add_log(f"[persona] list failed: {_config.redact(str(_e))}")
    return names


def _read_storage():
    _ensure_migrated()
    try:
        with open(_storage_file(), encoding="utf-8") as _f:
            data = json.load(_f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as _e:
        _config.add_log(f"[persona] storage read failed: {_config.redact(str(_e))}")
    return {}


def _write_storage(data):
    _ensure_migrated()
    try:
        os.makedirs(os.path.dirname(_storage_file()), exist_ok=True)
        with open(_storage_file(), "w", encoding="utf-8") as _f:
            json.dump(data, _f, indent=2)
        return True
    except Exception as _e:
        _config.add_log(f"[persona] storage write failed: {_config.redact(str(_e))}")
        return False


def get_persona():
    """Return the active persona name, or None when cleared (default)."""
    name = (_read_storage().get("persona") or "")
    name = str(name).strip().lower() or None
    if name and name not in list_personas():
        return None  # stale entry pointing at a deleted overlay
    return name


def set_persona(name):
    """Set the active persona. "none"/"" clears. Returns a status string."""
    key = str(name or "").strip().lower()
    if key in ("", "none", "off", "clear"):
        _write_storage({"persona": None})
        _config.add_log("[persona] cleared (back to default)")
        return "Persona cleared. Back to the default voice."
    valid = list_personas()
    if key not in valid:
        return (f"Unknown persona '{name}'. Available: "
                f"{', '.join(valid) if valid else '(none yet)'}.")
    _write_storage({"persona": key})
    _config.add_log(f"[persona] set to '{key}'")
    return f"Persona set to '{key}'."


def persona_overlay():
    """The active overlay's prompt fragment, or "" when none is set."""
    name = get_persona()
    if not name:
        return ""
    path = os.path.join(_PERSONAS_DIR, name + ".md")
    try:
        with open(path, encoding="utf-8") as _f:
            return _f.read().strip()
    except Exception as _e:
        _config.add_log(f"[persona] overlay read failed: {_config.redact(str(_e))}")
        return ""


# --- builtins-style tool wrappers (the coordinator wires these into dispatch) ---

def tool_set_persona(name: str = "") -> str:
    """Set the active persona overlay ("none" clears)."""
    return set_persona(name)


def tool_list_personas() -> str:
    """List available persona overlays and the active one."""
    names = list_personas()
    active = get_persona()
    lines = ["Available personas:"]
    for n in names:
        mark = " (active)" if n == active else ""
        lines.append(f"  - {n}{mark}")
    if not names:
        lines.append("  (none yet)")
    return "\n".join(lines)


def tool_get_persona() -> str:
    """Report the active persona overlay."""
    active = get_persona()
    return f"Active persona: {active or 'none (default)'}"
