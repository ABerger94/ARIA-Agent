"""Tool SDK — user-defined tools in aria/tools/user_tools/.

A user tool module is a plain ``*.py`` file (``_``-prefixed files are skipped)
defining:

    TOOL_NAME = "my_tool"            # ^[a-z][a-z0-9_]{2,40}$
    TOOL_DESCRIPTION = "..."         # one-line summary for the model
    TOOL_PARAMETERS = {"type": "OBJECT", "properties": {...}, "required": [...]}

    def run(args: dict) -> str: ...  # the handler

Loading never crashes boot: import errors, bad names, and name collisions
with the existing registry are logged (via aria.config.add_log) and skipped.

Deferred import (pass 25): loading only READS the file (AST scan for
TOOL_NAME / DESCRIPTION / PARAMETERS and a ``def run``) — no module code
runs at load time. The module is imported lazily, once, on its handler's
first call, so the import happens under the same approval / safe-mode
policy as the call itself.

Policy (pass 25): a user tool runs arbitrary Python in ARIA's process,
so its name is registered in config's user-tool classifier
(config.is_user_tool) — approval under confirm-risky, hard-blocked in
safe mode, like a discovered MCP tool. Reload unregisters names for
removed tools.

Wiring for the coordinator (integration pass):
  - on dispatch init:  usertools.load_user_tools_into_registry()
  - tool:              usertools.tool_reload_user_tools()  -> registry name "reload_user_tools"
  - declarations:      usertools.get_user_tools() gives (name, handler, declaration)
    tuples; declaration dicts match the schemas.py {'name','description','parameters'}
    shape so they can be appended to the model's function_declarations list.

Top-level imports here are stdlib + aria.config only; dispatch is imported
lazily inside functions to avoid import cycles.
"""

import ast
import hashlib
import importlib.util
import os
import re
import threading

from aria import config as _config

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_SCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_tools")

# name -> (handler_fn, declaration_dict); handler_fn takes a dict and returns str
_USER_TOOLS = {}
# name -> source file path, for the revision binding (pass 27 #18).
_USER_TOOL_PATHS = {}


def source_digest(path):
    """SHA-256 of a user-tool file's bytes, or None when unreadable."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def freeze_user_tool_args(name, args):
    """Bind a to-be-parked user-tool call to its source revision.

    Pass 27 #18: metadata is scanned once while the module is
    imported later, so replacing the file between scan and
    invocation executed the replacement under the old declaration.
    The freeze reads the CURRENT bytes at park time and stores
    their digest in the parked args; the handler refuses to run
    when the file no longer matches. Raises ValueError when the
    source cannot be read — the caller must refuse to park a
    revisionless approval, like the skill/trust freezes.
    """
    path = _USER_TOOL_PATHS.get(name)
    if not path:
        raise ValueError(
            f"user tool '{name}' has no known source file")
    digest = source_digest(path)
    if digest is None:
        raise ValueError(
            f"user tool '{name}' source is unreadable — cannot bind "
            "an approval to it")
    gate_args = dict(args or {})
    gate_args["_source_digest"] = digest
    return gate_args


def _reserved_names():
    """Approval-resolver names a user tool must never claim.

    approve / deny / set_approval_mode are deliberately NOT in
    the dispatch registry (dispatch.py) and are exempt from
    approval gating (approval.NEVER_GATED) — a user tool with
    one of those names would register, get a model-facing
    declaration, and run with no approval (pass 26 B1#1).
    Read from approval when importable; the literal fallback
    keeps the rejection working if approval cannot load.
    """
    try:
        from aria import approval as _approval_mod
        return set(_approval_mod.NEVER_GATED)
    except Exception:
        return {"approve", "deny", "list_pending_approvals",
                "set_approval_mode", "get_approval_mode"}


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
    # Execute the freshly-read bytes, not the loader's .pyc
    # (pass 27 #18): the bytecode cache is keyed on the file's
    # second-resolution mtime, so a file rewritten within the same
    # second would execute STALE bytecode — the old revision under
    # a new approval, after the handler just verified the new
    # bytes. Compiling the bytes just read executes exactly the
    # bytes the digest was computed over.
    with open(path, "rb") as f:
        source = f.read()
    code = compile(source, path, "exec")
    exec(code, mod.__dict__)
    return mod


def _normalize_parameters(params):
    if isinstance(params, dict) and params.get("type") == "OBJECT":
        out = dict(params)
        out.setdefault("properties", {})
        out.setdefault("required", [])
        return out
    return {"type": "OBJECT", "properties": {}, "required": []}


def _scan_module(path, fname):
    """Read a user tool file's metadata WITHOUT executing it.

    Pass 25: the loader used to exec_module() every file at boot,
    so a planted file's top-level code ran ungated before any
    approval — and the tool then ran with no policy classification
    at all. The metadata (TOOL_NAME / DESCRIPTION / PARAMETERS and
    whether run() is defined) is now read by AST only; the module
    itself is imported lazily, once, on the first call of its
    handler — so the import runs under the same approval /
    safe-mode policy as the call. Literal values only: a computed
    TOOL_NAME or TOOL_DESCRIPTION falls back to the invalid /
    empty default and the file is skipped or described plainly.
    Returns (name, desc, params) or None when skipped.
    """
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except Exception as _e:
        _config.add_log(f"[user-tools] skipped {fname}: could not read: {_config.redact(str(_e))}")
        return None
    try:
        tree = ast.parse(source, filename=fname)
    except SyntaxError as _e:
        _config.add_log(f"[user-tools] skipped {fname}: syntax error: {_config.redact(str(_e))}")
        return None
    assigns = {}
    has_run = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                assigns[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "run":
            has_run = True
    name = assigns.get("TOOL_NAME")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        _config.add_log(
            f"[user-tools] skipped {fname}: TOOL_NAME {name!r} invalid "
            f"(must match ^[a-z][a-z0-9_]{{2,40}}$)")
        return None
    if not has_run:
        _config.add_log(f"[user-tools] skipped {fname}: no run(args) function defined")
        return None
    desc = assigns.get("TOOL_DESCRIPTION", "")
    params = _normalize_parameters(assigns.get("TOOL_PARAMETERS"))
    return (name, str(desc or ""), params)


def _make_lazy_handler(path, name):
    """A handler that imports its module once, on first call.

    The module's top-level code runs at most once per process:
    a failed import or a non-callable run() is cached as broken
    and later calls return the failure instead of re-executing.
    Pass 27 #27: the import is serialized by a per-handler lock
    (double-checked: a cheap check outside, the deciding check
    inside) — without it, two concurrent first calls could both
    see an empty cache and both execute the module's top-level
    code, contradicting "at most once".
    """
    state = {}
    init_lock = threading.Lock()

    def _fail(message):
        """A user-tool failure, typed FAILED (pass 26 B3#4): the
        text is kept verbatim (existing tests pin it), but the
        ResultText carries the status, so outcome.normalize()
        yields FAILED instead of DONE — a failed user tool used
        to read as a success. outcome is imported lazily, and a
        failure to import it falls back to the plain string."""
        try:
            from aria import outcome as _outcome_mod
            return _outcome_mod.ResultText(message, _outcome_mod.FAILED)
        except Exception:
            return message

    def _handler(args, _tool=name):
        # Pass 27 #18: when the call carries an approved source
        # digest, the file must still match it BEFORE the module
        # is imported — the import itself executes top-level code,
        # so checking after import would run the replacement. A
        # mismatch (or an unreadable source) refuses typed FAILED
        # instead of executing under the old approval. Calls with
        # no digest (auto mode, no approval to bind) run as before.
        call_args = args if isinstance(args, dict) else {}
        approved = call_args.get("_source_digest")
        if approved is not None:
            current = source_digest(path)
            if current is None:
                return _fail(f"[user tool '{_tool}' refused: source "
                             f"unreadable — cannot verify the approved "
                             f"revision]")
            if current != approved:
                return _fail(f"[user tool '{_tool}' refused: source "
                             f"changed since approval — re-approve to "
                             f"run the new revision]")
            call_args = {k: v for k, v in call_args.items()
                         if k != "_source_digest"}
        # A cached import belongs to the digest it was loaded
        # from: an approval for NEW bytes must execute the new
        # bytes, not the stale cached module.
        # Pass 27 #27: serialize the import. The outer check
        # avoids taking the lock on every call; the inner check,
        # under the lock, re-decides — so concurrent first calls
        # (or concurrent re-imports for a new digest) cannot both
        # enter _load_module_from_path and run the module's
        # top-level code twice.
        if ("run" not in state or (approved is not None
                                   and state.get("digest") != approved)):
            with init_lock:
                if ("run" not in state or (approved is not None
                                           and state.get("digest") != approved)):
                    try:
                        mod = _load_module_from_path(path)
                    except Exception as _e:
                        state["run"] = None
                        return _fail(f"[user tool '{_tool}' failed: import failed: "
                                     f"{_config.redact(str(_e))}]")
                    run_fn = getattr(mod, "run", None)
                    if not callable(run_fn):
                        state["run"] = None
                        return _fail(f"[user tool '{_tool}' failed: no run(args) "
                                     f"function defined]")
                    state["run"] = run_fn
                    state["digest"] = source_digest(path)
        run_fn = state.get("run")
        if run_fn is None:
            return _fail(f"[user tool '{_tool}' failed: the module failed "
                         f"to load — see the log]")
        try:
            return str(run_fn(call_args))
        except Exception as _e:
            return _fail(f"[user tool '{_tool}' failed: {_config.redact(str(_e))}]")

    _handler.__name__ = f"user_tool_{name}"
    return _handler


def load_user_tools(scan_dir=None):
    """Scan ``scan_dir`` (default aria/tools/user_tools/) for user tool modules.

    Returns a list of (name, handler_fn, declaration_dict) tuples.
    NEVER raises: every failure mode is logged and skipped.
    No module code runs here — metadata comes from an AST scan and
    the import is deferred to the handler's first call (see
    _scan_module / _make_lazy_handler).
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
        scanned = _scan_module(path, fname)
        if scanned is None:
            continue
        name, desc, params = scanned
        if name in _reserved_names():
            _config.add_log(
                f"[user-tools] skipped '{name}' ({fname}): name is "
                f"reserved for the approval resolvers and must never "
                f"be a user tool")
            continue
        if name in existing or name in _USER_TOOLS:
            _config.add_log(
                f"[user-tools] skipped '{name}' ({fname}): name collides with "
                f"an existing tool")
            continue
        handler = _make_lazy_handler(path, name)
        decl = {"name": name, "description": desc or name, "parameters": params}
        _USER_TOOLS[name] = (handler, decl)
        _USER_TOOL_PATHS[name] = path
        results.append((name, handler, decl))
        _config.add_log(f"[user-tools] loaded '{name}' from {fname}")
    return results


def get_user_tools():
    """Currently loaded user tools as [(name, handler_fn, declaration_dict)]."""
    return [(name, handler, decl) for name, (handler, decl) in _USER_TOOLS.items()]


def get_user_tool_decl(name):
    """The model-facing declaration dict for *name*, or None."""
    entry = _USER_TOOLS.get(name)
    return entry[1] if entry else None


def _register_into_dispatch(loaded):
    """Push loaded tools into the live dispatch registry. Returns names added."""
    try:
        from aria.tools import dispatch  # lazy: avoids import cycle
    except Exception as _e:
        _config.add_log(f"[user-tools] dispatch unavailable: {_config.redact(str(_e))}")
        return []
    try:
        from aria.tools.schemas import register_dynamic_tool_declaration
    except Exception as _e:
        _config.add_log(f"[user-tools] schemas unavailable: {_config.redact(str(_e))}")
        register_dynamic_tool_declaration = None
    added = []
    for name, handler, decl in loaded:
        if name in _reserved_names():
            _config.add_log(
                f"[user-tools] skipped '{name}': reserved for the "
                f"approval resolvers and must never be a user tool")
            _USER_TOOLS.pop(name, None)
            _config.unregister_user_tool(name)
            continue
        if name in dispatch.get_registered_tools():
            _config.add_log(f"[user-tools] skipped '{name}': collides with existing registry tool")
            _USER_TOOLS.pop(name, None)
            _config.unregister_user_tool(name)
            continue
        dispatch.register_tool(name, handler)
        # The name is a user tool: it runs arbitrary code in ARIA's
        # process, so the approval / safe-mode classifiers must see
        # it (config.is_user_tool) — without this the tool ran with
        # no policy at all (pass 25).
        _config.register_user_tool(name)
        # Pass 25: the declaration is the model-facing schema —
        # without it the tool stays dispatch-callable but invisible
        # to the model until restart. Boot wiring did this step in
        # dispatch.py; reload went through here and skipped it.
        if register_dynamic_tool_declaration is not None:
            register_dynamic_tool_declaration(name, decl, toolkit="user")
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
        # Drop stale policy names too, or removed tools stay gated
        # under a name that no longer exists (and a re-added tool
        # with the same name would inherit a stale classification).
        for name in before:
            _config.unregister_user_tool(name)
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
