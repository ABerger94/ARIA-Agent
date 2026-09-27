#!/usr/bin/env python3
"""Safely manage aria_keys.json - no JSON hand-editing needed.

Run from the folder that holds robot.py:
    python aria_add_key.py

Can add Gemini API keys, set the GitHub token, validate the file,
or rebuild it cleanly if the JSON is broken. Works even when
A.R.I.A. herself is rate-limited (that's the whole point).
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "aria_keys.json")


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


def looks_like_gemini(k):
    return (k.startswith("AIza") or k.startswith("AQ.")) and len(k) >= 20


def looks_like_github(k):
    return (k.startswith("ghp_") or k.startswith("github_pat_")) and len(k) >= 20


def save(keys):
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)


def mask(k):
    return (k[:4] + "..." + k[-4:]) if len(k) > 12 else "key"


def pool_of(keys):
    pool = keys.get("GEMINI_API_KEYS")
    if not isinstance(pool, list):
        pool = []
    single = keys.get("GEMINI_API_KEY")
    if single and single != "INSERT" and single not in pool:
        pool.append(single)
    return pool


def add_gemini_key(keys):
    pool = pool_of(keys)
    print("Gemini pool has %d key(s)." % len(pool))
    changed = False
    while True:
        new = input("Paste a Gemini key (Enter when done): ").strip()
        if not new:
            break
        if not looks_like_gemini(new):
            print("That doesn't look like a Gemini key (AIza... or AQ..., 20+ chars). Skipped.")
            continue
        if new in pool:
            print("Already in the pool. Skipped.")
            continue
        pool.append(new)
        changed = True
        print("Added %s to the pool." % mask(new))
    if changed:
        keys["GEMINI_API_KEYS"] = pool
    return changed


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
    bak = KEYS_FILE + ".bak"
    if os.path.exists(KEYS_FILE):
        shutil.copy2(KEYS_FILE, bak)
        print("Backed up the broken file to %s." % bak)
    keys = {"GEMINI_API_KEY": "INSERT", "GEMINI_API_KEYS": [],
            "GITHUB_TOKEN": "INSERT", "GITHUB_USERNAME": "ABerger94"}
    print("Creating a clean keys file. Paste values (Enter skips):")
    add_gemini_key(keys)
    set_github_token(keys)
    save(keys)
    print("Wrote a clean %s." % KEYS_FILE)


def remove_key(keys):
    pool = pool_of(keys)
    if not pool:
        print("Pool is empty.")
        return False
    print("Keys:")
    for i, k in enumerate(pool):
        print("  %d. %s" % (i + 1, mask(k)))
    pick = input("Remove which key? (number, blank cancels): ").strip()
    if pick.isdigit() and 1 <= int(pick) <= len(pool):
        doomed = pool[int(pick) - 1]
        if input("Remove %s? (y/N): " % mask(doomed)).strip().lower() == "y":
            new_pool = [k for k in pool if k != doomed]
            keys["GEMINI_API_KEYS"] = new_pool
            if keys.get("GEMINI_API_KEY") == doomed:
                keys["GEMINI_API_KEY"] = new_pool[0] if new_pool else "INSERT"
            print("Removed %s." % mask(doomed))
            return True
        print("Kept it.")
    return False


def test_key(keys):
    """Ask Google directly whether a key works; print Google's exact verdict."""
    import urllib.request
    import urllib.error
    pool = pool_of(keys)
    if not pool:
        print("No Gemini keys in the pool to test.")
        return
    print("Keys:")
    for i, k in enumerate(pool):
        print("  %d. %s" % (i + 1, mask(k)))
    pick = input("Test which key? (number, or paste a key to test it directly): ").strip()
    key = None
    if pick.isdigit() and 1 <= int(pick) <= len(pool):
        key = pool[int(pick) - 1]
    elif looks_like_gemini(pick):
        key = pick
    if not key:
        print("Nothing to test.")
        return
    print("Asking Google about %s ..." % mask(key))
    body = json.dumps({"contents": [{"parts": [{"text": "ping"}]}]}).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-3.8-flash:generateContent?key=" + key)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read().decode())
        print("OK - Google accepted this key. It works.")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        print("Google answered HTTP %d:" % e.code)
        print(detail)
        if e.code == 404 and "no longer available" in detail:
            print("-> This 404 is Google retiring the MODEL for new projects, not a bad key. "
                  "Your old key is grandfathered; new keys need a current model. "
                  "Make sure robot.py is v8.1+ (gemini-3.8-flash).")
        elif e.code == 404:
            print("-> 404 can mean the key/project is too new (wait ~10 min) "
                  "or the Generative Language API is not enabled in its Cloud project.")
        elif e.code == 402:
            print("-> 402 means this key's Cloud project is on Prepay billing with $0 credits. "
                  "Free fix: in Cloud Console, unlink billing from that project to put it back "
                  "on the free tier, then test again. Paid fix: buy prepay credits in AI Studio.")
        elif e.code == 403:
            print("-> 403 means the API is disabled for this key's project - enable "
                  "'Generative Language API' in Google Cloud Console for that project.")
        elif e.code == 400:
            print("-> 400 usually means the key itself is mistyped or invalid - re-copy it from AI Studio.")
    except Exception as e:
        print("Could not reach Google: %s" % e)


def main():
    keys, err = load()
    if err == "missing":
        print("No aria_keys.json here - creating one.")
        fresh_start()
    elif err and err.startswith("broken JSON"):
        print("Your keys file has " + err + ".")
        c = input("Start fresh? I'll back it up first. (y/n): ").strip().lower()
        if c == "y":
            fresh_start()
        else:
            print("OK - fix that spot by hand (usually a missing or extra comma) and run again.")
    elif err:
        print("aria_keys.json is unusable (%s). Nothing changed." % err)
    else:
        print("Keys file is valid. Pool has %d key(s)." % len(pool_of(keys)))
        while True:
            c = input("[a]dd key(s)  [t]est a key  [r]emove a key  [g]itHub token  [d]one: ").strip().lower()
            if c == "a":
                if add_gemini_key(keys):
                    save(keys)
                    print("Saved. Restart robot.py to use the new keys.")
            elif c == "t":
                test_key(keys)
            elif c == "r":
                if remove_key(keys):
                    save(keys)
                    print("Saved. Restart robot.py.")
            elif c == "g":
                if set_github_token(keys):
                    save(keys)
                    print("Saved. Restart robot.py.")
            elif c == "d" or c == "":
                break
            else:
                print("Pick a, t, g, or d.")
    print("Done.")


if __name__ == "__main__":
    main()
