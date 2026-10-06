"""Sample user tool for the Tool SDK.

This file is the documented example (see docs/USER_TOOL_SDK.md). It is
deliberately harmless: it only returns a greeting string, no I/O, no network.

To make your own tool, copy this file to a new name, change TOOL_NAME, and
edit run() — then call ``reload_user_tools`` (or restart ARIA).
"""

TOOL_NAME = "greeting"
TOOL_DESCRIPTION = "Returns a friendly greeting for the given name."
TOOL_PARAMETERS = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", "description": "Who to greet."},
    },
    "required": [],
}


def run(args: dict) -> str:
    """Greet someone by name.

    args: {"name": "Alek"} (optional; defaults to "friend")
    """
    name = (args or {}).get("name") or "friend"
    return f"Hello, {name}! Hope you're having a great day."
