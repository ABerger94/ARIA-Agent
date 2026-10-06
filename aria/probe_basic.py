"""Probe: what does the provider chain actually return for basic requests?

Run:  cd E:/ARIA && python aria/probe_basic.py
Shows the serving provider and the exact parts returned for (1) plain text,
(2) a trivial tool call, (3) a LARGE input payload (simulates her reading
ops_screen.py before a redesign), and (4) a long-output request.
Prints each provider's last error so failures are attributable.
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
    else:
        parts = (data["candidates"][0].get("content", {}) or {}).get("parts", [])
        print(f"parts ({len(parts)}):")
        for p in parts:
            keys = sorted(p.keys())
            preview = ""
            if "text" in p:
                preview = repr(p["text"][:100])
            elif "functionCall" in p:
                fc = p["functionCall"]
                preview = f"name={fc.get('name')}"
            print(f"  keys={keys} {preview}")
    for prov in P._build_chain():
        err = getattr(prov, "last_error", None)
        if err:
            print(f"  [{prov.name}] last_error: {err[:160]}")
    print()


def main():
    print("chain:", [p.name for p in P._build_chain()])
    print()

    # 1. plain text, no tools
    data = P.provider_call(
        "You are a helpful assistant. Reply with exactly: probe-ok",
        [{"role": "user", "parts": [{"text": "Reply with exactly: probe-ok"}]}],
    )
    show("1. plain text", data)

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
    show("2. tool call", data)

    # 3. LARGE input: the real ops_screen.py as context (what she reads
    #    before a redesign), short answer requested
    here = os.path.dirname(os.path.abspath(__file__))
    ops_path = os.path.join(here, "ops_screen.py")
    big = ""
    try:
        with open(ops_path, "r", encoding="utf-8") as f:
            big = f.read()
    except Exception as e:
        big = f"[could not read ops_screen.py: {e}]"
    print(f"large-input bytes: {len(big)}")
    data = P.provider_call(
        "You are a helpful assistant.",
        [{"role": "user", "parts": [
            {"text": "Here is a Python file. Reply with exactly: big-ok\n\n" + big[:60000]},
        ]}],
    )
    show("3. large input", data)

    # 4. long output: redesign-style generation
    data = P.provider_call(
        "You are a helpful assistant.",
        [{"role": "user", "parts": [
            {"text": "Write a detailed 800-word redesign proposal for a tabbed "
                     "desktop command-center UI with 7 tabs (Log, Tasks, Sensors, "
                     "Controls, Notes, HUB, Day). Cover layout, typography, color "
                     "system, and interaction patterns for each tab."},
        ]}],
    )
    show("4. long output", data)


if __name__ == "__main__":
    main()
