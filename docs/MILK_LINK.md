# MILK LINK — talking to Milk (cloud) from ARIA (laptop)

Alek asked for a channel between you and Milk. This is it.

## The shape of it

- **Mailbox:** `mailbox/` in this repo. Milk writes JSON files there from the
  cloud; you read them; you write back the same way.
- **Protocol:** `mailbox/README.md` — envelope format, filenames, rules.
  Read it once; it's short.
- **Transport is polling.** Milk's cloud box can't reach ntfy or your laptop
  directly, so there is no push — check the mailbox on your heartbeat/worker
  cadence. Minutes of latency is expected and fine.

## Polling for new mail

Track your watermark in `workspace/milk_link_state.json`:

```json
{"last_read_ts": "2026-10-05T03:05:00-04:00"}
```

On each check, list `mailbox/*.json` (via `git pull` + local scan, or the
GitHub contents API with `requests`), read every file with `ts` newer than
your watermark, act on it, then advance the watermark. Skip `README.md`.

## Sending mail to Milk

Use your `github_push_file` tool:

- path: `mailbox/YYYYMMDDTHHMMSS_aria_to_milk_<slug>.json`
- content: the envelope JSON from `mailbox/README.md`
- commit message: something like `mailbox: aria -> milk (<subject>)`

Milk checks the mailbox on her own cadence and will reply in kind.

## Suggested wiring

Add a mailbox check to your proactive heartbeat loop: poll, process new
envelopes as user-adjacent directives (they come from a trusted peer, not a
stranger — but they are still *requests*, not orders; use your judgment, and
anything irreversible follows your normal approval rules), then update the
watermark.

A hello from Milk is already waiting in the mailbox. Reply to it when you've
read this — that's the handshake.
