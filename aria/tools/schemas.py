"""
ARIA Tool Schemas, Toolkits, and Command Guide.
Defines function declarations for Gemini tool calling, progressive toolkit mappings,
and user-facing command examples.
"""

from typing import Dict, List, Any, Set

ALL_FUNCTION_DECLARATIONS = [   {   'description': 'Searches the live web for facts, docs, news, or answers.',
        'name': 'web_search',
        'parameters': {'properties': {'query': {'type': 'STRING'}}, 'required': ['query'], 'type': 'OBJECT'}},
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
    {   'description': 'Manage the Gemini API key pool. Quota is per Google Cloud project, so each key should come '
                       'from a separate project. Actions: status (pool health), add (adds a new key), remove (removes '
                       'by number).',
        'name': 'gemini_keys',
        'parameters': {'properties': {'action': {'type': 'STRING'}, 'key': {'type': 'STRING'}}, 'type': 'OBJECT'}},
    {   'description': 'Run a bounded workflow skill: a reusable multi-step procedure with a hard cap on tool calls. '
                       'Skills: deep_research (web research with cited summary), system_check (laptop health report), '
                       'file_sweep (find and digest workspace files matching the objective). Prefer a skill over a '
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
    {   'description': 'Press a media key: mute, volume_up, volume_down, play_pause, next, prev.',
        'name': 'media_key',
        'parameters': {'properties': {'action': {'type': 'STRING'}}, 'required': ['action'], 'type': 'OBJECT'}},
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
        'parameters': {'properties': {'name': {'type': 'STRING'}}, 'required': ['name'], 'type': 'OBJECT'}}]

TOOLS_DECLARATION = [
    {"function_declarations": ALL_FUNCTION_DECLARATIONS}
]

TOOLKITS = {   'admin': {   'summary': 'API keys, bridge token, command guide, volume',
                 'tools': ['gemini_keys', 'bridge_token', 'show_commands', 'hide_commands', 'volume']},
    'comms': {'summary': 'Gmail: store credentials, send and read email',
              'tools': ['gmail_setup', 'send_email', 'read_email']},
    'core': {   'summary': 'everyday tools: web search, open apps/URLs, run Python code, click/type, screenshots, '
                           'screen reading, clipboard, files, memory save/search, bounded workflow skills',
                'tools': [   'web_search',
                             'open_app_or_url',
                             'run_python_code',
                             'gui_click',
                             'gui_type',
                             'save_memory',
                             'search_memory',
                             'take_screenshot',
                             'read_screen',
                             'fetch_url',
                             'clipboard_read',
                             'clipboard_write',
                             'find_file',
                             'read_file',
                             'write_file',
                             'list_workspace',
                             'run_skill']},
    'github': {'summary': 'push files and create GitHub repos', 'tools': ['github_push_file', 'github_create_repo']},
    'memory_plus': {   'summary': 'notes, journal, forgetting memories, morning briefing',
                       'tools': ['forget_memory', 'journal_write', 'take_note', 'read_notes', 'morning_briefing']},
    'mcp': {   'summary': 'Model Context Protocol: add/connect MCP servers, use their tools as ARIA tools',
               'tools': ['mcp_setup', 'mcp_connect', 'mcp_disconnect', 'mcp_list_servers', 'mcp_remove_server']},
    'mtg': {   'summary': 'Magic card lookup, Commander deck advice, price watches',
               'tools': ['mtg_card', 'mtg_advice', 'watch_price', 'list_price_watches', 'unwatch_price']},
    'scheduler': {   'summary': 'reminders, spoken timers, recurring tasks, break nudges, live calendar',
                     'tools': [   'set_reminder',
                                  'set_recurring_task',
                                  'list_scheduled_tasks',
                                  'cancel_scheduled_task',
                                  'set_timer',
                                  'break_reminders',
                                  'calendar_setup',
                                  'check_calendar']},
    'spotify': {'summary': 'music: Spotify control, DJ mode, media keys', 'tools': ['spotify', 'dj', 'media_key']},
    'vision': {   'summary': 'webcam photos, face tracking, neck servos',
                  'tools': ['take_photo', 'face_tracking', 'move_head_servos', 'drive_wheels', 'body_stop']},
    'windows': {   'summary': 'list, focus, minimize, close windows',
                   'tools': ['list_windows', 'focus_window', 'minimize_window', 'close_window']}}

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
    ('Time', 'set_timer', 'set a 20 minute timer'),
    ('Time', 'set_reminder', 'remind me at 6pm to take the trash out'),
    ('Time', 'set_recurring_task', 'remind me every weekday at 8am to check tips'),
    ('Time', 'list_scheduled_tasks', 'what reminders do I have?'),
    ('Time', 'cancel_scheduled_task', 'cancel my 6pm reminder'),
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
    ('Phone & keys', 'gemini_keys', 'check my Gemini keys'),
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
    ('This screen', 'show_commands', 'show commands'),
    ('This screen', 'hide_commands', 'hide commands')]

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
