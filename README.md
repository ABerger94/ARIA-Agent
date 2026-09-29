# A.R.I.A. — Adaptive Robotic Intelligence Agent

A Python desktop AI companion. She lives on your Windows laptop as an animated
face in an OpenCV HUD: she talks (natural Edge TTS voice), listens (mic +
speech recognition), remembers, and acts through a Gemini-powered agent loop
with 54 tools. Her phone bridge turns any phone into her face, voice, and
eyes — and an Arduino robot body gives her a pan/tilt head, with
differential-drive wheels as the next phase.

Built by Alek Berger. Not a framework, not a demo — a finished companion.

## Features

- **Animated HUD Face & Pixel Avatar** (OpenCV) — expressive pixel avatar
  with animated idle, listening, speaking, thinking, tool-running (code-eyes),
  and sleepy states; waveform mouth; twin ear antennae with synchronous pulsing
  tips and ear twitches; customizable HUD color themes (`aria/theme.cfg`).
  The HUD subsystem box reports live status for every subsystem, including
  which camera her eyes use (`[bridge]`, `[cam N]`, or `[net]`).
- **Voice In / Voice Out** — Edge TTS neural voice with sentence boundary
  streaming and pipelined TTS synthesis prefetch; instant speech interruption
  barge-in via the `X` key or voice; desktop mic input prefers local
  faster-whisper transcription when installed, with Google cloud fallback;
  hold-SPACE push-to-talk; wake-word listener loop.
- **Whisper Mode & Mood Engine** — toggleable whisper mode (W key, voice, or HUD)
  for softer volume, concise replies, and quieted heartbeats; mood system
  (energy/warmth axes) flavoring idle facial tempo and greetings.
- **Agent Brain** — Gemini function-calling loop (no arbitrary turn cap;
  30-minute reasoning budget per request — on timeout she asks "Should I keep
  going?" and "yes"/"continue" resumes the active chain).
- **Progressive Tool Loading** — only 17 core tool schemas go to the model
  per call; specialist toolkits (Gmail, Spotify, scheduler, GitHub,
  vision/hardware, Windows control, MTG, memory/notes, admin) unlock on demand
  via `load_toolkit`. 54 tools total across 10 toolkits.
- **Durable Memory & Spine** — SQLite semantic vector memory + Markdown journal;
  unbroken memory spine (`memory_spine.jsonl`) logging turns, tool invocations,
  and journal entries; session context (`where_we_left_off.md`) automatically
  restored at boot.
- **Hot Package Self-Restart** — she can edit her own codebase or `soul.md`;
  the agent fingerprints every `.py` file in the `aria/` package plus
  `soul.md` at boot and cleanly re-executes on verified modifications.
- **Scheduler & Proactive Heartbeat** — one-shot and recurring reminders/jobs;
  autonomous background heartbeat when important events or tasks occur.
- **Phone Bridge (HTTPS)** — secure mobile companion web app (token login +
  cookie auth, per-machine self-signed certificate, reachable over Tailscale)
  with a sleek minimal UI: her big pixel face as the hero, Face / Eyes / Full
  view modes, and fullscreen face mode for the mounted phone (tap to exit).
  The bridge page can be her camera: tap Camera ON and the phone streams its
  front (or rear, via the flip switch) camera to the laptop as
  `ARIA_BODY_CAMERA=bridge`. Eyes mode shows the live camera view
  (`/phone_cam.mjpg`); "Describe what you see" snapshots a frame and has her
  narrate it (`/api/look`). Hold-to-talk and tap-to-talk voice, text
  directives, settings (speak replies, test audio, camera), and a toggleable
  conversation log. Spoken replies use Edge TTS first with offline Windows
  SAPI as fallback.
- **Robot Body** — Arduino Nano firmware (`arduino/aria_body/aria_body.ino`)
  driving a pan/tilt head (`P<pos>T<pos>`) and differential-drive wheels
  (`W<l>,<r>`) over a no-solder Nano + sensor shield, with a dedicated stop
  command (`S`) and an animated OLED face — eyes that blink and glance where
  the head turns, plus a smile (1.3" SH1106 I2C display, U8g2 library).
  `drive_wheels` / `body_stop` tools, `ARIA_BODY_SERIAL_URL`
  for network serial, and `sim/robot_sim.py`, a virtual body for testing
  without hardware. Her eyes select via `ARIA_BODY_CAMERA`: USB index, IP
  camera URL, or `bridge`. Full build guide in `docs/ROBOT_BODY.md`.
- **54 Built-in Tools** — Spotify control & DJ mode, Windows desktop automation
  (apps, URLs, mouse clicks, keystrokes, window focus, media keys), file search,
  Gmail send/read, GitHub repository operations, MTG Commander lookups, robot
  head/wheel control, vision screen-reading and camera description, and more.

## Requirements

- Windows 10/11, Python 3.10+
- A free [Google AI Studio](https://aistudio.google.com/) Gemini API key
- Microphone + speakers (webcam optional, used for face tracking / vision)

## Quick start

```bat
pip install -r requirements.txt
python aria.py
```

First run creates `aria_keys.json` next to the script. Add your keys with:

```bat
python tools\aria_add_key.py
```

(or set `GEMINI_API_KEY` / `GITHUB_TOKEN` as environment variables — env vars
take precedence over `aria_keys.json`).

Gmail sending uses an app password, saved via ARIA's `gmail_setup` tool.

To give her the phone face: on the laptop `set ARIA_BODY_CAMERA=bridge`,
open the bridge page on the phone (see Bridge URL printed at startup), log in
with your bridge token, and tap Camera ON in the ⚙ settings panel.

## Auto-start on boot

1. Copy `windows\aria_autostart.bat` and `windows\aria_watchdog.bat` to the
   same folder as `aria.py`
2. Double-click `aria_autostart.bat` once and enter your `aria.py` path
   (e.g. `E:\ARIA\aria.py`)
3. It installs the watchdog into your Windows Startup folder — ARIA launches
   on every boot and restarts automatically if she crashes

## Project layout

```
aria.py                  Root entrypoint / launcher
aria/                    Modular system package
  agent/                 Gemini agent loop, streaming, prompt engineering,
                         self-edit auto-restart watcher
  hud.py                 OpenCV HUD, subsystem status rows, overlay renderer
  pixel_avatar.py        Pixel-person avatar, animated expressions, ear antennae
  theme.cfg              HUD color theme definitions
  speech.py              Edge TTS streaming prefetch, Whisper/Google STT,
                         bridge TTS + transcription
  bridge.py              HTTPS phone bridge server: face/eyes UI, live MJPEG
                         streams, voice + camera endpoints
  memory.py              SQLite semantic vector memory, journal, and memory spine
  scheduler.py           Autonomous scheduler, reminders, proactive heartbeat
  spotify.py             Desktop Spotify player control & DJ mode
  hardware.py            Arduino serial: servo head, drive wheels, body tools
  vision.py              Camera capture (USB / IP / bridge), face tracking,
                         screen analysis, phone-frame ingest
  tools/                 Core tools & on-demand specialist toolkits
arduino/aria_body/       Nano firmware: P/T head, W wheels, S stop protocol
sim/robot_sim.py         Virtual robot body (TCP) for hardware-free testing
docs/ROBOT_BODY.md       No-solder robot body build guide
tests/test_headless.py   Headless test suite (no camera/mic/hardware needed)
workspace/               Runtime data (memory DB, spine, models, mood, chat logs)
soul.md                  Her persona & standing directives — loaded on boot
requirements.txt         Python dependencies
windows/                 Auto-start installer + watchdog (.bat)
tools/                   Helper scripts (key manager)
```

## Version history

### Since v9.36

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
- Tested on Windows 11 with Python 3.12.
- The v9.17 changelog entry ("Google cloud transcription by default, local
  faster-whisper dormant behind `_USE_LOCAL_STT`") was never implemented —
  no such flag exists in the code. Current behavior: the desktop mic prefers
  local faster-whisper whenever it is installed, falling back to Google cloud
  transcription, and the bridge's voice messages are transcribed by Gemini.
