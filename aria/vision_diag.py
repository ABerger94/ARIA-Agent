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


def load_config():
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
    config = load_config()
    key = config.OLLAMA_CLOUD_API_KEY
    if not key or key == "INSERT":
        print("FAIL: no Ollama API key in aria_keys.json "
              "(field OLLAMA_API_KEY). Add it, then re-run.")
        return 1
    print("key: present (source hidden)")

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
    if ok:
        print(f"    model replied: {reply[:80]!r}")
        print("VERDICT: vision path is healthy — the failure is in ARIA's "
              "wiring, not the API. Send this output to Milk.")
        return 0
    print("VERDICT: Ollama Cloud rejects the image payload for this model. "
          "Send this output to Milk.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
