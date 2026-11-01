# Work Center and reliable execution (1.1)

Open **Work Center** beside Settings in the native dashboard.

## Projects

Personal keeps the existing install-root workspace and memory database. New
projects have separate memories, conversation context, chat/spine logs, task
journals, missions, bookmarks, clipboard staging, generated files, deliverables
and model-usage records under `workspace/projects/<id>/`. Credentials, hardware,
application appearance and existing reusable routines remain installation-wide.
Switching the selected project does not move an in-flight execution: it retains
the project captured when it was submitted. The scheduler continues approved
missions in other projects. Switching projects ends a Gemini Live session; its
existing failover hook re-arms pipeline voice.

Project settings include provider order, code/vision role-model overrides,
maximum response tokens, optional file-tool roots and daily/per-task model
budgets. **Enforce spending limits is off by default**, including existing
installs with saved limit values. Ordinary chat does not need price JSON.
Select that checkbox and save to opt into limits; empty budget fields mean
unlimited on that axis. File roots constrain file
tools, not arbitrary shell or Python code; existing approval and safe-mode
policies govern those tools.

## Work controls and receipts

**Stop speaking** only interrupts audio. **Cancel work** on the dashboard stops
new foreground actions. Work Center also lists active executions and missions,
with separate cancellation and mission pause/resume controls. Cancellation is
cooperative: an external request already in flight may finish. Its receipt is
retained. Pause applies between mission steps.

Every dispatched action has a durable intent and result receipt. Mission step
IDs survive restarts and approval replay; delegated-task resumes retain their
original execution scope. A successful receipt is returned rather than sending
the same action again. If the process died after a request but before its result
was saved, an action with effects becomes uncertain and is not automatically
repeated. Read-only tools can explicitly declare replay safety; unknown tools
are conservative. Verification cannot repeat an effect just because its
output was rejected.

`failed`, `unverified`, `started` and `ask` are distinct from completion. The
shared result adapter preserves legacy string APIs while retaining structured
receipts internally. Delegated tasks with an unavailable or invalid verifier
park for review instead of silently passing.

To reconcile an uncertain mission, inspect its destination first, select the
mission in Work Center and use **Record checked receipt**. Enter the actual
reference you checked. This records a user attestation, not independent service
verification, and lets the remaining steps continue without repeating the
uncertain step. This operation is deliberately absent from model tools.

## Deliverables

Successful file-producing calls are indexed with the producing action ID and
SHA-256 evidence. The Deliverables view previews text, opens files/folders and
checks whether the current file still matches its recorded version. Different
hashes remain separately indexed; old file contents are not an automatic
backup. Missing or changed files are reported explicitly.

## Model budgets

Usage tracking works without local spending limits or configured prices.
To enforce a project budget, check **Work Center → Project settings → Enforce
spending limits**, enter a daily or per-task limit and actual model prices,
then save. Uncheck it and save to stop local project limits from blocking
requests. Saved numerical limits alone do not enable enforcement.

Prices are configured by the owner in USD per million input/output tokens:

```json
{"provider:exact-model-id":{"input":0.1,"output":0.4}}
```

The numbers above illustrate the format; enter your actual provider prices.
A bounded execution refuses an unpriced model. Before each HTTP model attempt,
an atomic reservation covers a conservative prompt bound and the requested
output limit. Returned token usage settles that reservation; missing usage or
an ambiguous failed request retains an estimate, clearly labelled in the UI.
The ledger records provider, model, task scope, latency and routing reason.
Daily limits use UTC. A mission's existing `spend_cap` now bounds its model
calls, defaults to zero, and requires configured zero-priced models or an
explicitly approved larger cap for model-using tools. It is not a purchase or
external-service billing gateway. Gemini Live is disabled while project limits are enforced
because its realtime session cannot use this per-request reservation contract.

## Resource profiles

Full keeps the original visible animation cadence. Balanced reduces idle and
minimized rendering. Minimal also suppresses optional life-room supervision,
face tracking and audio loopback at startup. Microphone and speech controls
remain available. Select the profile in Models and resources; background-service
changes apply after restart. CPU, resident memory, battery and startup figures
are measurements, not promised savings.

## Validation

`python scripts/test_suites.py` discovers every `test_*.py` and runs each in its
own pytest process, preserving legacy module-stub isolation. External Python
network requests are blocked by the test fixture; localhost HTTP/WebSocket
fixtures remain available. Windows CI installs the hash-locked product, checks
native startup and the COM helper, builds the frozen app and compiles the
installer. `python aria.py --diagnostics` uses temporary data and stays offline;
it creates the real dashboard and Work Center without starting microphones or
contacting model providers. Actual microphone, speaker and laptop-driver
behavior still requires a hardware session.
