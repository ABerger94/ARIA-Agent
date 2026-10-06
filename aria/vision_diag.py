"""ARIA vision diagnostic — run on the laptop:  python aria/vision_diag.py

Tests the Ollama Cloud vision path DIRECTLY (no ARIA restart needed) and
prints exactly which step fails. Reads the API key from aria_keys.json —
never prints it.
"""
import base64
import io
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLOUD = "https://ollama.com"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def check_wiring():
    """Verify the checkout actually contains the vision fix.

    Catches stale or partially-updated pulls (seen on flaky drives):
    the API can be healthy while ARIA runs old wiring.
    Returns a list of missing pieces (empty = wiring OK).
    """
    import subprocess

    def has(relpath, marker):
        try:
            with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
                return marker in f.read()
        except Exception:
            return False

    checks = [
        ("aria/main.py", "vision._default_vision_call",
         "vision hook wired to native path (main.py)"),
        ("aria/vision.py", "_ollama_native_vision_call",
         "native /api/chat vision (vision.py)"),
        ("aria/vision.py", "only_provider",
         "chain restriction (vision.py)"),
    ]
    try:
        head = subprocess.run(
            ["git", "log", "--oneline", "-1"], cwd=ROOT,
            capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        head = "(git unavailable)"
    print(f"git HEAD: {head or '(unknown)'}")
    missing = []
    for relpath, marker, desc in checks:
        ok = has(relpath, marker)
        print(f"[w] {desc}: {'OK' if ok else 'MISSING'}")
        if not ok:
            missing.append(desc)
    return missing


def load_config():
    """Load aria/config.py directly (bypasses the heavy aria/__init__)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_config_diag", os.path.join(HERE, "config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_main_loop(key):
    """Reproduce the main agent loop's exact request path (text + tools,
    then streaming) against Ollama Cloud. Prints which variant fails."""
    import types
    import importlib

    # Stub the heavy aria/__init__; load only what the request path needs.
    aria_stub = types.ModuleType("aria")
    aria_stub.__path__ = [os.path.join(ROOT, "aria")]
    sys.modules["aria"] = aria_stub
    sys.modules["aria.config"] = load_config()
    agent_stub = types.ModuleType("aria.agent")
    agent_stub.__path__ = [os.path.join(ROOT, "aria", "agent")]
    sys.modules["aria.agent"] = agent_stub
    tools_stub = types.ModuleType("aria.tools")
    tools_stub.__path__ = [os.path.join(ROOT, "aria", "tools")]
    sys.modules["aria.tools"] = tools_stub

    providers = importlib.import_module("aria.agent.providers")
    spec = importlib.util.spec_from_file_location(
        "schemas_main", os.path.join(ROOT, "aria", "tools", "schemas.py"))
    schemas = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schemas)

    decls = [{"function_declarations":
              [d for d in schemas.ALL_FUNCTION_DECLARATIONS
               if d["name"] in ("describe_camera", "read_screen", "take_photo")]}]
    contents = [{"role": "user", "parts": [{"text": "Reply with the word ok."}]}]
    p = providers.OpenAICompatProvider(
        "ollama_cloud", "https://ollama.com/v1", key, "gpt-oss:120b")

    results = []
    # [3a] plain text + tools, non-streaming
    try:
        data = p.call("You are a test.", contents, tool_decls=decls)
        ok = bool(data and data.get("candidates"))
        results.append(("text+tools", ok, p.last_error))
    except Exception as e:
        results.append(("text+tools", False, f"raised: {e}"))
    # [3b] streaming (what the main loop actually uses)
    try:
        chunks = []
        data = p.call("You are a test.", contents, tool_decls=decls,
                       on_text_chunk=lambda c: chunks.append(c))
        ok = bool(data and data.get("candidates"))
        results.append(("text+tools+stream", ok, p.last_error))
    except Exception as e:
        results.append(("text+tools+stream", False, f"raised: {e}"))
    for name, ok, err in results:
        print(f"[3] main-loop probe ({name}): {'PASS' if ok else 'FAIL: ' + str(err)}")
    return all(ok for _, ok, _ in results)
    """Load aria/config.py directly (bypasses the heavy aria/__init__)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria_config_diag", os.path.join(HERE, "config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(path, payload, key, timeout=60):
    req = urllib.request.Request(
        CLOUD + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""
        return False, None, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def main():
    missing = check_wiring()
    if missing:
        print("VERDICT: your checkout is stale or partially updated — ARIA is "
              "running old vision wiring even though the API is fine.")
        print("Repair (keys are gitignored, safe to keep):")
        print("  git fetch origin && git reset --hard origin/main")
        print("Then restart ARIA and retry.")
        return 1

    config = load_config()
    print(f"keys file: {config.KEYS_FILE}")
    key = config.OLLAMA_CLOUD_API_KEY
    if not key or key == "INSERT":
        print("FAIL: no Ollama API key in aria_keys.json "
              "(field OLLAMA_API_KEY). Add it, then re-run.")
        return 1
    print("key: present (source hidden)")
    try:
        with open(config.KEYS_FILE, encoding="utf-8") as _f:
            _d = json.load(_f)
        populated = sorted(k for k, v in _d.items()
                           if v and v != "INSERT" and "KEY" in k.upper())
        print(f"populated key fields: {populated}")
    except Exception as e:
        print(f"WARNING: could not re-read keys file: {e}")

    vision_model = (os.environ.get("OLLAMA_VISION_MODEL")
                    or getattr(config, "OLLAMA_VISION_MODEL", None)
                    or "gemma4:31b-cloud")
    print(f"vision model tag: {vision_model}")

    # 1. text-only probe of the vision model (is the tag servable?)
    ok, data, err = call("/api/chat", {
        "model": vision_model, "stream": False,
        "messages": [{"role": "user", "content": "Reply with the word ok."}],
    }, key)
    print(f"[1] text-only probe: {'PASS' if ok else 'FAIL: ' + str(err)}")
    if not ok:
        print("VERDICT: the model tag itself is rejected — vision can never "
              "work with this tag. Try another OLLAMA_VISION_MODEL.")
        return 1

    # 2. image probe via native /api/chat (images array)
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "JPEG")
        tiny_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"FAIL: cannot build test image: {e}")
        return 1
    ok, data, err = call("/api/chat", {
        "model": vision_model, "stream": False,
        "messages": [{"role": "user", "content": "What color is this square?",
                      "images": [tiny_b64]}],
    }, key, timeout=120)
    reply = (data.get("message") or {}).get("content", "") if data else ""
    print(f"[2] native image probe: {'PASS' if ok else 'FAIL: ' + str(err)}")
    if not ok:
        print("VERDICT: Ollama Cloud rejects the image payload for this model. "
              "Send this output to Milk.")
        return 1
    print(f"    model replied: {reply[:80]!r}")

    # 3. main-loop probes: exact request path the agent uses (text + tools,
    # then streaming). A failure here = the conversational turn itself is
    # broken, independent of vision.
    if check_main_loop(key):
        print("VERDICT: all probes healthy — API and wiring are fine. "
              "If ARIA still fails, restart her and send Milk the new log lines.")
        return 0
    print("VERDICT: the main-loop request is rejected — send this output to Milk.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
