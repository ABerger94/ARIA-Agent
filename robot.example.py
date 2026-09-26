import cv2
import numpy as np
import pyttsx3
import time
import base64
import json
import os
import subprocess
import urllib.request
import urllib.error
import speech_recognition as sr
from datetime import datetime, timedelta
import psutil
from PIL import ImageGrab
import textwrap
import sqlite3
import threading
import queue

# Optional GUI & Hardware automation libraries
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False

# =====================================================================
# 1. A.R.I.A. CONFIGURATION & DIRECTORIES
# =====================================================================
GEMINI_API_KEY = "AQ...."

# Workspace & Memory Databases
WORKSPACE_DIR = os.path.join(os.path.expanduser("~"), "robot_workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)
DB_PATH = os.path.join(WORKSPACE_DIR, "aria_memory.db")

# Voice Engine
tts = pyttsx3.init()
tts.setProperty('rate', 170)
recognizer = sr.Recognizer()

# Global State Variables
CURRENT_STATE = "idle"
SUBTITLE_TEXT = "A.R.I.A. Autonomous Agent OS online. All subsystems active."
LOG_STREAM = ["A.R.I.A. Kernel 4.0 loaded.", "Neural memory database connected."]
SPEECH_QUEUE = queue.Queue()
BUSY_PROCESSING = False
SERVO_PAN, SERVO_TILT = 90, 45
HARDWARE_CONNECTED = False
SERIAL_CONN = None

def add_log(msg):
    global LOG_STREAM
    timestamp = datetime.now().strftime("%H:%M:%S")
    LOG_STREAM.append(f"[{timestamp}] {msg}")
    if len(LOG_STREAM) > 8:
        LOG_STREAM.pop(0)

# =====================================================================
# 2. PERSISTENT LONG-TERM MEMORY (SQLite)
# =====================================================================
def init_memory_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            key TEXT,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def memory_save(category: str, key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO memory (id, category, key, value, updated_at)
        VALUES ((SELECT id FROM memory WHERE key = ?), ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (key, category, key, value))
    conn.commit()
    conn.close()
    add_log(f"Memory saved: [{key}]")

def memory_search(query: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT category, key, value FROM memory WHERE key LIKE ? OR value LIKE ?", 
                   (f"%{query}%", f"%{query}%"))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return "No relevant memories found in database."
    return "\n".join([f"• [{cat}] {k}: {v}" for cat, k, v in rows])

def memory_get_all() -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT category, key, value FROM memory ORDER BY updated_at DESC LIMIT 15")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return "Memory bank is currently empty."
    return "\n".join([f"• [{cat}] {k}: {v}" for cat, k, v in rows])

init_memory_db()

# =====================================================================
# 3. PHYSICAL ROBOTICS HARDWARE BRIDGE (USB Serial)
# =====================================================================
def init_hardware():
    global SERIAL_CONN, HARDWARE_CONNECTED
    if not SERIAL_AVAILABLE:
        add_log("Hardware serial library offline.")
        return
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if "Arduino" in port.description or "CH340" in port.description or "USB Serial" in port.description:
            try:
                SERIAL_CONN = serial.Serial(port.device, 115200, timeout=1)
                HARDWARE_CONNECTED = True
                add_log(f"Physical hardware linked: {port.device}")
                return
            except Exception as e:
                add_log(f"Hardware port error: {e}")
    add_log("Hardware: Virtual Mode (No physical servos)")

def send_servo_command(pan: int, tilt: int):
    global SERVO_PAN, SERVO_TILT, SERIAL_CONN
    SERVO_PAN = max(0, min(180, pan))
    SERVO_TILT = max(0, min(90, tilt))
    if HARDWARE_CONNECTED and SERIAL_CONN and SERIAL_CONN.is_open:
        cmd = f"P{SERVO_PAN}T{SERVO_TILT}\n"
        SERIAL_CONN.write(cmd.encode())
    add_log(f"Neck orientation: Pan {SERVO_PAN}°, Tilt {SERVO_TILT}°")

init_hardware()

# =====================================================================
# 4. FULL AGENT TOOL SUITE
# =====================================================================
def tool_web_search(query: str) -> str:
    add_log(f"Searching web: '{query[:25]}...'")
    if not SEARCH_AVAILABLE:
        return "Search library not installed."
    try:
        results = DDGS().text(query, max_results=3)
        if not results:
            return "No web results found."
        return "\n".join([f"• {r['title']}: {r['body']}" for r in results])
    except Exception as e:
        return f"Search error: {e}"

def tool_run_python(code: str) -> str:
    add_log("Executing Python script...")
    temp_script = os.path.join(WORKSPACE_DIR, "_temp_run.py")
    with open(temp_script, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        result = subprocess.run(["python", temp_script], capture_output=True, text=True, timeout=15, cwd=WORKSPACE_DIR)
        output = result.stdout + result.stderr
        add_log("Execution complete.")
        return output if output.strip() else "[Code ran successfully with no console output]"
    except Exception as e:
        return f"[Execution Error: {e}]"

def tool_gui_click(x: int, y: int) -> str:
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.click(x, y)
    add_log(f"GUI: Clicked coordinates ({x}, {y})")
    return f"Successfully clicked at screen coordinates ({x}, {y})."

def tool_gui_type(text: str) -> str:
    if not GUI_AVAILABLE:
        return "PyAutoGUI not installed."
    pyautogui.write(text, interval=0.03)
    add_log(f"GUI: Typed '{text[:20]}...'")
    return f"Successfully typed text into active application."

def tool_open_app_or_url(target: str) -> str:
    add_log(f"Launching: {target}")
    try:
        if target.startswith("http://") or target.startswith("https://"):
            os.system(f'start "" "{target}"')
            return f"Opened URL: {target}"
        else:
            os.system(f'start {target}')
            return f"Launched application: {target}"
    except Exception as e:
        return f"Error opening target: {e}"

def tool_write_file(filename: str, content: str) -> str:
    filepath = os.path.join(WORKSPACE_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"Saved file '{filename}'")
    return f"File '{filename}' created and saved in workspace."

def tool_read_file(filename: str) -> str:
    filepath = os.path.join(WORKSPACE_DIR, filename)
    if not os.path.exists(filepath):
        return f"Error: File '{filename}' not found."
    with open(filepath, "r", encoding="utf-8") as f:
        add_log(f"Read file '{filename}'")
        return f.read()

def tool_list_files() -> str:
    files = [f for f in os.listdir(WORKSPACE_DIR) if not f.startswith("_")]
    return f"Files in workspace: {', '.join(files) if files else 'Empty'}"

# Declarations for Gemini Function Calling
TOOLS_DECLARATION = [
    {
        "function_declarations": [
            {
                "name": "web_search",
                "description": "Searches the live web for facts, docs, news, or answers.",
                "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}
            },
            {
                "name": "run_python_code",
                "description": "Executes Python code in workspace.",
                "parameters": {"type": "OBJECT", "properties": {"code": {"type": "STRING"}}, "required": ["code"]}
            },
            {
                "name": "save_memory",
                "description": "Stores a permanent fact, project note, or user preference in persistent memory.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "category": {"type": "STRING", "description": "e.g. user_profile, project, preference, note"},
                        "key": {"type": "STRING", "description": "Specific memory key"},
                        "value": {"type": "STRING", "description": "Information to remember"}
                    },
                    "required": ["category", "key", "value"]
                }
            },
            {
                "name": "search_memory",
                "description": "Searches long-term persistent memory for past notes, projects, or user facts.",
                "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}
            },
            {
                "name": "gui_click",
                "description": "Clicks at specific X, Y pixel coordinates on the screen.",
                "parameters": {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]}
            },
            {
                "name": "gui_type",
                "description": "Types text into the currently active computer window.",
                "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}
            },
            {
                "name": "open_app_or_url",
                "description": "Launches a desktop app or website URL.",
                "parameters": {"type": "OBJECT", "properties": {"target": {"type": "STRING"}}, "required": ["target"]}
            },
            {
                "name": "move_head_servos",
                "description": "Rotates physical robot neck servos (Pan 0-180 deg, Tilt 0-90 deg).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"pan": {"type": "INTEGER"}, "tilt": {"type": "INTEGER"}},
                    "required": ["pan", "tilt"]
                }
            },
            {
                "name": "write_file",
                "description": "Saves a file to workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"filename": {"type": "STRING"}, "content": {"type": "STRING"}},
                    "required": ["filename", "content"]
                }
            },
            {
                "name": "read_file",
                "description": "Reads a file from workspace.",
                "parameters": {"type": "OBJECT", "properties": {"filename": {"type": "STRING"}}, "required": ["filename"]}
            },
            {
                "name": "list_workspace",
                "description": "Lists all workspace files.",
                "parameters": {"type": "OBJECT", "properties": {}}
            }
        ]
    }
]

# =====================================================================
# 5. JARVIS / EVE 1280x720 WIDESCREEN HUD
# =====================================================================
LATEST_CAMERA_FRAME = None

def capture_screen():
    add_log("Capturing screen buffer...")
    img = ImageGrab.grab()
    img_np = np.array(img)
    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    small = cv2.resize(img_bgr, (960, 540))
    _, buffer = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
    return buffer.tobytes()

def capture_webcam():
    global LATEST_CAMERA_FRAME
    cap = cv2.VideoCapture(0)
    for _ in range(3):
        ret, frame = cap.read()
    cap.release()
    if ret and frame is not None:
        LATEST_CAMERA_FRAME = frame.copy()
        small = cv2.resize(frame, (640, 480))
        _, buffer = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        return buffer.tobytes()
    return None

def apply_led_scanlines(canvas, x1, y1, x2, y2):
    for y in range(max(0, y1), min(canvas.shape[0], y2), 4):
        canvas[y, max(0, x1):min(canvas.shape[1], x2)] = canvas[y, max(0, x1):min(canvas.shape[1], x2)] // 2

def draw_hud():
    global CURRENT_STATE
    w, h = 1280, 720
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    CYAN = (255, 220, 30)
    GLOW = (120, 90, 10)
    AMBER = (30, 160, 255)
    GREEN = (40, 240, 120)
    BORDER = (45, 50, 60)
    PANEL_BG = (15, 17, 22)

    # Background grid
    for x in range(0, w, 80):
        cv2.line(canvas, (x, 0), (x, h), (18, 20, 24), 1)
    for y in range(0, h, 80):
        cv2.line(canvas, (0, y), (w, y), (18, 20, 24), 1)

    # Panels
    cv2.rectangle(canvas, (20, 70), (280, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 70), (280, 460), BORDER, 1)

    cv2.rectangle(canvas, (1000, 70), (1260, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), BORDER, 1)

    cv2.rectangle(canvas, (20, 480), (1260, 705), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), BORDER, 1)

    # Header
    now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cpu_usage = psutil.cpu_percent()
    mem_usage = psutil.virtual_memory().percent
    battery = psutil.sensors_battery()
    bat_str = f"{battery.percent}%" if battery else "AC"

    cv2.putText(canvas, "A.R.I.A. // AUTONOMOUS AGENT OS v4.0", (30, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, CYAN, 2, cv2.LINE_AA)
    sys_stats = f"{now_str}  |  CPU: {cpu_usage}%  |  MEM: {mem_usage}%  |  BAT: {bat_str}"
    cv2.putText(canvas, sys_stats, (650, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 170, 180), 1, cv2.LINE_AA)
    cv2.line(canvas, (20, 55), (1260, 55), CYAN, 1)

    # Left Panel: Subsystems
    cv2.putText(canvas, "[ SUBSYSTEM MATRIX ]", (35, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
    modules = [
        ("• Hands-Free Audio", "ACTIVE"),
        ("• Neural Memory", "SYNCED"),
        ("• GUI Automation", "ONLINE"),
        ("• Proactive Heartbeat", "RUNNING"),
        ("• Physical Bridge", "LINKED" if HARDWARE_CONNECTED else "VIRTUAL"),
        ("• Neck Orientation", f"{SERVO_PAN}° / {SERVO_TILT}°")
    ]
    for i, (mod, stat) in enumerate(modules):
        cv2.putText(canvas, mod, (35, 135 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(canvas, stat, (200, 135 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.38, GREEN, 1, cv2.LINE_AA)

    # Optic PIP (Picture in Picture)
    pip_x, pip_y, pip_w, pip_h = 35, 335, 230, 115
    cv2.rectangle(canvas, (pip_x, pip_y), (pip_x + pip_w, pip_y + pip_h), (30, 35, 45), -1)
    if LATEST_CAMERA_FRAME is not None:
        thumb = cv2.resize(LATEST_CAMERA_FRAME, (pip_w, pip_h))
        canvas[pip_y:pip_y+pip_h, pip_x:pip_x+pip_w] = thumb
    cv2.circle(canvas, (pip_x + pip_w // 2, pip_y + pip_h // 2), 15, CYAN, 1)
    cv2.line(canvas, (pip_x + pip_w // 2 - 25, pip_y + pip_h // 2), (pip_x + pip_w // 2 + 25, pip_y + pip_h // 2), CYAN, 1)
    cv2.line(canvas, (pip_x + pip_w // 2, pip_y + pip_h // 2 - 25), (pip_x + pip_w // 2, pip_y + pip_h // 2 + 25), CYAN, 1)
    cv2.putText(canvas, "OPTIC FEED // CAM_01", (pip_x + 5, pip_y + pip_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.35, CYAN, 1, cv2.LINE_AA)

    # Right Panel: Action Stream
    cv2.putText(canvas, "[ ACTION STREAM ]", (1015, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
    for i, log in enumerate(LOG_STREAM):
        cv2.putText(canvas, log[:32], (1015, 135 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (170, 190, 200), 1, cv2.LINE_AA)

    # Central Visor
    lx, rx, cy = 520, 760, 230
    if CURRENT_STATE == "idle":
        for ex in (lx, rx):
            cv2.ellipse(canvas, (ex, cy), (55, 78), 0, 0, 360, GLOW, -1)
            cv2.ellipse(canvas, (ex, cy), (48, 70), 0, 0, 360, CYAN, -1)
            cv2.circle(canvas, (ex - 15, cy - 25), 8, (255, 255, 255), -1)
            apply_led_scanlines(canvas, ex - 60, cy - 80, ex + 60, cy + 80)
    elif CURRENT_STATE == "listening":
        for ex in (lx, rx):
            cv2.circle(canvas, (ex, cy), 70, (255, 80, 255), -1)
            cv2.circle(canvas, (ex - 15, cy - 20), 10, (255, 255, 255), -1)
            apply_led_scanlines(canvas, ex - 70, cy - 70, ex + 70, cy + 70)
    elif CURRENT_STATE == "thinking":
        for ex, tilt, dy in ((lx, -12, -15), (rx, 8, -5)):
            cv2.ellipse(canvas, (ex, cy + dy), (48, 68), tilt, 0, 360, (0, 100, 200), -1)
            cv2.ellipse(canvas, (ex, cy + dy), (42, 60), tilt, 0, 360, AMBER, -1)
            cv2.circle(canvas, (ex - 12, cy + dy - 20), 7, (255, 255, 255), -1)
            apply_led_scanlines(canvas, ex - 60, cy + dy - 70, ex + 60, cy + dy + 70)
    elif CURRENT_STATE == "coding":
        for ex in (lx, rx):
            cv2.rectangle(canvas, (ex - 55, cy - 65), (ex + 55, cy + 65), (0, 100, 40), -1)
            cv2.rectangle(canvas, (ex - 50, cy - 60), (ex + 50, cy + 60), GREEN, 2)
            cv2.putText(canvas, "</>", (ex - 35, cy + 12), cv2.FONT_HERSHEY_SIMPLEX, 1.1, GREEN, 2, cv2.LINE_AA)
            apply_led_scanlines(canvas, ex - 60, cy - 70, ex + 60, cy + 70)
    elif CURRENT_STATE == "speaking":
        for ex in (lx, rx):
            cv2.ellipse(canvas, (ex, cy - 10), (52, 45), 0, 190, 350, CYAN, 10)
            apply_led_scanlines(canvas, ex - 60, cy - 60, ex + 60, cy + 40)
        t = time.time() * 12
        for i in range(-16, 17):
            bar_x = 640 + (i * 12)
            bar_h = int(abs(np.sin(t + i * 0.45)) * 34) + 4
            cv2.line(canvas, (bar_x, 370 - bar_h), (bar_x, 370 + bar_h), CYAN, 2)

    # Subtitles
    cv2.putText(canvas, "[ VOCAL SYNTHESIS SUBTITLES ]", (40, 508), cv2.FONT_HERSHEY_SIMPLEX, 0.48, CYAN, 1, cv2.LINE_AA)
    wrapped_lines = textwrap.wrap(SUBTITLE_TEXT, width=88)
    if len(wrapped_lines) <= 4:
        font_scale, line_height = 0.58, 28
    elif len(wrapped_lines) <= 6:
        font_scale, line_height = 0.48, 24
    else:
        wrapped_lines = textwrap.wrap(SUBTITLE_TEXT, width=105)
        font_scale, line_height = 0.42, 20

    start_y = 538
    for idx, line in enumerate(wrapped_lines[:7]):
        cv2.putText(canvas, line, (40, start_y + idx * line_height), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (235, 242, 255), 1, cv2.LINE_AA)

    controls = "CONTROLS: [HANDS-FREE ACTIVE]  |  [SPACE] Speak  |  [S] Screen Vision  |  [T] Type  |  [Q] Exit"
    cv2.putText(canvas, controls, (40, 692), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (130, 140, 150), 1, cv2.LINE_AA)

    cv2.imshow("A.R.I.A. Autonomous Agent OS", canvas)
    cv2.waitKey(1)

def speak(text):
    global SUBTITLE_TEXT, CURRENT_STATE
    SUBTITLE_TEXT = text
    add_log(f"Speech: {text[:28]}...")
    CURRENT_STATE = "speaking"
    draw_hud()
    tts.say(text)
    tts.runAndWait()
    CURRENT_STATE = "idle"
    draw_hud()

# =====================================================================
# 6. AUTONOMOUS AGENT BRAIN (Multi-Turn + Memory Injection)
# =====================================================================
CONVERSATION_HISTORY = []

def run_agent(user_prompt, image_bytes=None, is_screen=False):
    global CONVERSATION_HISTORY, CURRENT_STATE, BUSY_PROCESSING
    BUSY_PROCESSING = True
    CURRENT_STATE = "thinking"
    draw_hud()
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    now_time = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    known_memories = memory_get_all()
    
    system_instruction = (
        f"You are A.R.I.A. (Adaptive Robotic Intelligence Agent), an embodied autonomous desktop AI Agent OS. "
        f"Current time: {now_time}. "
        f"Known persistent memories about the user and past sessions:\n{known_memories}\n"
        "Capabilities: Live web search, persistent memory read/write, Python code execution, GUI mouse/typing automation, "
        "and physical neck servo actuation. "
        "When told important personal facts, preferences, or project details, actively call 'save_memory'! "
        "Respond concisely and intelligently in 1-2 spoken sentences."
    )
    
    prompt_label = "User (Screen View): " if is_screen else "User: "
    user_parts = [{"text": f"{prompt_label}{user_prompt}"}]
    if image_bytes:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_image}})
    
    CONVERSATION_HISTORY.append({"role": "user", "parts": user_parts})
    if len(CONVERSATION_HISTORY) > 6:
        CONVERSATION_HISTORY = CONVERSATION_HISTORY[-6:]

    contents = [{"role": "user", "parts": [{"text": system_instruction}]}] + list(CONVERSATION_HISTORY)

    for _ in range(6):
        payload = {"contents": contents, "tools": TOOLS_DECLARATION}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        
        data = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    add_log("Rate limit pause (3s)...")
                    draw_hud()
                    time.sleep(3)
                else:
                    raise e
                    
        if not data:
            speak("API rate limit encountered. Standing by.")
            BUSY_PROCESSING = False
            return

        candidate = data["candidates"][0]
        model_parts = candidate["content"]["parts"]
        contents.append({"role": "model", "parts": model_parts})
        
        function_call = next((p["functionCall"] for p in model_parts if "functionCall" in p), None)
        
        if not function_call:
            text_parts = [p["text"] for p in model_parts if "text" in p and not p.get("thought", False)]
            final_text = "".join(text_parts) if text_parts else "Directive executed."
            CONVERSATION_HISTORY.append({"role": "model", "parts": [{"text": final_text}]})
            speak(final_text)
            BUSY_PROCESSING = False
            return

        CURRENT_STATE = "coding"
        draw_hud()
        fn_name = function_call["name"]
        args = function_call.get("args", {})
        tool_result = ""
        
        if fn_name == "web_search":
            tool_result = tool_web_search(args.get("query", ""))
        elif fn_name == "run_python_code":
            tool_result = tool_run_python(args.get("code", ""))
        elif fn_name == "save_memory":
            memory_save(args.get("category", "general"), args.get("key", ""), args.get("value", ""))
            tool_result = f"Memory saved: {args.get('key')}"
        elif fn_name == "search_memory":
            tool_result = memory_search(args.get("query", ""))
        elif fn_name == "gui_click":
            tool_result = tool_gui_click(int(args.get("x", 0)), int(args.get("y", 0)))
        elif fn_name == "gui_type":
            tool_result = tool_gui_type(args.get("text", ""))
        elif fn_name == "open_app_or_url":
            tool_result = tool_open_app_or_url(args.get("target", ""))
        elif fn_name == "move_head_servos":
            send_servo_command(int(args.get("pan", 90)), int(args.get("tilt", 45)))
            tool_result = "Head servos repositioned."
        elif fn_name == "write_file":
            tool_result = tool_write_file(args.get("filename", "file.txt"), args.get("content", ""))
        elif fn_name == "read_file":
            tool_result = tool_read_file(args.get("filename", ""))
        elif fn_name == "list_workspace":
            tool_result = tool_list_files()

        contents.append({
            "role": "user",
            "parts": [{"functionResponse": {"name": fn_name, "response": {"output": tool_result}}}]
        })
        CURRENT_STATE = "thinking"
        draw_hud()

    BUSY_PROCESSING = False

# =====================================================================
# 7. PROACTIVE HEARTBEAT ENGINE (Autonomous Daemon)
# =====================================================================
def proactive_heartbeat_loop():
    """Runs continuously in the background every 30s to watch over user."""
    time.sleep(10)
    last_battery_alert = False
    session_start = time.time()
    
    while True:
        try:
            # 1. Low battery alert
            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged and battery.percent < 20 and not last_battery_alert:
                last_battery_alert = True
                if not BUSY_PROCESSING:
                    speak(f"Allen, your laptop battery is at {battery.percent}%. Please connect to AC power.")
            elif battery and battery.power_plugged:
                last_battery_alert = False

            # 2. 60-Minute Focus Break
            elapsed_hours = (time.time() - session_start) / 3600
            if elapsed_hours >= 1.0:
                session_start = time.time()
                if not BUSY_PROCESSING:
                    speak("You have been active for an hour. Consider stretching your eyes.")
        except Exception as e:
            add_log(f"Heartbeat err: {e}")
            
        time.sleep(30)

# Start background proactive engine
threading.Thread(target=proactive_heartbeat_loop, daemon=True).start()

# =====================================================================
# 8. HANDS-FREE VOICE & ACTION DISPATCHER
# =====================================================================
def handle_action(mode="voice", typed_prompt=None):
    global CURRENT_STATE
    user_text = ""
    if typed_prompt:
        user_text = typed_prompt
        add_log(f"Directive: {user_text[:25]}")
    else:
        CURRENT_STATE = "listening"
        draw_hud()
        add_log("Microphone listening...")
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=6, phrase_time_limit=10)
                user_text = recognizer.recognize_google(audio)
                add_log(f"Acoustic: '{user_text[:25]}...'")
        except Exception as e:
            CURRENT_STATE = "idle"
            draw_hud()
            return

    image_bytes = None
    is_screen = False
    if mode == "screen" or any(k in user_text.lower() for k in ["screen", "display", "desktop", "my window"]):
        image_bytes = capture_screen()
        is_screen = True
    elif mode == "camera" or any(k in user_text.lower() for k in ["look", "see", "holding", "camera"]):
        image_bytes = capture_webcam()

    threading.Thread(target=run_agent, args=(user_text, image_bytes, is_screen), daemon=True).start()

def continuous_voice_listener():
    """Listens continuously in the background for speech."""
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=1.0)
        while True:
            if not BUSY_PROCESSING and CURRENT_STATE == "idle":
                try:
                    audio = recognizer.listen(source, timeout=3, phrase_time_limit=8)
                    transcript = recognizer.recognize_google(audio).lower()
                    if "aria" in transcript or "hey aria" in transcript:
                        add_log(f"Wake trigger: '{transcript[:25]}'")
                        cleaned = transcript.replace("hey aria", "").replace("aria", "").strip()
                        if cleaned:
                            threading.Thread(target=run_agent, args=(cleaned, None, False), daemon=True).start()
                        else:
                            speak("I'm listening.")
                except:
                    pass
            time.sleep(0.3)

# Start hands-free voice loop in background
threading.Thread(target=continuous_voice_listener, daemon=True).start()

# =====================================================================
# 9. MAIN EVENT LOOP
# =====================================================================
print("\n" + "="*70)
print(" A.R.I.A. AUTONOMOUS AGENT OPERATING SYSTEM (v4.0)")
print(" Features: Hands-Free Voice, SQLite Memory, Heartbeat, GUI Auto, Servos")
print(" Controls:")
print("  - Just say: 'Hey A.R.I.A. [command]' anytime!")
print("  - [SPACEBAR] : Manual Voice Input")
print("  - [S]        : Screen Vision (Reads your current laptop display)")
print("  - [T]        : Type directive directly")
print("  - [Q]        : Disengage / Shutdown")
print("="*70 + "\n")

draw_hud()
speak("A.R.I.A. Autonomous Agent OS initialized. Hands-free voice and memory matrix online.")

while True:
    draw_hud()
    key = cv2.waitKey(100) & 0xFF
    if key == ord(' '):
        handle_action(mode="voice")
    elif key == ord('s') or key == ord('S'):
        handle_action(mode="screen")
    elif key == ord('t') or key == ord('T'):
        CURRENT_STATE = "listening"
        draw_hud()
        prompt = input("\nEnter directive: ")
        if prompt.strip():
            handle_action(typed_prompt=prompt)
    elif key == ord('q') or key == 27:
        break

cv2.destroyAllWindows()
