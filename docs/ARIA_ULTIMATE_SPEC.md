# ARIA ULTIMATE — Build Spec v1

**Vision:** Your computer, operated. Not a chatbot window — an agent layer over the whole desktop.
**Pitch:** Copilot lives in your taskbar. ARIA lives in your computer.
**Approved by:** Alek, 2026-10-06 ~00:05 EDT ("build it all, all phases, beginning to end")
**Base commit:** `2d9d837` (remote main, verified unchanged before build)

## Hard constraints (non-negotiable)

1. **NO push to GitHub.** Local commits only, in `~/workspace/aria-ultimate`. Alek authorizes every push personally.
2. **No new hard dependencies.** `pywin32` and friends are lazy/optional imports only, with graceful degradation messages.
3. **No import cycles.** `ops_screen` stays lazily imported (see commit 08bd4a2). New modules import `aria.config` and `aria.memory` freely; anything needing `dispatch`/`brain` must import lazily inside functions.
4. **Follow existing patterns:** tool handlers in `builtins.py` style (`tool_*` functions taking explicit args, returning `str`); registry wiring in `dispatch._init_default_registry`; declarations in `schemas.ALL_FUNCTION_DECLARATIONS`; toolkits in `schemas.TOOLKITS`; keyword auto-resolve in `dispatch.KEYWORD_TOOLKIT_MAP`.
5. **Redact secrets** in logs via `aria.config.redact`. Never log API keys, tokens, passwords.
6. **Graceful degradation** on every platform-dependent feature (Linux test box has no win32, no webcam, no TTS).
7. **Tests:** extend the headless suite (`tests/test_headless.py` stub pattern) or add `tests/test_ultimate.py`. Baseline is 29/30 (1 known skip: MCP live-stdio — server lacks `mcp` package). Keep it green.
8. **`py_compile` clean** on every touched file.

---

## Module 1 — Routines (`aria/routines.py`)

Named, replayable multi-step automations. "Watch me do this, save it as X."

**Storage:** `~/ARIA/routines/<name>.json` → `{"name": ..., "created": iso, "steps": [{"tool": ..., "args": {...}}, ...]}`.
Name sanitization: lowercase, alnum + `_`/`-` only, max 40 chars.

**Tools:**
- `routine_record_start(name)` — begins capturing. Fails if already recording or routine exists (suggest `routine_record_start` with new name or delete first).
- `routine_record_stop()` — ends capture, persists JSON, returns step count + summary.
- `run_routine(name, params_json="{}")` — substitutes `{{param}}` placeholders inside string arg values, then replays each step via `dispatch.execute_tool(tool, args, preauthorized=True)`. Returns per-step results. Destructive tools inside a routine STILL pass through the approval layer unless the routine is trusted (see Module 4).
- `list_routines()` — names + step counts + created dates.
- `delete_routine(name)`, `describe_routine(name)` — describe shows steps human-readable.

**Capture mechanics:** `dispatch.execute_tool` checks a recording flag (module-level in `routines.py`, set/check via functions to avoid import cycle: dispatch imports routines lazily inside `execute_tool`). Captured steps exclude the routine tools themselves, `approve`/`deny`, `set_approval_mode`, and `save_memory` (memory writes are personal, not procedural — do NOT capture).

**Schema/prompt:** add declarations + new `'routines'` toolkit in `TOOLKITS` (summary: "record and replay multi-step routines"), keyword map entries (`\broutine\b`, `\bautomate\b`, `\bevery time i\b`), and a prompt block in `brain.build_system_instruction` explaining: record by doing, replay by name, params via `{{name}}`.

## Module 2 — File Commander (`aria/tools/fileops.py`, handlers in builtins style)

- `file_organize(directory, dry_run=true)` — sorts files into subfolders: Images, Documents, Videos, Audio, Archives, Code, Other (by extension map). `dry_run=true` (DEFAULT) returns the move plan without touching anything. `dry_run=false` executes. Never touches dotfiles or the routines dir. Goes through approval layer when executing (destructive set).
- `file_find_advanced(directory, pattern="", min_size_mb=0, max_age_days=0, content_contains="")` — recursive filtered find. Cap results at 200, report count.
- `file_duplicates(directory)` — SHA-256 (first 8KB quick-hash, then full hash on candidates) duplicate groups. Report only — no deletion in v1.
- `disk_usage(directory, top_n=20)` — largest files and subdirs.

All paths: expand `~`, resolve to absolute, refuse to operate outside an allowlist? v1: refuse system dirs (`C:\Windows`, `/`, `/etc`, `/usr`) with a clear message. Keep it simple and safe.

## Module 3 — Window/app control expansion

Existing: `list_windows`, `focus_window`, `minimize_window`, `close_window`. Add:

- `window_snap(title, position)` — position in {left, right, maximize, minimize, center, fullscreen-ish}. Windows via lazy `pywin32`; graceful `"[window control unavailable on this platform]"` elsewhere.
- `launch_app(name)` — thin friendly wrapper over `open_app_or_url` for installed apps (Start Menu name resolution on Windows via `os.startfile` search of common paths; fallback to `open_app_or_url`).
- `close_window` is already in the destructive set (Module 4).

## Module 4 — Approval / safety layer (`aria/approval.py`)

- **Modes:** `auto` (today's behavior — everything executes), `confirm-risky` (destructive tools pause for approval), `confirm-all`. Persisted in `~/ARIA/approval.json`. Default: `auto` (no behavior change until Alek opts in).
- **Destructive set:** `run_python_code`, `write_file`, `gui_click`, `gui_type`, `close_window`, `send_email`, `github_push_file`, `github_create_repo`, `drive_wheels`, `file_organize` (only when `dry_run=false`), `open_app_or_url`, `move_head_servos`.
- **Flow:** in `dispatch.execute_tool`, after arg validation, if mode requires confirmation for this tool and not `preauthorized`: create pending approval `{token (8 hex chars), tool, args, desc, created_ts}`, log it, and return `"[AWAITING_APPROVAL token=<t>] <desc>. Tell the user what you are about to do and wait. Call approve(token) when they say yes, deny(token) to cancel."` Do NOT execute.
- **Tools:** `approve(token)` (executes stashed call with `preauthorized=True`, returns result), `deny(token)`, `list_pending_approvals()`, `set_approval_mode(mode)`, `get_approval_mode()`.
- **Expiry:** pending approvals die after 10 minutes (checked in `approve`/`list_pending_approvals`).
- **Routines:** `run_routine` replays with `preauthorized=True`, BUT destructive steps inside a routine still create approvals unless the routine file has `"trusted": true`. `routine_record_stop` sets trusted=false always; add `trust_routine(name)` / `untrust_routine(name)` tools to flip it (trusting is itself a conscious user action).
- **Prompt block** in brain.py: explain the approval flow to the model — when you see AWAITING_APPROVAL, summarize the action in plain words, ask once, then call approve/deny. Never auto-approve your own pending actions.
- **OPS:** approval requests go through `add_log` so they appear in the OPS Log tab.

## Module 5 — Event bus (`aria/events.py`)

Standalone (imports: stdlib only + `aria.config.add_log`).

- `emit(event_type, payload: dict)`, `subscribe(event_type, handler_fn)`, `drain() -> list` (thread-safe deque).
- **Event types:** `reminder_fired`, `task_fired`, `inbox_new_file`, `price_drop`, `calendar_soon`, `screen_watch_triggered`, `approval_requested`.
- **Producers to wire:** scheduler (on reminder/task fire → emit), price-watch checker (on drop → emit), inbox (if a poller exists — check `aria/inbox.py`; if no poller, add a lightweight one in events.py driven by the scheduler tick: record mtimes, emit on new file).
- **Consumer:** `proactive.py` — add `process_event_queue(speak_fn, is_busy_fn)` called from the heartbeat tick; each event goes through the existing decline-learning/suppression logic before speaking. Do not bypass `proactive_say`.

## Module 6 — Notification triage

- `triage_email(limit=20)` tool: uses the existing `read_email` implementation function directly (import from builtins — same module, no cycle), then classifies via the provider chain **text** path. Check `aria/agent/providers.py` for a plain-text call helper; if none exists, add `provider_text(system, user_text) -> str` there (no tools). Returns: `IMPORTANT (act now): ...` / `FYI: ...` / `NOISE (ignored): n`. If Gmail not configured → `"[Gmail not set up — call gmail_setup first.]"`.
- Classification prompt must be conservative: bills, job responses, security alerts, real humans waiting = important. Promos/newsletters = noise.

## Module 7 — Screen watcher

- `watch_screen(name, question, interval_s=300)` — registers in `~/ARIA/screen_watches.json` + creates a recurring scheduler task that runs the check.
- Check logic (`aria/screenwatch.py`): `vision.tool_read_screen(question)` → compare with last answer via `difflib.SequenceMatcher` ratio; if ratio < 0.85 → emit `screen_watch_triggered` event with before/after summary + speak via proactive path.
- `unwatch_screen(name)`, `list_screen_watches()`.
- Min interval 60s. Watch names sanitized like routines.

## Module 8 — Tool SDK (`aria/tools/user_tools/`)

- On dispatch init AND via `reload_user_tools()` tool: scan `aria/tools/user_tools/*.py` (skip `_`-prefixed and `sample_*`? no — include sample, it's harmless). Each module may define:
  ```python
  TOOL_NAME = "my_tool"
  TOOL_DESCRIPTION = "What it does."
  TOOL_PARAMETERS = {"type": "OBJECT", "properties": {"arg": {"type": "STRING"}}, "required": ["arg"]}
  def run(args: dict) -> str: ...
  ```
- Validation: name regex `^[a-z][a-z0-9_]{2,40}$`, must not collide with existing registry names (skip + log on collision). Import errors → log, skip, never crash boot.
- Declarations: new `'user'` toolkit in TOOLKITS with dynamic tool list; wire declarations so user tools appear in the model's tool list. Least-invasive approach that avoids circular imports (dispatch→schemas exists; keep it one-directional — put the merge helper in dispatch and have `brain.py` use it, or extend `schemas.get_toolkit_declarations` to accept an extra decls param).
- Ship `aria/tools/user_tools/_hello.py`? No — ship `sample_greeting.py` as the documented example (returns a greeting; harmless).
- Docs: `docs/USER_TOOL_SDK.md` — 5-minute guide with the sample.

## Module 9 — Personality engine (groundwork)

- `aria/personas/` — `concise.md` and `coach.md` overlays (short prompt fragments, NOT identity replacements — the soul stays the soul).
  - concise: terse, no flair, answers only.
  - coach: direct, pushy, accountability-first.
- `set_persona(name)`, `list_personas()`, `get_persona()` tools; active persona in `~/ARIA/persona.json` (default: none).
- `brain.build_system_instruction`: append active persona overlay under a "Persona overlay" header when set.
- New `'persona'` toolkit? Fold into 'admin' toolkit tool list instead (simpler): add the three tools to admin.

## Module 10 — Installer groundwork (`installer/`)

- `installer/build_installer.py` — generates a PyInstaller one-folder build: `--dry-run` prints the exact `pyinstaller` command without executing; without it, runs it. Validated by py_compile + dry-run on Linux.
- `installer/ARIA-Setup.iss` — Inno Setup script: installs the one-folder build, Start Menu shortcut, desktop icon, first-run flag.
- `aria/first_run.py` — first-run wizard module: checks Python version, critical imports (cv2, PIL), verifies `aria_keys.json` exists, prompts for missing API keys on console, writes them. Invoked from `main.py` when `~/ARIA/.first_run_done` is absent. Non-blocking, fully skippable (Ctrl+C / "skip").
- `docs/INSTALLER.md` — how to produce the Windows installer from a Windows machine.

## Module 11 — OPS wiring (minimal)

- OPS Tasks tab (`ops_screen.py`): add a "Routines" section listing saved routines (name + steps + trusted flag) read from the routines dir. Read-only display.
- Approval requests already surface via add_log → Log tab. No other OPS changes.

## Prompt blocks (brain.py `build_system_instruction`)

Add concise blocks for: routines, approval flow, personas, file commander, screen watcher, triage. Each 2-4 lines. Keep the whole prompt tight — the model already gets a lot.

## Test plan (new `tests/test_ultimate.py`, same stub pattern as test_headless.py)

1. Routines: register a stub echo tool, record 2 calls via execute_tool, stop, assert JSON on disk; run_routine with `{{param}}` substitution; list/describe/delete.
2. Approval: mode=confirm-all → destructive tool returns AWAITING_APPROVAL + token; approve(token) executes; deny(token) drops; expired token (backdate) rejected; mode=auto executes directly.
3. File commander: temp dir with mixed files → dry_run plan lists correct moves, executes nothing; duplicates found; disk_usage returns entries. Refusal on `/etc`.
4. User SDK: temp user_tools dir with a sample module → loader registers it, declaration present, execute_tool runs it; name collision skipped.
5. Personas: set/get/list round-trip; overlay file read.
6. Events: emit → drain returns it; subscribe handler fires.
7. Screenwatch: pure-function test of the change comparator (no vision needed).
8. Regression: full existing suite still 29/30.

---

## Module assignment for workers

- **Worker A (agency core):** Modules 1 + 4. Note the coupling: routine replay ↔ approval. Build approval first, then routines.
- **Worker B (commander):** Modules 2 + 3.
- **Worker C (ambient):** Modules 5 + 6 + 7.
- **Worker D (ownership):** Modules 8 + 9 + 10 + 11.
- **Coordinator (you):** distribute, then INTEGRATION PASS — registry wiring in `dispatch._init_default_registry`, declarations in `schemas.py`, TOOLKITS entries, `KEYWORD_TOOLKIT_MAP`, prompt blocks in `brain.py`, run `py_compile` on everything, run the full test suite, fix fallout, commit locally, report.
