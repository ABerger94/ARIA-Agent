"""User tool modules for the Tool SDK (aria/tools/usertools.py).

Each ``*.py`` file here (except ``_``-prefixed) may define a single tool:

    TOOL_NAME = "my_tool"
    TOOL_DESCRIPTION = "What it does."
    TOOL_PARAMETERS = {"type": "OBJECT",
                       "properties": {"arg": {"type": "STRING"}},
                       "required": ["arg"]}

    def run(args: dict) -> str:
        return f"hello {args.get('arg')}"

See docs/USER_TOOL_SDK.md for the 5-minute guide.
"""
