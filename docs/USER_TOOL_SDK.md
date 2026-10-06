# ARIA Tool SDK — 5-minute guide

Add your own tools to ARIA without touching the core codebase. A user tool
is one Python file in `aria/tools/user_tools/`.

## The contract

Each `*.py` file (files starting with `_` are ignored) may define:

```python
TOOL_NAME = "my_tool"            # lowercase, 3-41 chars: ^[a-z][a-z0-9_]{2,40}$
TOOL_DESCRIPTION = "What it does."   # shown to the model
TOOL_PARAMETERS = {              # Gemini-style schema
    "type": "OBJECT",
    "properties": {
        "arg": {"type": "STRING", "description": "An argument."},
    },
    "required": ["arg"],
}

def run(args: dict) -> str:
    """Called with the model's arguments; return a plain string."""
    return f"You passed: {args.get('arg')}"
```

Rules:
- `run(args)` gets a dict, returns a str. Exceptions are caught and turned
  into an error string — your tool can never crash ARIA.
- The name must match `^[a-z][a-z0-9_]{2,40}$` and must not collide with an
  existing tool (collisions are skipped with a log line).
- Import errors are logged and skipped — a broken file never breaks boot.

## Worked example

`aria/tools/user_tools/sample_greeting.py` ships as the reference:

```python
TOOL_NAME = "greeting"
TOOL_DESCRIPTION = "Returns a friendly greeting for the given name."
TOOL_PARAMETERS = {
    "type": "OBJECT",
    "properties": {"name": {"type": "STRING"}},
    "required": [],
}

def run(args: dict) -> str:
    name = (args or {}).get("name") or "friend"
    return f"Hello, {name}! Hope you're having a great day."
```

## Load / reload

- **On boot:** the dispatch init calls
  `aria.tools.usertools.load_user_tools_into_registry()`, which scans the
  dir and registers every valid tool.
- **Without restarting:** call the `reload_user_tools` tool (wired to
  `aria.tools.usertools.tool_reload_user_tools`). It re-scans, registers new
  files, and reports what was added or removed:

  ```
  User tools reloaded: 2 active. Added: my_tool. Removed: none.
  ```

## For integrators

- `load_user_tools(scan_dir=None) -> [(name, handler_fn, declaration_dict)]`
  scans a directory (default `aria/tools/user_tools/`) and returns loaded
  tools **without** touching the registry.
- `get_user_tools()` returns the currently loaded set in the same tuple
  shape. Declaration dicts match the `schemas.py` shape
  (`{"name", "description", "parameters"}`) so they can be appended to the
  model's `function_declarations` list (the coordinator does this).
- Logs go through `aria.config.add_log` (redacted), so loader activity shows
  up in the OPS Log tab.

## Tips

- Keep tools deterministic and fast — they run inside the agent loop.
- Never `print()` secrets, and never return raw API keys in the result
  string; use `aria.config.redact` if you echo user input that might contain
  one.
- Validate `args` defensively: the model sometimes omits optional params.
