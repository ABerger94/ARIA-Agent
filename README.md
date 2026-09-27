# A.R.I.A. — Adaptive Robotic Intelligence Agent

A Python desktop AI companion. She lives on your Windows laptop as an animated
face in an OpenCV HUD: she talks (natural Edge TTS voice), listens (mic +
speech recognition), remembers, and acts through a Gemini-powered agent loop
with 50+ tools.

Built by Alek Berger. Not a framework, not a demo — a finished companion.

## Features

- **Animated HUD face** (OpenCV) — idle, listening, speaking, thinking states,
  waveform mouth, expressive eyes
- **Voice in / voice out** — Edge TTS neural voice with pyttsx3 fallback;
  mic input with local Whisper transcription (v9.14, Google fallback);
  hold-SPACE push-to-talk (v9.11); wake word
- **Agent brain** — Gemini function-calling loop (no turn cap; 10-minute
  budget per request — on timeout she asks "Should I keep going?" and
  "yes"/"continue" resumes the same reasoning chain)
- **Progressive tool loading** (v9.8) — only 16 core tool schemas go to the
  model per call; specialist toolkits (Gmail, Spotify, scheduler, GitHub,
  vision/hardware, Windows control, MTG, memory/notes, admin) unlock on demand
  via `load_toolkit`
- **Duplicate-call blocking + argument repair** — the same tool with the same
  arguments never runs twice in one request; missing arguments get a repair
  nudge instead of a crash
- **Durable memory** — SQLite + Markdown journal with semantic recall;
  unbroken memory spine (v9.10): append-only log of turns, tool calls, and
  journal entries, last session's context reloads at boot
- **Scheduler** — one-shot and recurring reminders/jobs
- **51 tools** — Spotify control, Gmail send/read, GitHub push, Windows
  control (open apps/URLs, type, click, media keys), file search, volume,
  MTG advice, notes, timers, and more
- **Proactive heartbeat** — she can speak up on her own when something matters
- **Tactical chat overlay** — `C` opens, `J`/`K` scroll history
- **Phone bridge** (HTTPS) — talk to her from your iPhone, with a live
  face-only video stream (v9.12)
- **USB-serial Arduino bridge** — hook for a future physical robot body

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

## Auto-start on boot

1. Copy `windows\aria_autostart.bat` and `windows\aria_watchdog.bat` to the
   same folder as `aria.py`
2. Double-click `aria_autostart.bat` once and enter your `aria.py` path
   (e.g. `E:\ARIA\aria.py`)
3. It installs the watchdog into your Windows Startup folder — ARIA launches
   on every boot and restarts automatically if she crashes

## Project layout

```
aria.py                  Main program (v9.23)
soul.md                  Her persona — loaded into mind on every start
requirements.txt         Python dependencies
windows/                 Auto-start installer + watchdog (.bat)
tools/                   Helper scripts (key manager)
```

## Version history

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
