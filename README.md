# A.R.I.A. — Adaptive Robotic Intelligence Agent

A Python desktop AI companion. She lives on your Windows laptop as an animated
face in an OpenCV HUD: she talks (natural Edge TTS voice), listens (mic +
speech recognition), remembers, and acts through a multi-provider agent loop
(Ollama Cloud → Groq → OpenRouter → Mistral) with 95 tools across 16 toolkits. Her phone bridge turns any phone into her
face, voice, and eyes; she connects to MCP servers and uses their tools as her
own; an Arduino robot body gives her a pan/tilt head and differential-drive
wheels; and a tabbed OPS command center puts her log, tasks, sensors, and
routines one keypress away.

Built by Alek Berger. Not a framework, not a demo — a finished companion.

## Features

- **Animated HUD Face & Full-Body Pixel Avatar** (OpenCV) — expressive
  pixel-person avatar with fluid cognitive-state expressions: idle, listening,
  speaking, thinking, tool-running (code-eyes), sleepy, and reactive poses
  (arms, scratch, listen). Waveform mouth; twin ear antennae with synchronous
  pulsing tips and ear twitches; customizable HUD color themes
  (`aria/theme.cfg`). Interactive **context tiles** (dashboard, subtitles,
  audio, tasks, Spotify) that collapse and cycle. The subsystem box reports
  live status for every subsystem, including which camera her eyes use
  (`[bridge]`, `[cam N]`, or `[net]`), and the CAM.01 optic PIP shows the live
  camera feed.
- **Voice In / Voice Out** — Edge TTS neural voice with sentence boundary
  streaming and pipelined TTS synthesis prefetch; instant speech interruption
  barge-in via the `X` key or voice; desktop mic input prefers local
  faster-whisper transcription when installed, with Google cloud fallback;
  hold-SPACE push-to-talk; wake-word listener loop.
- **Whisper Mode & Mood Engine** — toggleable whisper mode (W key, voice, or HUD)
  for softer volume, concise replies, and quieted heartbeats; mood system
  (energy/warmth axes) flavoring idle facial tempo and greetings.
- **Agent Brain** — multi-provider function-calling loop (no arbitrary turn cap;
  30-minute reasoning budget per request — on timeout she asks "Should I keep
  going?" and "yes"/"continue" resumes the active chain). Ollama Cloud
  (gpt-oss:120b) is the primary brain, failing over through Groq, OpenRouter,
  and Mistral, with per-key rotation and automatic quarantine on rate limits
  and errors. The HUD reports which provider is serving and flags fallbacks.
  (Screenshot vision still uses Gemini.)
- **Progressive Tool Loading** — only the core tool schemas go to the model
  per call; specialist toolkits (autonomy, Gmail, Spotify, scheduler, GitHub,
  vision/hardware, Windows control, MTG, memory/notes, admin, MCP, routines,
  files, monitor, user) unlock on demand via `load_toolkit` or automatically
  from keywords in your request. 95 tools total across 16 toolkits, with
  duplicate-call blocking, argument repair, and parallel execution of
  independent calls.
- **MCP Client** — she connects to Model Context Protocol servers and uses
  their tools as her own. `mcp_setup` adds a server (stdio command or
  SSE/streamable-HTTP URL), `mcp_connect` connects and registers every server
  tool as a first-class ARIA tool named `mcp_<server>__<tool>` (argument
  validation and output truncation apply like any other tool),
  `mcp_list_servers` shows connection status, `mcp_disconnect` unloads.
  Configured servers auto-connect in the background at boot. Requires the
  `mcp` Python package; only connect servers you trust, since stdio servers
  run local commands.
- **OPS Command Center** — press `O` for a tabbed mission-control overlay
  inside the Python window: Log, Tasks, Sensors, Controls, Notes, HUB, Day.
  `H` still toggles the commands view; `1–7` switch tabs, `J/K` scroll.
  Real Roboto Mono typography (bundled, rendered via PIL), rounded panels,
  and honest sensors — a missing `psutil` shows an install hint instead of
  fake gauges, and the GPU gauge only appears when `nvidia-smi` reports one.
  The Tasks tab lists scheduled jobs, background workers, and saved routines.
- **Routines** — teach her a multi-step workflow once ("save that as
  *stream setup*"), replay it forever by name. Steps record from real tool
  calls, support `{{parameter}}` slots, and carry a trusted flag — untrusted
  routines still ask before destructive steps.
- **Approval layer** — `set_approval_mode`: `auto` (today's behavior),
  `confirm-risky`, or `confirm-all`. In confirm modes a destructive action
  pauses with a token; say the word and she runs `approve`, or `deny` to drop
  it. Approvals expire after 10 minutes and surface in the OPS Log.
- **File Commander** — `file_organize` sorts a messy folder by type
  (dry-run plan first, always — nothing moves until you say so),
  `file_find_advanced` searches by name/size/age/content, `file_duplicates`
  finds byte-identical copies (report only), `disk_usage` shows what's eating
  the drive. System directories are refused outright.
- **Window control** — beyond list/focus/minimize/close: `window_snap` tiles
  windows (left/right/maximize/center), `launch_app` opens installed apps by
  name.
- **Ambient event bus** — reminders, price drops, new inbox files, and
  calendar-soon events flow through the heartbeat's existing decline-learning,
  so she speaks up about what matters and stays quiet about what you've
  dismissed.
- **Email triage & screen watcher** — `triage_email` sorts unread mail into
  act-now / FYI / noise; `watch_screen` takes a standing vision question
  ("tell me when the build finishes") and alerts on change.
- **Price watcher, for real** — `watch_price` now actually checks hourly and
  fires a `price_drop` event instead of just promising to.
- **Tool SDK** — drop a Python file in `aria/tools/user_tools/` defining
  `TOOL_NAME`, `TOOL_DESCRIPTION`, `TOOL_PARAMETERS`, and `run(args)`, and
  she learns a new skill at boot (`reload_user_tools` picks up changes live).
  Five-minute guide in `docs/USER_TOOL_SDK.md`.
- **Personas** — switchable prompt overlays (`concise`, `coach`) via
  `set_persona`; her soul stays her soul.
- **Installer groundwork & first-run wizard** — `installer/` holds a
  PyInstaller one-folder build generator (`--dry-run` validated) plus an
  Inno Setup script for a real Windows installer; on first launch she walks
  through missing API keys herself (skippable). Build notes in
  `docs/INSTALLER.md`.
- **Autonomy: Heartbeat, Workers, Self-Healing** — a proactive heartbeat daemon
  that acts on important events on its own; persistent background workers for
  long jobs, a downloads watcher, and system resource monitoring (with alerts
  when CPU/memory/disk cross thresholds); and a self-healing loop that
  records incidents, diagnoses errors, and attempts automatic recovery.
  Manage it all with `manage_autonomous_goal`, `manage_background_job`,
  `system_health_audit`, and `self_heal_diagnose`.
- **Durable Memory & Spine** — SQLite semantic vector memory + Markdown journal;
  unbroken memory spine (`memory_spine.jsonl`) logging turns, tool invocations,
  and journal entries; session context (`where_we_left_off.md`) automatically
  restored at boot.
- **Hot Package Self-Restart** — she can edit her own codebase or `soul.md`;
  the agent fingerprints every `.py` file in the `aria/` package plus
  `soul.md` at boot and cleanly re-executes on verified modifications.
- **Scheduler** — one-shot and recurring reminders/jobs, morning briefings,
  break reminders.
- **Phone Bridge (HTTPS)** — secure mobile companion web app (token login +
  cookie auth, per-machine self-signed certificate) with a sleek minimal UI:
  her big pixel face as the hero, Face / Eyes / Full view modes, and
  fullscreen face mode for the mounted phone (tap to exit). The bridge page
  can be her camera: tap Camera ON and the phone streams its front (or rear,
  via the flip switch) camera to the laptop as `ARIA_BODY_CAMERA=bridge`.
  Eyes mode shows the live camera view (`/phone_cam.mjpg`); "Describe what
  you see" snapshots a frame and has her narrate it (`/api/look`).
  Hold-to-talk and tap-to-talk voice, text directives, settings (speak
  replies, test audio, camera), and a toggleable conversation log. Spoken
  replies use Edge TTS first with offline Windows SAPI as fallback.
  The **/upload page** lets you send files from the phone straight into her
  inbox — a background watcher picks them up and she can read, describe, or
  act on them (`inbox_list`, `inbox_describe`, `inbox_read`).
- **Robot Body** — Arduino Nano firmware (`arduino/aria_body/aria_body.ino`)
  driving a pan/tilt head (`P<pos>T<pos>`) and differential-drive wheels
  (`W<l>,<r>`) over a no-solder Nano + sensor shield, with a dedicated stop
  command (`S`) and an animated OLED face — eyes that blink and glance where
  the head turns, plus a smile (1.3" SH1106 I2C display, U8G2 library).
  `drive_wheels` / `body_stop` tools, `ARIA_BODY_SERIAL_URL`
  for network serial, and `sim/robot_sim.py`, a virtual body for testing
  without hardware. Her eyes select via `ARIA_BODY_CAMERA`: USB index, IP
  camera URL, or `bridge`. Full build guide in `docs/ROBOT_BODY.md`.

## Requirements

- Windows 10/11, Python 3.9+ (**Python 3.9 recommended** — PyAudio and pygame
  install cleanly there; if you launch under a newer Python that's missing
  them, `aria.py` automatically relaunches under 3.9 when it's installed)
- An [Ollama Cloud](https://ollama.com/) API key (`OLLAMA_API_KEY` — free
  starter tier) for her primary brain (gpt-oss:120b); Groq / OpenRouter /
  Mistral keys are optional fallbacks. A Gemini key is still used for
  screenshot vision.
- Microphone + speakers (webcam optional, used for face tracking / vision)

## Quick start

```bat
python -m pip install -r requirements.txt
python aria.py
```

First run creates `aria_keys.json` next to the script. Add your keys with:

```bat
python tools\aria_add_key.py
```

(or set `OLLAMA_API_KEY` / `GEMINI_API_KEY` / `GITHUB_TOKEN` as environment
variables — env vars take precedence over `aria_keys.json`). On first launch
she runs a short setup wizard that checks dependencies and walks through any
missing keys (Ctrl+C skips it).

Gmail sending and reading use an app password, saved via ARIA's `gmail_setup` tool (`send_email` / `read_email`).

To give her the phone face: on the laptop `set ARIA_BODY_CAMERA=bridge`,
open the bridge page on the phone (see Bridge URL printed at startup), log in
with your bridge token, and tap Camera ON in the ⚙ settings panel.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'numpy'`** (or cv2, etc.) — the
  interpreter running `aria.py` doesn't have the dependencies. Run
  `python -m pip install -r requirements.txt` with the *same* `python` you
  launch her with. If `pip` and `python` disagree about environments
  (`where python` shows more than one), install into the right one explicitly.
- **`pygame` fails to build** — pip couldn't find a prebuilt wheel for your
  Python and the from-source build needs a C++ toolchain. Upgrade pip first
  (`python -m pip install --upgrade pip setuptools wheel`) and retry. If it
  still fails, skip it: pygame is only used for local audio playback and is
  imported lazily — she boots and runs fine without it. Install everything
  else with:
  `findstr /v /b "pygame" requirements.txt > %TEMP%\aria-req.txt`
  followed by `python -m pip install -r %TEMP%\aria-req.txt`.
- **She goes quiet after an update** — pull the latest code and restart her;
  she also restarts herself when she edits her own code.

## Auto-start on boot

1. Copy `windows\aria_autostart.bat` and `windows\aria_watchdog.bat` to the
   same folder as `aria.py`
2. Double-click `aria_autostart.bat` once and enter your `aria.py` path
   (e.g. `E:\ARIA\aria.py`)
3. It installs the watchdog into your Windows Startup folder — ARIA launches
   on every boot and restarts automatically if she crashes

## Project layout

```
aria.py                  Root entrypoint / launcher (auto-redirects to
                         Python 3.9 when PyAudio/pygame are missing)
aria/                    Modular system package
  agent/                 Multi-provider agent loop, streaming, prompt
                         engineering, self-edit auto-restart watcher
    brain.py             Central orchestrator: prompt building, function-
                         calling loop, sandboxing, supervision
    proactive.py         Proactive heartbeat daemon (consumes the event bus)
    workers.py           Persistent background jobs, downloads watcher,
                         system resource monitor
    self_healing.py      Incident log, error diagnosis, auto-heal attempts
    shortcuts.py         Voice/text shortcuts
  hud.py                 OpenCV HUD: subsystem status rows, optic PIP,
                         collapsible context tiles, overlay renderer
  pixel_avatar.py        Full-body pixel-person avatar, reactive cognitive
                         expressions, ear antennae
  theme.cfg              HUD color theme definitions
  speech.py              Edge TTS streaming prefetch, Whisper/Google STT,
                         bridge TTS + transcription
  bridge.py              HTTPS phone bridge server: face/eyes UI, live MJPEG
                         streams, voice + camera endpoints, /upload page
  inbox.py               Phone-upload inbox: watcher + list/describe/read
  memory.py              SQLite semantic vector memory, journal, memory spine
  mcp.py                 MCP client: connect to MCP servers, register their
                         tools as ARIA tools (mcp_<server>__<tool>)
  routines.py            Record-and-replay multi-step routines
  approval.py            Approval layer: auto / confirm-risky / confirm-all
  events.py              In-process event bus (reminder_fired, price_drop, …)
  screenwatch.py         Standing vision questions with change detection
  persona.py             Switchable persona overlays (concise, coach)
  personas/              Persona overlay fragments
  pricecheck.py          Hourly price-watch checker (emits price_drop)
  first_run.py           First-launch setup wizard (deps + API keys)
  ops_screen.py          Tabbed OPS command center (O: Log/Tasks/Sensors/
                         Controls/Notes/HUB/Day)
  scheduler.py           Autonomous scheduler and reminders
  spotify.py             Desktop Spotify player control & DJ mode
  hardware.py            Arduino serial: servo head, drive wheels, body tools
  vision.py              Camera capture (USB / IP / bridge), face tracking,
                         screen analysis, phone-frame ingest
  tools/                 Core tools & on-demand specialist toolkits
    schemas.py           Tool declarations + 16 progressive toolkits
    dispatch.py          Registry, execution, parallel calls, dedup
    sandbox.py           Risky-tool gating, argument validation
    builtins.py          Tool implementations
    skills.py            Bounded workflow skills
    fileops.py           File commander: organize/find/duplicates/disk usage
    winctl.py            Window snap + app launching
    triage.py            Email triage (IMPORTANT / FYI / NOISE)
    usertools.py         User tool SDK loader
    user_tools/          Drop-in custom tools (sample_greeting.py)
arduino/aria_body/       Nano firmware: P/T head, W wheels, S stop protocol
sim/robot_sim.py         Virtual robot body (TCP) for hardware-free testing
docs/ROBOT_BODY.md       No-solder robot body build guide
docs/ARIA_ULTIMATE_SPEC.md  Ultimate build spec (all 11 modules)
docs/USER_TOOL_SDK.md    5-minute custom-tool guide
docs/INSTALLER.md        Building the Windows installer
installer/               PyInstaller one-folder generator + Inno Setup script
tests/test_headless.py   Headless test suite (no camera/mic/hardware needed)
tests/test_ultimate*.py  Ultimate module tests (50 tests)
workspace/               Runtime data (memory DB, spine, models, mood, chat logs)
soul.md                  Her persona & standing directives — loaded on boot
requirements.txt         Python dependencies
windows/                 Auto-start installer + watchdog (.bat)
tools/                   Helper scripts (key manager)
```

## Version history

### Latest

- **ARIA Ultimate** — the all-encompassing desktop agent build: **routines**
  (record once, replay by name with `{{parameter}}` slots), an **approval
  layer** (`auto` / `confirm-risky` / `confirm-all` with token approve/deny),
  a **file commander** (dry-run-first organize, advanced find, duplicate
  detection, disk usage), **window snap + app launching**, an **ambient event
  bus** feeding the heartbeat, **email triage** and a **screen watcher**, a
  working **hourly price checker**, a **tool SDK** (`aria/tools/user_tools/`
  drop-ins), switchable **personas**, and **installer groundwork**
  (PyInstaller one-folder generator, Inno Setup script, first-run key
  wizard). 95 tools across 16 toolkits; 50 new tests green.
- **OPS command center** — press `O` for a tabbed overlay (Log, Tasks,
  Sensors, Controls, Notes, HUB, Day) inside the Python window; `H` keeps the
  commands view. Visual overhaul with real Roboto Mono typography via PIL,
  rounded panels, and honest sensors (missing `psutil` shows an install hint;
  GPU gauge only when `nvidia-smi` reports one). Fixed the boot-killing
  circular import (`spotify → dispatch → hud → ops_screen → agent →
  shortcuts → spotify`) by lazily importing `ops_screen`.
- **Ollama Cloud primary** — the default brain chain is now ollama_cloud
  (gpt-oss:120b) → Groq → OpenRouter → Mistral; Gemini leaves the default
  chain while its keys return 402 (screenshot vision stays Gemini-only).
  The HUD reports the serving provider and flags fallbacks.
- **HUD fault tracebacks** — full exception tracebacks now land in the log
  instead of a bare fault line; fixed an undefined `List` import in
  proactive.

- **Optic PIP live-frame fix** — the CAM.01 picture-in-picture box could never
  render video: `hud.py` bound `LATEST_CAMERA_FRAME` by value at import time
  (permanently `None`) while `vision.py` rebinds it per capture. The frame is
  now read live off the vision module on every draw.
- **Python 3.9 auto-redirect** — `aria.py` detects a Python missing PyAudio /
  pygame at launch and relaunches itself under the Python 3.9 install when
  present; self-restarts prefer 3.9 too. Ends the "works on one interpreter,
  breaks on another" era.
- **HUD upgrade** — collapsible context tiles (dashboard, subtitles, audio,
  tasks, Spotify), full-body pixel avatar with reactive cognitive-state
  expressions and poses, subsystem rows capped to fit cleanly above the optic
  PIP, which moved, resized, and was relabeled CAM.01.
- **Autonomy pack** — proactive heartbeat daemon, persistent background
  workers (long jobs, downloads watcher, system resource monitor with
  alerts), and a self-healing loop (incident log → diagnosis → auto-heal
  attempts). New `autonomy` toolkit: `manage_autonomous_goal`,
  `manage_background_job`, `system_health_audit`, `self_heal_diagnose`.
- **Phone bridge inbox uploads** — `/upload` page on the bridge sends files
  straight into her inbox; a background watcher picks them up and
  `inbox_list` / `inbox_describe` / `inbox_read` let her work with them.
  Inbox-first photo routing: "look at" a sent/uploaded photo uses the inbox,
  never the webcam.
- **MCP streamable-HTTP transport** — pure-Python MCP client support for
  streamable-HTTP servers alongside stdio and SSE; UI dynamic scaling.

### Since v9.36

- **MCP client** — ARIA connects to Model Context Protocol servers and uses
  their tools: `mcp_setup` / `mcp_connect` / `mcp_disconnect` /
  `mcp_list_servers` / `mcp_remove_server` (new `mcp` toolkit); every server
  tool is registered at connect time as a first-class ARIA tool
  (`mcp_<server>__<tool>`) with its JSON Schema converted to Gemini function
  declarations, so argument validation, duplicate-call blocking, and output
  truncation all apply. stdio and SSE/HTTP transports, persistent sessions on
  a background asyncio loop, background auto-connect of enabled servers at
  boot; `mcp>=1.0,<2` added to requirements.
- **Robot body** — Arduino Nano firmware with eased pan/tilt head, timed
  differential-drive wheels, and dedicated stop; `drive_wheels` / `body_stop`
  agent tools; `ARIA_BODY_SERIAL_URL` for network serial; `sim/robot_sim.py`
  virtual body; full no-solder build guide in `docs/ROBOT_BODY.md`; wheels
  chosen over tracks for the mobile base.
- **Body camera sources** — `ARIA_BODY_CAMERA` accepts a USB index, an IP
  camera stream URL, or `bridge` (frames uploaded by the phone bridge page);
  snapshots and face tracking work on all three; HUD subsystem box shows the
  active source (`[bridge]` / `[cam N]` / `[net]`).
- **Bridge-page camera** — the phone bridge streams its own camera
  (480x360 JPEG, ~3 fps) to `POST /api/camframe`; one phone is now her face,
  voice, and eyes with no extra apps; front/rear flip switch in the header.
- **Bridge redesign** — sleek minimal dark UI: her pixel face as the hero,
  Face / Eyes / ⛶ Full segmented control, fullscreen face mode for the
  mounted phone (tap to exit), live Eyes viewer (`/phone_cam.mjpg`),
  "Describe what you see" snapshot narration (`POST /api/look`),
  hold-to-talk + tap-to-talk, ⚙ settings panel (speak replies, test audio,
  camera), toggleable conversation log, UTF-8 header icons.
- **Bridge hardening** — token login page + HttpOnly SameSite cookie auth on
  all routes, unknown paths 404, configurable bind host, per-machine
  self-signed certificate (PowerShell/.NET fallback when `cryptography` is
  broken), loud TTS failures surfacing the real engine error, Edge-first
  TTS with offline SAPI fallback, no `?token=` in media URLs.
- **Bridge confirmation gating removed** — risky tools now execute immediately
  with an audit log instead of pausing for yes/no.
- `.gitattributes` normalizes line endings to LF (prevents CRLF/LF merge
  conflicts on Windows).

- **v9.36** — Voice pipeline & false wake hardening: removed noisy VAD
  bypass, eliminated self-hearing mic echo during TTS playback, and enforced
  strict wake phrase recognition confidence; audio resampling and wake lock
  handling optimizations.
- **v9.35** — Pixel Avatar face overhaul & modular refactor:
  - Refactored monolithic codebase into the modular `aria/` package; hot
    self-restart watcher upgraded to fingerprint the full package tree.
  - New Pixel-person avatar HUD with expressive face states: idle, listening,
    speaking waveform mouth, thinking / green code-eyes face during model tool
    calls, and floating diagonal sleepy Z's.
  - Twin ear antennae with synchronized pulsating tips and twitch animations.
  - HUD color themes configurable via `theme.cfg`.
  - Instant speech barge-in via the `X` key to cut off active TTS playback.
  - Phone bridge audio reply playback fixes and mobile stability improvements.
- **v9.34** — Whisper mode (softer/quieter voice, 1–2 sentence replies, no
  heartbeat chatter except the low-battery safety alert; toggled by voice,
  HUD button, or the W key; persisted) and a mood system (energy/warmth
  axes, one HUD mood word, flavors greetings and idle-face tempo only —
  never facts or answers).
- **v9.33** — Everything lives under the script folder: a one-time startup
  migration moves any leftover ~/robot_workspace data (memory DB, chat
  history, journal, models, …) into <script-dir>/workspace, never
  overwriting files already there.
- **v9.32** — HUD subsystems panel right-aligns the status column inside
  the box; long statuses like "ARMED (1)" no longer spill past the edge.
- **v9.31** — Phone bridge text replies speak aloud on the phone: after a
  text reply arrives, the bridge page fetches its audio from /api/say and
  plays it (same as hold-to-talk); Speak replies ON/OFF toggle persisted
  in localStorage. Audio trims at 500 chars; full text still shows.
- **v9.30** — Merges her streaming chat-log fix with the v9.28 feature set:
  her cure (always log the full reply text after the stream) is kept, with
  the HUD subtitle/draw kept out of silent turns; live subsystem rows,
  last-tool readout, and the 30-minute loop budget are restored.
- **v9.29** — Streamed speech returns to the tactical chat log: v9.25's
  sentence streaming bypassed speak(), so streamed replies were spoken but
  never logged; the post-stream branch now records the full reply text.
- **v9.28** — Live HUD subsystem status tracking: every subsystem row now
  reports its real state instead of a hardcoded label — camera/screen
  reflect the last capture attempt, memory runs a cached DB probe, sandbox
  shows RUNNING while code executes, scheduler shows a live pending-task
  count; plus a new last-tool row showing the most recently executed tool
  and its time. Agent-loop time budget raised from 10 to 30 minutes per
  request.
- **v9.27** — Pipelined parallel TTS prefetching + early clause speech emission:
  text generation feeds a dedicated prefetch synthesis worker in parallel with
  playback, eliminating the 1-2s gap between sentences; early clause boundary
  detection begins vocalizing long opening sentences after the first clause
  (>=3 words), cutting time-to-first-sound.
- **v9.26** — Streaming-speech queue fix: the pre-tool speech stop now uses
  interrupt_speech() (the old inline drain skipped task_done, leaking the
  unfinished-tasks counter and leaving the stop event set, which silently
  muted streaming on every tool-call turn); the stop event is cleared
  before each fresh streaming call.
- **v9.25** — Streaming Sentence TTS & Context Diet: real-time SSE token
  streaming from Gemini detects sentence boundaries on the fly and enqueues
  speech instantly, cutting voice latency to <800ms; unique memory key
  indexing with ON CONFLICT DO UPDATE; internal system keys isolated from
  the prompt context; dynamic semantic memory retrieval injects user identity
  and top-k semantically relevant memories.
- **v9.24** — Self-edit auto-restart: she fingerprints her script + soul.md
  at boot; a changed fingerprint at turn end triggers an announced,
  compile-checked self-relaunch (deferred while confirming; a new turn
  cancels a pending restart)
- **v9.23** — Barge-in: any new typed, mic, PTT, wake-word, or phone-bridge
  message stops her current speech immediately; empty transcripts don't
  interrupt
- **v9.22** — Every face gets a mouth: yellow waveform ripple on thinking /
  working / listening, green on coding (idle/speaking keep pink); working
  radar arc moved down to clear the mouth
- **v9.21** — "Open the video downloader" shortcut: probes localhost:3003,
  starts the server via start-windows.bat when it's down, then opens the page
  (port is a one-line constant now)
- **v9.20** — File/app launching quotes the path (same as URLs already
  did), so paths with spaces like "The Boy and the Heron" open correctly
  instead of truncating at the first space
- **v9.19** — Version numbers removed from all user-visible text and code
  (HUD, subtitles, console, spoken greeting, comments); the header
  changelog keeps the full version history
- **v9.18** — Subtitle sanitizer: vocal subtitles now pass through the
  HUD's `_hud()` ASCII sanitizer like the chat log and action stream,
  so curly quotes and other Unicode punctuation render correctly
- **v9.17** — Back to Google cloud transcription by default; the local
  faster-whisper path stays in the file, dormant behind `_USE_LOCAL_STT`
- **v9.16** — Hallucination guard for local STT: voice-activity filtering
  (Silero VAD), no conditioning on previous text, and segments the model
  itself flags as non-speech are dropped -- silence becomes "didn't catch
  that" instead of invented words
- **v9.15** — Loop cap removed: no turn limit, 10-minute budget per request;
  on timeout she asks "Should I keep going?" and "yes"/"continue" resumes
  the same reasoning chain
- **v9.14** — Local speech recognition (faster-whisper on CPU) with silent
  Google fallback
- **v9.13** — Reliable morning brief: scheduled briefs run the briefing
  routine directly instead of through the model
- **v9.12** — Face-only video stream on the phone bridge (`/face.mjpg`)
- **v9.11** — Push-to-talk: hold SPACE to record, release to transcribe
- **v9.10** — Unbroken memory spine: append-only turn/tool/journal log,
  `where_we_left_off.md` at shutdown, recent context reloads at boot
- **v9.9** — Secret redaction, heartbeat decline learning, animated faces,
  bounded workflow skills, key-triggered input off the main thread
- **v9.8** — Progressive tool loading, duplicate-call blocking, argument
  repair; fixed 4 wrong required-field declarations
- **v9.7** — Explicit "open that link from earlier" references resolve URLs
  from conversation history
- **v9.6** — Gmail sending via app password
- **v8.3** — Key rotation on 5xx errors; "open Spotify" action; voice media keys
- **v7.11** — Current face: pink waveform mouth, lash flicks, pink iris rings,
  plum eyeliner, larger eyes

## Notes

- `aria_keys.json`, logs, memory DB, and journal are gitignored — they stay
  on your machine and never get committed.
- Tested on Windows 11 (Python 3.9 for full audio support; 3.10+ works, and
  `aria.py` redirects to 3.9 automatically when PyAudio/pygame are missing).
- The v9.17 changelog entry ("Google cloud transcription by default, local
  faster-whisper dormant behind `_USE_LOCAL_STT`") was never implemented —
  no such flag exists in the code. Current behavior: the desktop mic prefers
  local faster-whisper whenever it is installed, falling back to Google cloud
  transcription, and the bridge's voice messages are transcribed by Gemini.
