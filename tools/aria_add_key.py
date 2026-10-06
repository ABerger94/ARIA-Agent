#!/usr/bin/env python3
"""Safely manage aria_keys.json - no JSON hand-editing needed.

Run from the repo root:
    python tools/aria_add_key.py

Can set the Ollama Cloud API key (ARIA's main brain), the fallback
provider keys, and the GitHub token; validates the file, or rebuilds
it cleanly if the JSON is broken.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(os.path.dirname(HERE), "aria_keys.json")

PROVIDER_KEYS = [
    ("OLLAMA_API_KEY", "Ollama Cloud API key (main brain)"),
    ("GROQ_API_KEY", "Groq API key (fallback provider)"),
    ("OPENROUTER_API_KEY", "OpenRouter API key (fallback provider)"),
    ("MISTRAL_API_KEY", "Mistral API key (fallback provider)"),
]


def load():
    if not os.path.exists(KEYS_FILE):
        return {}, "missing"
    try:
        with open(KEYS_FILE) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}, "not-a-dict"
        return d, None
    except json.JSONDecodeError as e:
        return None, "broken JSON at line %d, column %d: %s" % (e.lineno, e.colno, e.msg)


def looks_like_key(k):
    return isinstance(k, str) and len(k.strip()) >= 20


def looks_like_github(k):
    return (k.startswith("ghp_") or k.startswith("github_pat_")) and len(k) >= 20


def save(keys):
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


def mask(k):
    return (k[:4] + "..." + k[-4:]) if len(k) > 12 else "key"


def set_provider_key(keys):
    print("Provider keys:")
    for i, (field, label) in enumerate(PROVIDER_KEYS):
        cur = keys.get(field, "")
        state = mask(cur) if cur and cur != "INSERT" else "not set"
        print("  %d. %s: %s" % (i + 1, label, state))
    pick = input("Set which key? (number, blank cancels): ").strip()
    if not pick.isdigit() or not 1 <= int(pick) <= len(PROVIDER_KEYS):
        return False
    field, label = PROVIDER_KEYS[int(pick) - 1]
    new = input("Paste the %s (Enter to skip): " % label).strip()
    if not new:
        return False
    if not looks_like_key(new):
        print("That doesn't look like an API key (20+ chars). Skipped.")
        return False
    keys[field] = new
    print("%s saved." % label)
    return True


def set_github_token(keys):
    cur = keys.get("GITHUB_TOKEN", "")
    print("GitHub token: %s." % (mask(cur) if cur and cur != "INSERT" else "not set"))
    new = input("Paste the GitHub token (Enter to skip): ").strip()
    if not new:
        return False
    if not looks_like_github(new):
        print("That doesn't look like a GitHub token (ghp_... or github_pat_...). Skipped.")
        return False
    keys["GITHUB_TOKEN"] = new
    if not keys.get("GITHUB_USERNAME") or keys.get("GITHUB_USERNAME") == "INSERT":
        keys["GITHUB_USERNAME"] = "ABerger94"
    print("GitHub token saved.")
    return True


def fresh_start():
    keys = {"OLLAMA_API_KEY": "INSERT",
            "GROQ_API_KEY": "INSERT",
            "OPENROUTER_API_KEY": "INSERT",
            "MISTRAL_API_KEY": "INSERT",
            "GITHUB_TOKEN": "INSERT",
            "GITHUB_USERNAME": "ABerger94"}
    print("Creating a clean keys file. Paste values (Enter skips):")
    set_provider_key(keys)
    set_github_token(keys)
    save(keys)
    print("Wrote a clean %s." % KEYS_FILE)


def main():
    keys, err = load()
    if err == "missing":
        print("No aria_keys.json here - creating one.")
        fresh_start()
    elif err and err.startswith("broken JSON"):
        print("Your keys file has " + err + ".")
        c = input("Start fresh? (y/n): ").strip().lower()
        if c == "y":
            fresh_start()
        else:
            print("OK - fix that spot by hand (usually a missing or extra comma) and run again.")
    elif err:
        print("aria_keys.json is unusable (%s). Nothing changed." % err)
    else:
        print("Keys file is valid: %s" % KEYS_FILE)
        while True:
            c = input("[p]rovider key  [g]itHub token  [d]one: ").strip().lower()
            if c == "p":
                if set_provider_key(keys):
                    save(keys)
                    print("Saved. Restart ARIA to use the new key.")
            elif c == "g":
                if set_github_token(keys):
                    save(keys)
                    print("Saved. Restart ARIA.")
            elif c == "d" or c == "":
                break
            else:
                print("Pick p, g, or d.")
    print("Done.")


if __name__ == "__main__":
    main()
