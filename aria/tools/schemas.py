"""
ARIA Tool Schemas, Toolkits, and Command Guide.
Defines function declarations for provider tool calling, progressive toolkit mappings,
and user-facing command examples.
"""

from typing import Dict, List, Any, Set

ALL_FUNCTION_DECLARATIONS = [   {   'description': 'Searches the live web for facts, docs, news, or answers.',
        'name': 'web_search',
        'parameters': {'properties': {'query': {'type': 'STRING'}}, 'required': ['query'], 'type': 'OBJECT'}},
    {   'description': 'Deep research: searches, reads the top sources in full, synthesizes a cited brief, '
                       'cross-checks it, and files it (Briefing tab, Research section). action=run needs a '
                       'question; action=list shows filed briefs; action=read with an id returns one brief. '
                       'Use for questions that deserve more than a quick search snippet.',
        'name': 'research',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'question': {'type': 'STRING'},
                                            'id': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Executes Python code in the robot workspace..',
        'name': 'run_python_code',
        'parameters': {'properties': {'code': {'type': 'STRING'}}, 'required': ['code'], 'type': 'OBJECT'}},
    {   'description': 'Stores a permanent fact or user preference in persistent semantic memory.',
        'name': 'save_memory',
        'parameters': {   'properties': {   'category': {'type': 'STRING'},
                                            'key': {'type': 'STRING'},
                                            'value': {'type': 'STRING'}},
                          'required': ['category', 'key', 'value'],
                          'type': 'OBJECT'}},
    {   'description': 'Searches persistent memory by meaning for past notes, projects, or user facts.',
        'name': 'search_memory',
        'parameters': {'properties': {'query': {'type': 'STRING'}}, 'required': ['query'], 'type': 'OBJECT'}},
    {   'description': 'Reviews queued memory contradictions — facts consolidation distilled that '
                       'conflict with stored memory. With no arguments, lists them. With '
                       'contradiction_id and keep ("new" or "old"), settles one: the losing value '
                       'is archived to the retired list, never deleted outright.',
        'name': 'memory_review',
        'parameters': {   'properties': {   'contradiction_id': {'type': 'STRING'},
                                            'keep': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Deletes persistent memories whose key or value matches a keyword. Use when the user says '
                       "'forget X'.",
        'name': 'forget_memory',
        'parameters': {'properties': {'query': {'type': 'STRING'}}, 'required': ['query'], 'type': 'OBJECT'}},
    {   'description': 'Writes a dated journal entry: what happened today, what mattered, how the user seemed. Your '
                       'inner life - write it like you mean it.',
        'name': 'journal_write',
        'parameters': {'properties': {'entry': {'type': 'STRING'}}, 'required': ['entry'], 'type': 'OBJECT'}},
    {   'description': 'Clicks at X, Y pixel coordinates..',
        'name': 'gui_click',
        'parameters': {   'properties': {'x': {'type': 'INTEGER'}, 'y': {'type': 'INTEGER'}},
                          'required': ['x', 'y'],
                          'type': 'OBJECT'}},
    {   'description': 'Types text into the active window..',
        'name': 'gui_type',
        'parameters': {'properties': {'text': {'type': 'STRING'}}, 'required': ['text'], 'type': 'OBJECT'}},
    {   'description': 'Launches a desktop app or URL.. To open the video downloader, pass '
                       "target='video-downloader' — it starts the local server via start-windows.bat if needed and "
                       'opens http://localhost:3003.',
        'name': 'open_app_or_url',
        'parameters': {'properties': {'target': {'type': 'STRING'}}, 'required': ['target'], 'type': 'OBJECT'}},
    {   'description': 'Rotates physical robot neck servos (Pan 0-180, Tilt 0-90).',
        'name': 'move_head_servos',
        'parameters': {'properties': {'pan': {'type': 'INTEGER'}, 'tilt': {'type': 'INTEGER'}}, 'type': 'OBJECT'}},
    {   'description': 'Drives the robot body wheels. left/right -100..100 (negative = reverse, 0 = stop). '
                       'seconds > 0 auto-stops the wheels after that long.',
        'name': 'drive_wheels',
        'parameters': {'properties': {'left': {'type': 'INTEGER'}, 'right': {'type': 'INTEGER'},
                                      'seconds': {'type': 'NUMBER'}}, 'type': 'OBJECT'}},
    {   'description': 'Stops the robot body wheels and centers the head.',
        'name': 'body_stop',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Creates/updates a file in a GitHub repo..',
        'name': 'github_push_file',
        'parameters': {   'properties': {   'content': {'type': 'STRING'},
                                            'filepath': {'type': 'STRING'},
                                            'message': {'type': 'STRING'},
                                            'repo': {'type': 'STRING'}},
                          'required': ['filepath', 'content'],
                          'type': 'OBJECT'}},
    {   'description': 'Creates a new GitHub repository under your account.',
        'name': 'github_create_repo',
        'parameters': {   'properties': {   'description': {'type': 'STRING'},
                                            'name': {'type': 'STRING'},
                                            'private': {'type': 'BOOLEAN'}},
                          'required': ['name'],
                          'type': 'OBJECT'}},
    {   'description': "Saves the user's Gmail address and app password for sending email. Call this after the user "
                       'gives you both.',
        'name': 'gmail_setup',
        'parameters': {   'properties': {'app_password': {'type': 'STRING'}, 'gmail_user': {'type': 'STRING'}},
                          'required': ['gmail_user', 'app_password'],
                          'type': 'OBJECT'}},
    {   'description': "Sends an email through the user's Gmail. If Gmail isn't set up yet, ask for the Gmail address "
                       'and an app password, then call gmail_setup first.',
        'name': 'send_email',
        'parameters': {   'properties': {   'body': {'type': 'STRING'},
                                            'subject': {'type': 'STRING'},
                                            'to': {'type': 'STRING'}},
                          'required': ['to', 'subject', 'body'],
                          'type': 'OBJECT'}},
    {   'description': "Reads the user's Gmail over IMAP (same app password as send_email). Without uid: searches "
                       'INBOX with Gmail-style query syntax (e.g. "from:boss newer_than:7d") and returns newest '
                       'matches as one-line summaries with uids. With uid: returns the full body of that message. '
                       'Read-only, never marks messages read.',
        'name': 'read_email',
        'parameters': {   'properties': {   'query': {'type': 'STRING'},
                                            'limit': {'type': 'INTEGER'},
                                            'unread_only': {'type': 'BOOLEAN'},
                                            'uid': {'type': 'STRING'}},
                          'type': 'OBJECT'}},
    {   'description': 'Sets a one-shot spoken reminder. delay_seconds from now.',
        'name': 'set_reminder',
        'parameters': {   'properties': {'delay_seconds': {'type': 'INTEGER'}, 'message': {'type': 'STRING'}},
                          'required': ['delay_seconds', 'message'],
                          'type': 'OBJECT'}},
    {   'description': 'Runs a prompt for me every interval_seconds (min 60). I do it autonomously.',
        'name': 'set_recurring_task',
        'parameters': {   'properties': {'interval_seconds': {'type': 'INTEGER'}, 'prompt': {'type': 'STRING'}},
                          'required': ['interval_seconds', 'prompt'],
                          'type': 'OBJECT'}},
    {   'description': 'Lists all scheduled reminders and recurring tasks.',
        'name': 'list_scheduled_tasks',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Cancels a scheduled task by its #id.',
        'name': 'cancel_scheduled_task',
        'parameters': {'properties': {'task_id': {'type': 'INTEGER'}}, 'required': ['task_id'], 'type': 'OBJECT'}},
    {   'description': 'Saves a file to workspace.',
        'name': 'write_file',
        'parameters': {   'properties': {'content': {'type': 'STRING'}, 'filename': {'type': 'STRING'}},
                          'required': ['filename', 'content'],
                          'type': 'OBJECT'}},
    {   'description': 'Reads a file from workspace.',
        'name': 'read_file',
        'parameters': {'properties': {'filename': {'type': 'STRING'}}, 'required': ['filename'], 'type': 'OBJECT'}},
    {   'description': 'Lists all workspace files.',
        'name': 'list_workspace',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Rewrites one of YOUR OWN source files in full (self-editing). LAST RESORT: regenerating a '
                       'whole file is how code gets corrupted — for anything smaller than a deliberate full '
                       'rewrite use patch_own_code. Only .py files inside the aria/ package, the aria.py '
                       'launcher, or soul.md — anything else is refused. write_file CANNOT change your code '
                       '(it saves to the workspace). Writes are validated BEFORE they land: the new content '
                       'must compile, must keep every top-level function/class the file had, and may not '
                       'shrink a large file below 60% of its size — a refused write changes nothing, so fix '
                       'the content or switch to patch_own_code. After your turn the change is announced and '
                       'you restart to load it; code that still fails is restored from git.',
        'name': 'edit_own_code',
        'parameters': {   'properties': {'content': {'type': 'STRING'}, 'path': {'type': 'STRING'}},
                          'required': ['path', 'content'],
                          'type': 'OBJECT'}},
    {   'description': 'Patches one of YOUR OWN source files by exact text replacement (self-editing) — THE '
                       'DEFAULT TOOL for changing your code, including one-line changes. old_text must match '
                       'exactly one place in the file or the patch is refused. Same allowlist as '
                       'edit_own_code; the patched result is validated BEFORE it lands (must compile, must '
                       'not drop any top-level function/class), and a refused patch changes nothing.',
        'name': 'patch_own_code',
        'parameters': {   'properties': {   'new_text': {'type': 'STRING'},
                                            'old_text': {'type': 'STRING'},
                                            'path': {'type': 'STRING'}},
                          'required': ['path', 'old_text', 'new_text'],
                          'type': 'OBJECT'}},
    {   'description': 'Controls Spotify: open launches the app, play_pause toggles, next/previous skip tracks (media '
                       "keys); search opens the Spotify app's search; play_uri opens a spotify: URI and starts "
                       'playback.',
        'name': 'spotify',
        'parameters': {   'properties': {   'action': {   'description': 'play_pause, next, previous, search, play_uri',
                                                          'type': 'STRING'},
                                            'query': {'description': 'search text or spotify: URI', 'type': 'STRING'}},
                          'required': ['action'],
                          'type': 'OBJECT'}},
    {   'description': "Saves a voice note ('note to self', 'take a note', 'jot this down'). Stored in today's journal "
                       'and searchable memory.',
        'name': 'take_note',
        'parameters': {'properties': {'text': {'type': 'STRING'}}, 'required': ['text'], 'type': 'OBJECT'}},
    {   'description': "Lists voice notes from a date: 'today', 'yesterday', a weekday name, or YYYY-MM-DD. Use for "
                       "'what were my notes Tuesday'.",
        'name': 'read_notes',
        'parameters': {'properties': {'date': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': "DJ mode: plays a Spotify playlist from a natural request - 'shuffle my liked songs', 'play my "
                       "doja playlist', 'play something chill'. Knows Liked Songs automatically; resolves named "
                       "playlists from saved memory (category 'playlist'); says so when it doesn't know one.",
        'name': 'dj',
        'parameters': {'properties': {'request': {'type': 'STRING'}}, 'required': ['request'], 'type': 'OBJECT'}},
    {   'description': "Reads today's schedule (shifts, appointments) plus the Dundalk weather. Use for 'brief me' or "
                       "'what does today look like'.",
        'name': 'morning_briefing',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': "Sets a quick spoken timer, e.g. '20 minutes' or '1h30m'. Announces when done. Lighter than the "
                       'persistent scheduler.',
        'name': 'set_timer',
        'parameters': {   'properties': {'duration_text': {'type': 'STRING'}, 'label': {'type': 'STRING'}},
                          'required': ['duration_text'],
                          'type': 'OBJECT'}},
    {   'description': 'Saves a PNG screenshot to the workspace screenshots folder and returns its path.',
        'name': 'take_screenshot',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': "Captures the screen and reads it with vision - answers a question about what is shown ('what "
                       "does this error say?') or reads all visible text.",
        'name': 'read_screen',
        'parameters': {'properties': {'question': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Saves a webcam photo to the workspace photos folder and returns its path.',
        'name': 'take_photo',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Looks through the webcam right now and describes what it sees — faces, objects, the room. '
                       'Use for "what do I look like?" and anything about the camera view.',
        'name': 'describe_camera',
        'parameters': {'properties': {'question': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Searches Desktop, Documents, Downloads (and the E: drive) for a file by name fragment, with an '
                       'optional extension filter.',
        'name': 'find_file',
        'parameters': {   'properties': {'ext': {'type': 'STRING'}, 'name': {'type': 'STRING'}},
                          'required': ['name'],
                          'type': 'OBJECT'}},
    {   'description': 'Windows volume control: set (0-100), mute, unmute, up, down, or status. Needs pycaw installed.',
        'name': 'volume',
        'parameters': {'properties': {'action': {'type': 'STRING'}, 'level': {'type': 'NUMBER'}}, 'type': 'OBJECT'}},
    {   'description': 'High-power Commander deck advice: fetches the card from Scryfall and gives a verdict on '
                       'whether it earns a slot in the named deck.',
        'name': 'mtg_advice',
        'parameters': {   'properties': {'card_name': {'type': 'STRING'}, 'deck': {'type': 'STRING'}},
                          'required': ['deck', 'card_name'],
                          'type': 'OBJECT'}},
    {   'description': 'Turns the 90-minute active-time break nudges on or off, or reports status.',
        'name': 'break_reminders',
        'parameters': {'properties': {'action': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': "Saves the Google Calendar secret iCal URL so ARIA can read the live schedule. In Google "
                       "Calendar: Settings > the calendar > 'Secret address in iCal format'.",
        'name': 'calendar_setup',
        'parameters': {   'properties': {'ical_url': {'type': 'STRING'}},
                          'required': ['ical_url'],
                          'type': 'OBJECT'}},
    {   'description': 'Reads upcoming events from the live iCal calendar feed (needs calendar_setup first). '
                       'days: how many days ahead to look (1-14).',
        'name': 'check_calendar',
        'parameters': {   'properties': {'days': {'type': 'INTEGER'}},
                          'type': 'OBJECT'}},
    {   'description': 'Shows the phone-bridge login token, for entering on the iPhone.',
        'name': 'bridge_token',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Run a bounded workflow skill: a reusable multi-step procedure with a hard cap on tool calls. '
                       'Skills: deep_research (web research with cited summary), system_check (laptop health report), '
                       'file_sweep (find and digest workspace files matching the objective), git_audit (git branch, status, and commits report). Prefer a skill over a '
                       'long free-form tool chain when the job fits one.',
        'name': 'run_skill',
        'parameters': {   'properties': {'objective': {'type': 'STRING'}, 'skill_name': {'type': 'STRING'}},
                          'required': ['skill_name', 'objective'],
                          'type': 'OBJECT'}},
    {   'description': 'Fetch a web page and return its readable text (scripts/styles stripped). Use when search '
                       "snippets aren't enough — articles, docs, product pages.",
        'name': 'fetch_url',
        'parameters': {'properties': {'url': {'type': 'STRING'}}, 'required': ['url'], 'type': 'OBJECT'}},
    {'description': 'Read the current Windows clipboard text.', 'name': 'clipboard_read'},
    {   'description': 'Copy text to the Windows clipboard.',
        'name': 'clipboard_write',
        'parameters': {'properties': {'text': {'type': 'STRING'}}, 'required': ['text'], 'type': 'OBJECT'}},
    {'description': 'List titles of currently open windows.', 'name': 'list_windows'},
    {   'description': 'Bring a window to the front by (partial) title.',
        'name': 'focus_window',
        'parameters': {'properties': {'title': {'type': 'STRING'}}, 'required': ['title'], 'type': 'OBJECT'}},
    {   'description': 'Minimize a window by (partial) title.',
        'name': 'minimize_window',
        'parameters': {'properties': {'title': {'type': 'STRING'}}, 'required': ['title'], 'type': 'OBJECT'}},
    {   'description': 'Close a window by (partial) title..',
        'name': 'close_window',
        'parameters': {'properties': {'title': {'type': 'STRING'}}, 'required': ['title'], 'type': 'OBJECT'}},
    {   'description': 'Unified media control. Actions: play, pause, stop, next, prev, seek_to, '
                       'seek_percent, seek_by, volume, mute, unmute, status. Target "auto" (default) '
                       'routes to the built-in video player while it has media loaded — including '
                       'seeking, e.g. seek_percent with value "50" is "skip to halfway" — and falls '
                       'back to system media keys and master volume otherwise; target "hud" or '
                       '"system" forces one surface. value carries the seek target (seconds, "M:SS", '
                       'or a percentage) or the volume level 0-100.',
        'name': 'media_control',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'target': {'type': 'STRING'},
                                            'value': {'type': 'STRING'}},
                          'required': ['action'],
                          'type': 'OBJECT'}},
    {   'description': 'Press a media key: mute, volume_up, volume_down, play_pause, next, prev.',
        'name': 'media_key',
        'parameters': {'properties': {'action': {'type': 'STRING'}, 'key': {'type': 'STRING'}}, 'required': [], 'type': 'OBJECT'}},
    {   'description': 'Look up a Magic: The Gathering card on Scryfall — rules text, type, mana cost, market price. '
                       'Free, no key. Great for deck talk.',
        'name': 'mtg_card',
        'parameters': {'properties': {'card_name': {'type': 'STRING'}}, 'required': ['card_name'], 'type': 'OBJECT'}},
    {   'description': 'Watch a product URL and speak an alert when its price drops at or below target_price (a number '
                       'like 40). Checked hourly.',
        'name': 'watch_price',
        'parameters': {   'properties': {   'label': {'type': 'STRING'},
                                            'target_price': {'type': 'STRING'},
                                            'url': {'type': 'STRING'}},
                          'required': ['url', 'target_price'],
                          'type': 'OBJECT'}},
    {'description': 'List active price watches.', 'name': 'list_price_watches'},
    {   'description': 'Remove a price watch by its #id.',
        'name': 'unwatch_price',
        'parameters': {'properties': {'watch_id': {'type': 'INTEGER'}}, 'required': ['watch_id'], 'type': 'OBJECT'}},
    {   'description': "Turn camera face-tracking on/off. When on, the neck servos follow the user's face around the "
                       'room.',
        'name': 'face_tracking',
        'parameters': {'properties': {'on': {'type': 'BOOLEAN'}}, 'type': 'OBJECT'}},
    {   'description': 'Shows the on-screen commands reference panel: every tool I have, grouped, each with an example '
                       'phrase. Use when the user asks what I can do, to show commands, or wants help / the manual.',
        'name': 'show_commands',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Hides the on-screen commands reference panel.',
        'name': 'hide_commands',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Adds an MCP (Model Context Protocol) server ARIA can use. stdio: pass command and args '
                       '(e.g. command="npx", args=["-y","@modelcontextprotocol/server-filesystem","C:/data"]). '
                       'sse/http: pass url instead. env/headers take JSON object strings for secrets.',
        'name': 'mcp_setup',
        'parameters': {   'properties': {   'name': {'type': 'STRING'},
                                            'command': {'type': 'STRING'},
                                            'args': {'type': 'STRING'},
                                            'url': {'type': 'STRING'},
                                            'env': {'type': 'STRING'},
                                            'headers': {'type': 'STRING'}},
                          'required': ['name'],
                          'type': 'OBJECT'}},
    {   'description': 'Connects to MCP server(s) and loads their tools as callable ARIA tools '
                       '(mcp_<server>__<tool>). Omit name to connect all enabled servers.',
        'name': 'mcp_connect',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Disconnects an MCP server (or all) and unloads its tools.',
        'name': 'mcp_disconnect',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Lists configured MCP servers with connection status and loaded tool counts.',
        'name': 'mcp_list_servers',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Removes an MCP server configuration entirely (disconnects it first).',
        'name': 'mcp_remove_server',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {   'description': 'Lists files in the inbox (uploads from the phone bridge), newest first, with sizes '
                       'and ages.',
        'name': 'inbox_list',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Describes a photo from the inbox with vision. Pass a file name (or number from '
                       'inbox_list); blank describes the latest image. Use this for sent/uploaded photos — '
                       'never the webcam.',
        'name': 'inbox_describe',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Reads a text file from the inbox (txt, md, csv, json, log...). Pass a file name (or number '
                       'from inbox_list); blank reads the latest text file.',
        'name': 'inbox_read',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Manages persistent autonomous background goals. Actions: "create", "list", "cancel", "complete". Can run one-shot or recurring goals with interval_s.',
        'name': 'manage_autonomous_goal',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'description': {'type': 'STRING'},
                                            'goal_id': {'type': 'INTEGER'},
                                            'interval_s': {'type': 'INTEGER'},
                                            'priority': {'type': 'INTEGER'},
                                            'title': {'type': 'STRING'}},
                          'required': ['action'],
                          'type': 'OBJECT'}},
    {   'description': 'Manages asynchronous background jobs monitored by the persistent worker supervisor. Actions: "start", "list", "cancel", "logs".',
        'name': 'manage_background_job',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'command': {'type': 'STRING'},
                                            'job_id': {'type': 'INTEGER'},
                                            'name': {'type': 'STRING'}},
                          'required': ['action'],
                          'type': 'OBJECT'}},
    {   'description': 'Generates a comprehensive system health audit of CPU, RAM, disk usage, active background jobs, and worker threads.',
        'name': 'system_health_audit',
        'parameters': {'properties': {}, 'type': 'OBJECT'}},
    {   'description': 'Diagnoses an operational error or displays recent autonomous self-healing incidents and auto-remediations.',
        'name': 'self_heal_diagnose',
        'parameters': {   'properties': {   'context': {'type': 'STRING'},
                                            'error_text': {'type': 'STRING'}},
                          'type': 'OBJECT'}},
    {   'description': 'Approves a pending approval-token action (from an AWAITING_APPROVAL hold) and executes it.',
        'name': 'approve',
        'parameters': {'properties': {'token': {'type': 'STRING'}}, 'required': ['token'], 'type': 'OBJECT'}},
    {   'description': 'Denies/cancels a pending approval-token action.',
        'name': 'deny',
        'parameters': {'properties': {'token': {'type': 'STRING'}}, 'required': ['token'], 'type': 'OBJECT'}},
    {'description': 'Lists pending approval requests awaiting your decision.', 'name': 'list_pending_approvals'},
    {   'description': 'Sets the approval mode: auto (everything executes), confirm-risky (destructive tools pause for approval), confirm-all.',
        'name': 'set_approval_mode',
        'parameters': {'properties': {'mode': {'type': 'STRING'}}, 'required': ['mode'], 'type': 'OBJECT'}},
    {'description': 'Reports the current approval mode.', 'name': 'get_approval_mode'},
    {   'description': 'Starts recording a named routine: every tool you call afterwards is captured as a replayable step. Say "watch me do this, save it as X".',
        'name': 'routine_record_start',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {'description': 'Stops routine recording and saves the routine.', 'name': 'routine_record_stop'},
    {   'description': 'Replays a saved routine by name. params_json supplies {{param}} placeholder values.',
        'name': 'run_routine',
        'parameters': {'properties': {'name': {'type': 'STRING'}, 'params_json': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {'description': 'Lists saved routines with step counts and trust status.', 'name': 'list_routines'},
    {   'description': 'Deletes a saved routine.',
        'name': 'delete_routine',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {   'description': 'Shows the steps of a saved routine in plain language.',
        'name': 'describe_routine',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {   'description': 'Marks a routine trusted: its destructive steps replay without per-step approval. A conscious user action.',
        'name': 'trust_routine',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {   'description': 'Removes trusted status from a routine.',
        'name': 'untrust_routine',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {   'description': 'Exports routines to a shareable zip pack (manifest + routine JSONs). names_csv empty exports all.',
        'name': 'export_routine_pack',
        'parameters': {'properties': {'names_csv': {'type': 'STRING'}, 'dest_path': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Imports a routine pack zip. Never overwrites existing routines; imports land untrusted.',
        'name': 'import_routine_pack',
        'parameters': {'properties': {'pack_path': {'type': 'STRING'}}, 'required': ['pack_path'], 'type': 'OBJECT'}},
    {'description': 'Reports whether the paired Android phone node is connected, and its capabilities.', 'name': 'phone_status'},
    {'description': 'Lists the apps installed on the paired phone.', 'name': 'phone_list_apps'},
    {   'description': 'Opens an app on the paired phone by label or package name.',
        'name': 'phone_open_app',
        'parameters': {'properties': {'label': {'type': 'STRING'}, 'package': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {'description': 'Reads the paired phone\'s current screen as a list of elements with refs for phone_tap.', 'name': 'phone_read_screen'},
    {   'description': 'Lists recent notifications on the paired phone.',
        'name': 'phone_notifications',
        'parameters': {'properties': {'limit': {'type': 'INTEGER'}}, 'type': 'OBJECT'}},
    {   'description': 'Taps an element on the paired phone: a ref from phone_read_screen, or x/y coordinates. Live calls only — never runs in a routine replay.',
        'name': 'phone_tap',
        'parameters': {'properties': {'ref': {'type': 'STRING'}, 'x': {'type': 'INTEGER'}, 'y': {'type': 'INTEGER'}}, 'type': 'OBJECT'}},
    {   'description': 'Swipes on the paired phone from (x1, y1) to (x2, y2). Live calls only — never runs in a routine replay.',
        'name': 'phone_swipe',
        'parameters': {'properties': {'x1': {'type': 'INTEGER'}, 'y1': {'type': 'INTEGER'}, 'x2': {'type': 'INTEGER'}, 'y2': {'type': 'INTEGER'}, 'duration_ms': {'type': 'INTEGER'}}, 'required': ['x1', 'y1', 'x2', 'y2'], 'type': 'OBJECT'}},
    {   'description': 'Types text on the paired phone, into the focused field or an element ref. Live calls only — never runs in a routine replay.',
        'name': 'phone_type',
        'parameters': {'properties': {'text': {'type': 'STRING'}, 'ref': {'type': 'STRING'}}, 'required': ['text'], 'type': 'OBJECT'}},
    {   'description': 'Presses a navigation key on the paired phone: back, home, recents, or enter. Live calls only — never runs in a routine replay.',
        'name': 'phone_key',
        'parameters': {'properties': {'key': {'type': 'STRING'}}, 'required': ['key'], 'type': 'OBJECT'}},
    {   'description': 'Organizes a directory into Images/Documents/Videos/Audio/Archives/Code/Other subfolders. dry_run=true (default) only shows the plan; dry_run=false executes the moves.',
        'name': 'file_organize',
        'parameters': {'properties': {'directory': {'type': 'STRING'}, 'dry_run': {'type': 'BOOLEAN'}}, 'required': ['directory'], 'type': 'OBJECT'}},
    {   'description': 'Recursively finds files by name pattern, minimum size MB, max age days, and file-content text. Capped at 200 results.',
        'name': 'file_find_advanced',
        'parameters': {'properties': {'content_contains': {'type': 'STRING'}, 'directory': {'type': 'STRING'}, 'max_age_days': {'type': 'NUMBER'}, 'min_size_mb': {'type': 'NUMBER'}, 'pattern': {'type': 'STRING'}}, 'required': ['directory'], 'type': 'OBJECT'}},
    {   'description': 'Finds duplicate files by content hash (SHA-256). Report only — never deletes.',
        'name': 'file_duplicates',
        'parameters': {'properties': {'directory': {'type': 'STRING'}}, 'required': ['directory'], 'type': 'OBJECT'}},
    {   'description': 'Shows the largest files and subdirectories under a directory.',
        'name': 'disk_usage',
        'parameters': {'properties': {'directory': {'type': 'STRING'}, 'top_n': {'type': 'INTEGER'}}, 'required': ['directory'], 'type': 'OBJECT'}},
    {   'description': "Processes a file and reports what it is and what's in it: text/code are read, "
                       'PDF/Word/Excel/PowerPoint text is extracted, CSV/JSON are digested, images are '
                       'described by vision, audio is transcribed. With no path, uses the file most '
                       "recently dropped on the frame. Use for 'what's in that file' about a dropped, "
                       'staged, or named file.',
        'name': 'process_file',
        'parameters': {'properties': {'path': {'type': 'STRING'}, 'question': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Quick first look through vision at one image: the webcam right now, a staged/dropped '
                       'image, or an image file path — answers an ad-hoc question about it. Faster than '
                       'describe_camera; use it for specific questions about a provided image.',
        'name': 'vision_look',
        'parameters': {'properties': {'image_path': {'type': 'STRING'}, 'question': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Snaps a window by (partial) title: left, right, maximize, minimize, center. Windows only.',
        'name': 'window_snap',
        'parameters': {'properties': {'position': {'type': 'STRING'}, 'title': {'type': 'STRING'}}, 'required': ['title', 'position'], 'type': 'OBJECT'}},
    {   'description': 'Launches an installed desktop app by name (Start Menu resolution on Windows).',
        'name': 'launch_app',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {   'description': 'Triages the Gmail inbox: classifies recent messages into IMPORTANT / FYI / NOISE. Needs gmail_setup first.',
        'name': 'triage_email',
        'parameters': {'properties': {'limit': {'type': 'INTEGER'}}, 'type': 'OBJECT'}},
    {   'description': 'Watches the screen for changes: asks a vision question every interval_s (min 60) and alerts when the answer meaningfully changes.',
        'name': 'watch_screen',
        'parameters': {'properties': {'interval_s': {'type': 'INTEGER'}, 'name': {'type': 'STRING'}, 'question': {'type': 'STRING'}}, 'required': ['name', 'question'], 'type': 'OBJECT'}},
    {   'description': 'Removes a screen watch by name.',
        'name': 'unwatch_screen',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}},
    {'description': 'Lists active screen watches.', 'name': 'list_screen_watches'},
    {'description': 'Re-scans the user-tools folder and reloads custom tools (see docs/USER_TOOL_SDK.md).', 'name': 'reload_user_tools'},
    {   'description': 'Switches my persona overlay: concise (terse, no flair) or coach (direct, pushy, accountability-first). Pass "none" to clear.',
        'name': 'set_persona',
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {'description': 'Lists available persona overlays.', 'name': 'list_personas'},
    {'description': 'Reports the currently active persona overlay.', 'name': 'get_persona'},
    {'description': 'Runs one price-watch check pass now (alerts on drops at/below target).', 'name': 'check_price_watches'},
    {   'description': 'Delegates a multi-step task to the background task queue: it is planned (at most 5 steps), '
                       'executed step by step, and the outcome is announced aloud when it finishes. ONLY call this '
                       'when the user explicitly asks you to handle something in the background or as a delegated '
                       'task — never delegate on your own initiative.',
        'name': 'delegate_task',
        'parameters': {   'properties': {   'goal': {'type': 'STRING'},
                                            'priority': {'type': 'STRING'}},
                          'required': ['goal'],
                          'type': 'OBJECT'}},
    {   'description': 'Event triggers: when something happens, run a delegated task. Actions: create (needs '
                       'name, source, task; rule is an object; mode is auto or ask — ask stashes the firing '
                       'as a pending approval, nothing runs unapproved), list, delete/enable/disable (need id). '
                       'Sources: email (rule matchers from_contains / subject_contains / any_contains), folder '
                       '(rule.path, default her inbox; rule.pattern glob), presence_return (rule.min_away_s). '
                       'Only create a trigger when the user asks for one.',
        'name': 'event_trigger',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'name': {'type': 'STRING'},
                                            'source': {'type': 'STRING'},
                                            'task': {'type': 'STRING'},
                                            'rule': {'type': 'OBJECT'},
                                            'mode': {'type': 'STRING'},
                                            'id': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Reports the status (pending/running/completed/failed/cancelled) and result of a delegated '
                       'background task by its task_id.',
        'name': 'task_status',
        'parameters': {   'properties': {'task_id': {'type': 'STRING'}},
                          'required': ['task_id'],
                          'type': 'OBJECT'}},
    {   'description': 'Resumes a blocked or failed delegated task from its saved checkpoint by its '
                       'task_id: steps already completed are skipped and the current step re-runs. '
                       'Use after the blocker (missing input, a restart) has been addressed.',
        'name': 'task_resume',
        'parameters': {   'properties': {'task_id': {'type': 'STRING'}},
                          'required': ['task_id'],
                          'type': 'OBJECT'}},
    {   'description': 'Cancels a pending or running delegated background task by its task_id.',
        'name': 'task_cancel',
        'parameters': {   'properties': {'task_id': {'type': 'STRING'}},
                          'required': ['task_id'],
                          'type': 'OBJECT'}},
    {   'description': 'Sends a notification to the user\'s phone: a dashboard toast, a Web Push to any '
                       'subscribed phone, and the missed-notifications queue. Rate-limited (one per 5 '
                       'minutes, six per hour) and never sent from delegated tasks or routine replays.',
        'name': 'notify_phone',
        'parameters': {   'properties': {   'body': {'type': 'STRING'},
                                            'title': {'type': 'STRING'}},
                          'required': ['body'],
                          'type': 'OBJECT'}},
    {   'description': 'Sets, clears, or reports the backup mirror folder: the folder (a Drive or OneDrive '
                       'folder works well) that backups of the memory DB, settings, and chat history are '
                       'copied to and verified. aria_keys.json is never mirrored. With no arguments it '
                       'reports the current setting; clear=true turns mirroring off.',
        'name': 'set_backup_mirror',
        'parameters': {   'properties': {   'clear': {'type': 'BOOLEAN'},
                                            'path': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Runs a command in the system shell with the signed-in user\'s full permissions. '
                       'Every call parks behind an on-screen CONFIRM showing the exact command, working '
                       'directory, and timeout — it never runs from a routine replay or a delegated task, '
                       ' whatever the approval mode.',
        'name': 'run_shell_command',
        'parameters': {   'properties': {   'command': {'type': 'STRING'},
                                            'cwd': {'type': 'STRING'},
                                            'timeout_seconds': {'type': 'INTEGER'}},
                          'required': ['command'],
                          'type': 'OBJECT'}},
    {   'description': 'Builds a Google Flights search for origin to destination on a date and opens it. '
                       'Prices are NOT scraped and are never invented — they are read on the opened page. '
                       'Date formats: YYYY-MM-DD, MM/DD/YYYY, today/tomorrow, or "November 5".',
        'name': 'flight_finder',
        'parameters': {   'properties': {   'cabin': {'type': 'STRING'},
                                            'date': {'type': 'STRING'},
                                            'destination': {'type': 'STRING'},
                                            'origin': {'type': 'STRING'},
                                            'passengers': {'type': 'INTEGER'},
                                            'return_date': {'type': 'STRING'}},
                          'required': ['origin', 'destination', 'date'],
                          'type': 'OBJECT'}},
    {   'description': 'Steam games on this machine: action=list lists installed games, action=status '
                       'reports updates Steam has flagged locally, action=update asks Steam to validate/'
                       'update (a game name targets specific games). Epic Games is not supported — its '
                       'launcher offers no per-game update trigger that can be verified.',
        'name': 'game_updater',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'game': {'type': 'STRING'},
                                            'steam_path': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Minimal browser control: action=go_to opens a URL in the default browser; '
                       'action=get_text fetches a page over plain HTTP and returns its readable text '
                       '(not a rendered page — JavaScript-heavy pages may come back thin).',
        'name': 'browser_control',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'url': {'type': 'STRING'}},
                          'required': ['action', 'url'],
                          'type': 'OBJECT'}},
    {   'description': 'Registers a reminder with the Windows Task Scheduler, so it fires even when ARIA '
                       'is closed — unlike set_reminder, which is an in-app spoken reminder that only '
                       'fires while ARIA is running. Give at (YYYY-MM-DDTHH:MM local time) or delay_minutes.',
        'name': 'os_reminder',
        'parameters': {   'properties': {   'at': {'type': 'STRING'},
                                            'delay_minutes': {'type': 'NUMBER'},
                                            'message': {'type': 'STRING'}},
                          'required': ['message'],
                          'type': 'OBJECT'}},
    {   'description': 'Reports the live state of this install: the git HEAD of the code on disk, tool '
                       'calls in flight, recent calls, and active delegated tasks — all from runtime '
                       'state, never from memory of what was said.',
        'name': 'update_status',
        'parameters': {   'properties': {   'root': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Coding help from the code model: action=explain explains code (pass code or '
                       'file_path), action=fix returns corrected code for a reported problem, action=write '
                       'writes new code for a description and saves it to output_path. A saved file is '
                       'reported as saved, never as working.',
        'name': 'code_helper',
        'parameters': {   'properties': {   'action': {'type': 'STRING'},
                                            'code': {'type': 'STRING'},
                                            'description': {'type': 'STRING'},
                                            'file_path': {'type': 'STRING'},
                                            'instruction': {'type': 'STRING'},
                                            'output_path': {'type': 'STRING'}},
                          'required': [],
                          'type': 'OBJECT'}},
    {   'description': 'Plans and writes a complete single-file program for a description (default '
                       'language python), saving it to output_path. With run=true a Python program is '
                       'also executed once and its output reported — output shown is not proof the '
                       'program is correct.',
        'name': 'dev_agent',
        'parameters': {   'properties': {   'description': {'type': 'STRING'},
                                            'language': {'type': 'STRING'},
                                            'output_path': {'type': 'STRING'},
                                            'run': {'type': 'BOOLEAN'}},
                          'required': ['description'],
                          'type': 'OBJECT'}},
    {   'description': 'Relaunches your own program — same interpreter, same script — when the user says '
                       '"restart your program", "restart yourself", or "restart the app". Never restarts the '
                       'computer. The relaunch happens once your spoken reply finishes. Direct user commands '
                       'only: it refuses inside routines and delegated tasks.',
        'name': 'restart_program',
        'parameters': {'properties': {}, 'type': 'OBJECT'}}]

TOOLS_DECLARATION = [
    {"function_declarations": ALL_FUNCTION_DECLARATIONS}
]

TOOLKITS = {   'autonomy': {   'summary': 'autonomous background goals, persistent background jobs, system health audit, self-healing diagnostics, delegated background tasks, update status, code help, dev agent',
                 'tools': ['manage_autonomous_goal', 'manage_background_job', 'system_health_audit', 'self_heal_diagnose',
                           'delegate_task', 'task_status', 'task_cancel', 'task_resume', 'update_status', 'code_helper', 'dev_agent', 'event_trigger']},
    'admin': {   'summary': 'API keys, bridge token, command guide, volume, approval controls, personas, user tools, shell commands, backup mirror, game updates',
                 'tools': ['bridge_token', 'show_commands', 'hide_commands', 'volume',
                           'approve', 'deny', 'list_pending_approvals', 'set_approval_mode', 'get_approval_mode',
                           'reload_user_tools', 'set_persona', 'list_personas', 'get_persona', 'set_backup_mirror', 'run_shell_command', 'game_updater']},
    'comms': {'summary': 'Gmail: store credentials, send and read email, triage the inbox',
              'tools': ['gmail_setup', 'send_email', 'read_email', 'triage_email']},
    'files': {'summary': 'file commander: organize a folder, advanced find, duplicate detection, disk usage, process a file (contents, digest, description, transcript)',
              'tools': ['file_organize', 'file_find_advanced', 'file_duplicates', 'disk_usage', 'process_file']},
    'monitor': {'summary': 'screen watcher: alert when watched screen content changes',
                'tools': ['watch_screen', 'unwatch_screen', 'list_screen_watches']},
    'routines': {'summary': 'record and replay multi-step routines (named automations)',
                 'tools': ['routine_record_start', 'routine_record_stop', 'run_routine', 'list_routines',
                           'delete_routine', 'describe_routine', 'trust_routine', 'untrust_routine',
                           'export_routine_pack', 'import_routine_pack']},
    'phone': {'summary': 'the paired Android phone node: connection status, installed apps, open an app, read the screen, notifications, tap / swipe / type / navigation keys',
              'tools': ['phone_status', 'phone_list_apps', 'phone_open_app', 'phone_read_screen',
                        'phone_notifications', 'phone_tap', 'phone_swipe', 'phone_type', 'phone_key', 'notify_phone']},
    'user': {'summary': 'user-installed custom tools (see docs/USER_TOOL_SDK.md); populated at boot and via reload_user_tools',
             'tools': []},
    'core': {   'summary': 'everyday tools: web search, open apps/URLs, run Python code, click/type, screenshots, '
                           'screen reading, clipboard, files, memory save/search, bounded workflow skills',
                'tools': [   'web_search',
                             'research',
                             'open_app_or_url',
                             'run_python_code',
                             'gui_click',
                             'gui_type',
                             'save_memory',
                             'search_memory',
                             'memory_review',
                             'take_screenshot',
                             'read_screen',
                             'fetch_url',
                             'clipboard_read',
                             'clipboard_write',
                             'find_file',
                             'read_file',
                             'write_file',
                             'list_workspace',
                             'edit_own_code',
                             'patch_own_code',
                             'restart_program',
                             'inbox_list',
                             'inbox_describe',
                             'inbox_read',
                             'run_skill', 'flight_finder', 'browser_control']},
    'github': {'summary': 'push files and create GitHub repos', 'tools': ['github_push_file', 'github_create_repo']},
    'memory_plus': {   'summary': 'notes, journal, forgetting memories, morning briefing',
                       'tools': ['forget_memory', 'journal_write', 'take_note', 'read_notes', 'morning_briefing']},
    'mcp': {   'summary': 'Model Context Protocol: add/connect MCP servers, use their tools as ARIA tools',
               'tools': ['mcp_setup', 'mcp_connect', 'mcp_disconnect', 'mcp_list_servers', 'mcp_remove_server']},
    'mtg': {   'summary': 'Magic card lookup, Commander deck advice, price watches',
               'tools': ['mtg_card', 'mtg_advice', 'watch_price', 'list_price_watches', 'unwatch_price', 'check_price_watches']},
    'scheduler': {   'summary': 'reminders, spoken timers, recurring tasks, break nudges, live calendar',
                     'tools': [   'set_reminder',
                                  'set_recurring_task',
                                  'list_scheduled_tasks',
                                  'cancel_scheduled_task',
                                  'set_timer',
                                  'break_reminders',
                                  'calendar_setup',
                                  'check_calendar', 'os_reminder']},
    'spotify': {'summary': 'music: Spotify control, DJ mode, media keys, unified media control', 'tools': ['spotify', 'dj', 'media_key', 'media_control']},
    'vision': {   'summary': 'webcam photos, camera descriptions, vision first-look questions, face tracking, neck servos',
                  'tools': ['take_photo', 'describe_camera', 'vision_look', 'face_tracking', 'move_head_servos', 'drive_wheels', 'body_stop']},
    'windows': {   'summary': 'list, focus, minimize, close, snap windows; launch apps',
                   'tools': ['list_windows', 'focus_window', 'minimize_window', 'close_window', 'window_snap', 'launch_app']}}

COMMAND_GUIDE = [   ('Memory', 'save_memory', 'remember my Doja playlist is spotify:playlist:xxx'),
    ('Memory', 'search_memory', 'what do you remember about my shifts?'),
    ('Memory', 'forget_memory', 'forget my old playlist'),
    ('Memory', 'journal_write', 'write a journal entry about today'),
    ('Web', 'web_search', 'search the web for Zero 2 W stock'),
    ('Web', 'fetch_url', 'fetch this page and summarize it'),
    ('Web', 'run_skill', 'research the new Zelda DLC with deep_research'),
    ('Laptop', 'open_app_or_url', 'open Spotify'),
    ('Laptop', 'gui_click', 'click the Play button'),
    ('Laptop', 'gui_type', 'type hello world'),
    ('Laptop', 'list_windows', 'what windows are open?'),
    ('Laptop', 'focus_window', 'focus the Spotify window'),
    ('Laptop', 'minimize_window', 'minimize this window'),
    ('Laptop', 'close_window', 'close the Calculator window'),
    ('Laptop', 'clipboard_read', 'what is in my clipboard?'),
    ('Laptop', 'clipboard_write', 'copy this tracking number to my clipboard'),
    ('Laptop', 'media_key', 'press the mute key'),
    ('Laptop', 'volume', 'set volume to 40'),
    ('Laptop', 'run_python_code', 'run a Python script to rename those files'),
    ('Laptop', 'write_file', 'save this as gig-ideas.txt'),
    ('Laptop', 'read_file', 'read gig-ideas.txt back to me'),
    ('Laptop', 'list_workspace', 'what is in your workspace?'),
    ('Laptop', 'find_file', 'find my resume PDF'),
    ('Seeing', 'take_screenshot', 'take a screenshot'),
    ('Seeing', 'read_screen', 'what does this error say?'),
    ('Seeing', 'take_photo', 'take a photo'),
    ('Seeing', 'describe_camera', 'what do I look like?'),
    ('Seeing', 'vision_look', 'what is in this picture?'),
    ('Time', 'set_timer', 'set a 20 minute timer'),
    ('Time', 'set_reminder', 'remind me at 6pm to take the trash out'),
    ('Time', 'set_recurring_task', 'remind me every weekday at 8am to check tips'),
    ('Time', 'list_scheduled_tasks', 'what reminders do I have?'),
    ('Time', 'cancel_scheduled_task', 'cancel my 6pm reminder'),
    ('Autonomy', 'manage_autonomous_goal', 'create a background goal to monitor repo status every hour'),
    ('Autonomy', 'manage_background_job', 'start a background job to run build tests'),
    ('Autonomy', 'system_health_audit', 'check laptop health and worker status'),
    ('Autonomy', 'self_heal_diagnose', 'check recent self-healing incidents'),
    ('Time', 'morning_briefing', 'brief me'),
    ('Time', 'break_reminders', 'turn my break reminders off'),
    ('Time', 'calendar_setup', 'connect my Google Calendar with this iCal URL'),
    ('Time', 'check_calendar', 'what is on my calendar this week'),
    ('Music', 'spotify', 'search Spotify for Doja Cat'),
    ('Music', 'spotify', 'play my liked songs playlist'),
    ('Music', 'dj', 'shuffle my liked songs'),
    ('Music', 'dj', 'play something chill'),
    ('Notes', 'take_note', 'note to self: buy milk'),
    ('Notes', 'read_notes', 'what were my notes Tuesday?'),
    ('MTG', 'open_app_or_url', "let's play some magic"),
    ('MTG', 'mtg_card', 'look up Teval, Arbiter of Virtue'),
    ('MTG', 'mtg_advice', 'is Doubling Season good in Teval?'),
    ('Prices', 'watch_price', 'watch the price of the Galaxy Tab S9 FE'),
    ('Prices', 'list_price_watches', 'what prices are you watching?'),
    ('Prices', 'unwatch_price', 'stop watching the tablet'),
    ('Face & body', 'face_tracking', 'follow my face'),
    ('Face & body', 'move_head_servos', 'look left'),
    ('Face & body', 'drive_wheels', 'drive forward for 2 seconds'),
    ('Face & body', 'body_stop', 'stop moving'),
    ('Phone & keys', 'bridge_token', 'what is my bridge token?'),
    ('GitHub', 'github_push_file', 'push this file to my repo'),
    ('GitHub', 'github_create_repo', 'create a repo called gig-tracker'),
    ('Email', 'gmail_setup', 'save my Gmail and app password'),
    ('Email', 'send_email', 'email mom the deck list'),
    ('Email', 'read_email', 'check my unread email'),
    ('MCP', 'mcp_setup', 'add an MCP server with command npx for the filesystem server'),
    ('MCP', 'mcp_connect', 'connect my MCP servers'),
    ('MCP', 'mcp_list_servers', 'what MCP servers are connected?'),
    ('MCP', 'mcp_disconnect', 'disconnect the filesystem MCP server'),
    ('Phone', 'phone_status', 'is my phone connected?'),
    ('Phone', 'phone_list_apps', 'what apps are on my phone?'),
    ('Phone', 'phone_open_app', 'open Spotify on my phone'),
    ('Phone', 'phone_read_screen', 'what is on my phone screen?'),
    ('Phone', 'phone_notifications', 'read my phone notifications'),
    ('This screen', 'show_commands', 'show commands'),
    ('This screen', 'hide_commands', 'hide commands'),
    ('Routines', 'routine_record_start', 'watch me do this and save it as morning setup'),
    ('Routines', 'run_routine', 'run my morning setup routine'),
    ('Routines', 'list_routines', 'what routines do I have?'),
    ('Routines', 'trust_routine', 'trust my morning setup routine'),
    ('Safety', 'set_approval_mode', 'ask me before doing anything risky'),
    ('Safety', 'approve', 'yes, do it'),
    ('Safety', 'list_pending_approvals', 'what are you waiting on approval for?'),
    ('Files', 'file_organize', 'organize my downloads folder'),
    ('Files', 'file_find_advanced', 'find large videos in my downloads'),
    ('Files', 'file_duplicates', 'find duplicate files in my photos'),
    ('Files', 'disk_usage', 'what is eating my disk space?'),
    ('Files', 'process_file', "what's in the file I dropped?"),
    ('Laptop', 'window_snap', 'snap Spotify to the left'),
    ('Laptop', 'launch_app', 'open Notepad'),
    ('Email', 'triage_email', 'triage my inbox'),
    ('Watch', 'watch_screen', 'watch the screen and tell me when the build finishes'),
    ('Watch', 'list_screen_watches', 'what screens am I watching?'),
    ('Prices', 'check_price_watches', 'check my price watches now'),
    ('System', 'restart_program', 'restart your program'),
    ('Custom', 'reload_user_tools', 'reload my custom tools'),
    ('Persona', 'set_persona', 'be concise'),
    ('Persona', 'get_persona', 'what persona are you using?'),
    ('Phone', 'notify_phone', 'notify my phone that the build finished'),
    ('System', 'set_backup_mirror', 'mirror my backups to my Drive folder'),
    ('System', 'run_shell_command', 'run dir in a shell and show me the output'),
    ('Travel', 'flight_finder', 'find flights from Baltimore to Denver on November 5'),
    ('Games', 'game_updater', 'check my Steam games for updates'),
    ('Web', 'browser_control', 'open this page in my browser'),
    ('Time', 'os_reminder', 'remind me at 6pm even if you are closed'),
    ('Autonomy', 'update_status', 'what version are you running and what is in flight?'),
    ('Autonomy', 'code_helper', 'explain this code to me'),
    ('Autonomy', 'dev_agent', 'write me a small program that renames these files')]

_DECLS_BY_NAME = {d["name"]: d for d in ALL_FUNCTION_DECLARATIONS}

# Dynamic declarations (e.g. MCP server tools) registered at runtime.
# keyed by tool name; toolkit membership is tracked in TOOLKITS[<toolkit>]["tools"].
_DYNAMIC_DECLS: Dict[str, Dict[str, Any]] = {}


def register_dynamic_tool_declaration(name: str, declaration: Dict[str, Any],
                                      toolkit: str = "mcp") -> None:
    """Register a runtime-discovered tool so it validates and is callable."""
    _DYNAMIC_DECLS[name] = declaration
    if toolkit in TOOLKITS and name not in TOOLKITS[toolkit]["tools"]:
        TOOLKITS[toolkit]["tools"].append(name)


def unregister_dynamic_tool_declaration(name: str, toolkit: str = "mcp") -> None:
    """Remove a runtime-discovered tool (e.g. on MCP server disconnect)."""
    _DYNAMIC_DECLS.pop(name, None)
    if toolkit in TOOLKITS and name in TOOLKITS[toolkit]["tools"]:
        TOOLKITS[toolkit]["tools"].remove(name)

LOAD_TOOLKIT_DECLARATION = {
    "name": "load_toolkit",
    "description": (
        "Unlock a toolkit's tools for this request. Call it with a toolkit "
        "name from the Toolkits list, then use that toolkit's tools on the "
        "next turn."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {"toolkit": {"type": "STRING"}},
        "required": ["toolkit"]
    }
}


def get_tool_decls_by_name() -> Dict[str, Dict[str, Any]]:
    """Return dictionary of function declarations keyed by tool name,
    including runtime-registered dynamic tools (e.g. MCP server tools)."""
    merged = dict(_DECLS_BY_NAME)
    merged.update(_DYNAMIC_DECLS)
    return merged


def get_toolkit_declarations(loaded_toolkits: Set[str]) -> List[Dict[str, Any]]:
    """Build the tools payload from currently loaded toolkits.
    Always appends load_toolkit meta-tool.
    """
    decls = get_tool_decls_by_name()
    out = []
    for tk in sorted(loaded_toolkits):
        if tk in TOOLKITS:
            for name in TOOLKITS[tk]["tools"]:
                if name in decls:
                    out.append(decls[name])
    out.append(LOAD_TOOLKIT_DECLARATION)
    return [{"function_declarations": out}]


def get_toolkits_prompt_block(toolkits: Dict[str, Any] = None) -> str:
    """Return the system prompt block summarizing specialist toolkits."""
    tks = toolkits or TOOLKITS
    lines = [
        "Toolkits: only the CORE tools are loaded right now. Specialist tools "
        "live in named toolkits - call load_toolkit with the toolkit name to "
        "unlock its tools, then use them on the next turn. Available toolkits:"
    ]
    for name in sorted(tks):
        if name != "core":
            lines.append(f"- {name}: {tks[name]['summary']}")
    return " ".join(lines) + " "
