"""Probe: what does the provider chain actually return for basic requests?

Run:  cd E:/ARIA && python aria/probe_basic.py
Shows the serving provider and the exact parts returned for (1) plain text
and (2) a trivial tool call — the two shapes the main loop depends on.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aria.agent import providers as P


def show(label, data):
    print(f"--- {label} ---")
    print("serving provider:", P.get_active_provider())
    if not data or not data.get("candidates"):
        print("RESULT: no candidates (chain exhausted or all errored)")
        return
    parts = (data["candidates"][0].get("content", {}) or {}).get("parts", [])
    print(f"parts ({len(parts)}):")
    for p in parts:
        keys = sorted(p.keys())
        preview = ""
        if "text" in p:
            preview = repr(p["text"][:120])
        elif "functionCall" in p:
            fc = p["functionCall"]
            preview = f"name={fc.get('name')} args={fc.get('args')}"
        print(f"  keys={keys} {preview}")
    print()


def main():
    print("chain:", [p.name for p in P._build_chain()])
    print()

    # 1. plain text, no tools
    data = P.provider_call(
        "You are a helpful assistant. Reply with exactly: probe-ok",
        [{"role": "user", "parts": [{"text": "Reply with exactly: probe-ok"}]}],
    )
    show("plain text", data)

    # 2. trivial tool call
    decls = [{
        "name": "get_time",
        "description": "Get the current time. Call this when the user asks for the time.",
        "parameters": {"type": "object", "properties": {}},
    }]
    data = P.provider_call(
        "You are a helpful assistant. Use the get_time tool when asked for the time.",
        [{"role": "user", "parts": [{"text": "What time is it? Call the get_time tool."}]}],
        tool_decls=decls,
    )
    show("tool call", data)


if __name__ == "__main__":
    main()
