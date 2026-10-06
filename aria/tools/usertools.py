"""Tool SDK — user-defined tools in aria/tools/user_tools/.

A user tool module is a plain ``*.py`` file (``_``-prefixed files are skipped)
defining:

    TOOL_NAME = "my_tool"            # ^[a-z][a-z0-9_]{2,40}$
    TOOL_DESCRIPTION = "..."         # one-line summary for the model
    TOOL_PARAMETERS = {"type": "OBJECT", "properties": {...}, "required": [...]}

    def run(args: dict) -> str: ...  # the handler

Loading never crashes boot: import errors, bad names, and name collisions
with the existing registry are logged (via aria.config.add_log) and skipped.

Wiring for the coordinator (integration pass):
  - on dispatch init:  usertools.load_user_tools_into_registry()
  - tool:              usertools.tool_reload_user_tools()  -> registry name "reload_user_tools"
  - declarations:      usertools.get_user_tools() gives (name, handler, declaration)
    tuples; declaration dicts match the schemas.py {'name','description','parameters'}
    shape so they can be appended to the model's function_declarations list.

Top-level imports here are stdlib + aria.config only; dispatch is imported
lazily inside functions to avoid import cycles.
"""

import importlib.util
import os
import re

from aria import config as _config

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_SCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_tools")

# name -> (handler_fn, declaration_dict); handler_fn takes a dict and returns str
_USER_TOOLS = {}


def _existing_registry_names():
    """Lazily read the dispatch registry (avoids import cycle at module top)."""
    try:
        from aria.tools import dispatch
        return set(dispatch.get_registered_tools().keys())
    except Exception as _e:
        _config.add_log(f"[user-tools] could not read dispatch registry: {_config.redact(str(_e))}")
        return set()


def _load_module_from_path(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    mod_name = "aria.tools.user_tools." + stem
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _normalize_parameters(params):
    if isinstance(params, dict) and params.get("type") == "OBJECT":
        out = dict(params)
        out.setdefault("properties", {})
        out.setdefault("required", [])
        return out
    return {"type": "OBJECT", "properties": {}, "required": []}


def load_user_tools(scan_dir=None):
    """Scan ``scan_dir`` (default aria/tools/user_tools/) for user tool modules.

    Returns a list of (name, handler_fn, declaration_dict) tuples.
    NEVER raises: every failure mode is logged and skipped.
    """
    d = scan_dir or _SCAN_DIR
    results = []
    existing = _existing_registry_names()
    if not os.path.isdir(d):
        _config.add_log(f"[user-tools] scan dir missing: {d}")
        return results
    for fname in sorted(os.listdir(d)):
        if fname.startswith("_") or not fname.endswith(".py"):
            continue
        path = os.path.join(d, fname)
        try:
            mod = _load_module_from_path(path)
        except Exception as _e:
            _config.add_log(f"[user-tools] skipped {fname}: import failed: {_config.redact(str(_e))}")
            continue
        try:
            name = getattr(mod, "TOOL_NAME", None)
            desc = getattr(mod, "TOOL_DESCRIPTION", "")
            params = _normalize_parameters(getattr(mod, "TOOL_PARAMETERS", None))
            run_fn = getattr(mod, "run", None)
            if not isinstance(name, str) or not _NAME_RE.match(name):
                _config.add_log(
                    f"[user-tools] skipped {fname}: TOOL_NAME {name!r} invalid "
                    f"(must match ^[a-z][a-z0-9_]{{2,40}}$)")
                continue
            if not callable(run_fn):
                _config.add_log(f"[user-tools] skipped {fname}: no run(args) function defined")
                continue
            if name in existing or name in _USER_TOOLS:
                _config.add_log(
                    f"[user-tools] skipped '{name}' ({fname}): name collides with "
                    f"an existing tool")
                continue

            def _handler(args, _run=run_fn, _tool=name):
                try:
                    return str(_run(args if isinstance(args, dict) else {}))
                except Exception as _e:
                    return f"[user tool '{_tool}' failed: {_config.redact(str(_e))}]"

            _handler.__name__ = f"user_tool_{name}"
            decl = {"name": name, "description": str(desc) or name, "parameters": params}
            _USER_TOOLS[name] = (_handler, decl)
            results.append((name, _handler, decl))
            _config.add_log(f"[user-tools] loaded '{name}' from {fname}")
        except Exception as _e:
            _config.add_log(f"[user-tools] skipped {fname}: {_config.redact(str(_e))}")
            continue
    return results


def get_user_tools():
    """Currently loaded user tools as [(name, handler_fn, declaration_dict)]."""
    return [(name, handler, decl) for name, (handler, decl) in _USER_TOOLS.items()]


def _register_into_dispatch(loaded):
    """Push loaded tools into the live dispatch registry. Returns names added."""
    try:
        from aria.tools import dispatch  # lazy: avoids import cycle
    except Exception as _e:
        _config.add_log(f"[user-tools] dispatch unavailable: {_config.redact(str(_e))}")
        return []
    added = []
    for name, handler, _decl in loaded:
        if name in dispatch.get_registered_tools():
            _config.add_log(f"[user-tools] skipped '{name}': collides with existing registry tool")
            _USER_TOOLS.pop(name, None)
            continue
        dispatch.register_tool(name, handler)
        added.append(name)
    return added


def load_user_tools_into_registry(scan_dir=None):
    """Load user tools from disk AND register them in dispatch. Returns names added."""
    return _register_into_dispatch(load_user_tools(scan_dir))


def tool_reload_user_tools() -> str:
    """Builtins-style tool: re-scan the user_tools dir and report added/removed.

    Intended registry name: "reload_user_tools".
    """
    before = set(_USER_TOOLS)
    try:
        from aria.tools import dispatch  # lazy: avoids import cycle
        reg = getattr(dispatch, "_REGISTRY", None)
        if isinstance(reg, dict):
            for name in before:
                reg.pop(name, None)
        # Drop stale model declarations too, or removed tools stay callable.
        try:
            from aria.tools.schemas import unregister_dynamic_tool_declaration
            for name in before:
                unregister_dynamic_tool_declaration(name, toolkit="user")
        except Exception as _e_silent:
            _config.log_silent("tool_reload_user_tools", _e_silent)
    except Exception as _e_silent:
        _config.log_silent("tool_reload_user_tools", _e_silent)
    _USER_TOOLS.clear()
    loaded = load_user_tools()
    added = _register_into_dispatch(loaded)
    after = set(_USER_TOOLS)
    removed = sorted(before - after)
    return (f"User tools reloaded: {len(after)} active. "
            f"Added: {', '.join(sorted(added)) or 'none'}. "
            f"Removed: {', '.join(removed) or 'none'}.")
