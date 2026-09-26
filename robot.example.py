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

# =====================================================================
# 1. A.R.I.A. CONFIGURATION & CREDENTIALS
# =====================================================================
GEMINI_API_KEY = "INSERT"

# GitHub Credentials (Optional - paste yours here)
GITHUB_TOKEN = "INSERT"
GITHUB_USERNAME = "ABerger94"

# Workspace Directory
WORKSPACE_DIR = os.path.join(os.path.expanduser("~"), "robot_workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# Voice & Audio Setup
tts = pyttsx3.init()
tts.setProperty('rate', 170)
recognizer = sr.Recognizer()

# Rolling Conversation Memory & Log Stream
CONVERSATION_HISTORY = []
LOG_STREAM = [
    "A.R.I.A. Kernel 3.0 initialized.",
    "Neural matrix linked to Gemini-2.5.",
    "Optic and acoustic sensors primed.",
    "System standby. Awaiting directive."
]
SUBTITLE_TEXT = "A.R.I.A. online. At your service, Allen."

def add_log(msg):
    global LOG_STREAM
    timestamp = datetime.now().strftime("%H:%M:%S")
    LOG_STREAM.append(f"[{timestamp}] {msg}")
    if len(LOG_STREAM) > 8:
        LOG_STREAM.pop(0)

# Optional Web Search
try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False

# =====================================================================
# 2. AGENT TOOL ENGINE (SYSTEM, SCREEN, WEB, FILES, GITHUB)
# =====================================================================
def tool_web_search(query: str) -> str:
    add_log(f"Searching web: '{query[:25]}...'")
    if not SEARCH_AVAILABLE:
        return "Web search library not installed."
    try:
        results = DDGS().text(query, max_results=3)
        if not results:
            return "No web results found."
        summary = "\n".join([f"• {r['title']}: {r['body']}" for r in results])
        return summary
    except Exception as e:
        return f"Search error: {e}"

def tool_run_python(code: str) -> str:
    add_log("Executing Python script in workspace...")
    temp_script = os.path.join(WORKSPACE_DIR, "_temp_run.py")
    with open(temp_script, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        result = subprocess.run(["python", temp_script], capture_output=True, text=True, timeout=15, cwd=WORKSPACE_DIR)
        output = result.stdout + result.stderr
        add_log("Python execution finished.")
        return output if output.strip() else "[Code ran successfully with no output]"
    except Exception as e:
        return f"[Execution Error: {e}]"

def tool_open_app_or_url(target: str) -> str:
    """Opens a website or application on the user's laptop."""
    add_log(f"Launching: {target}")
    try:
        if target.startswith("http://") or target.startswith("https://"):
            os.system(f'start "" "{target}"')
            return f"Opened website: {target}"
        else:
            os.system(f'start {target}')
            return f"Launched application: {target}"
    except Exception as e:
        return f"Error opening target: {e}"

def tool_write_file(filename: str, content: str) -> str:
    filepath = os.path.join(WORKSPACE_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    add_log(f"File created: '{filename}'")
    return f"File '{filename}' created and saved successfully in workspace."

def tool_read_file(filename: str) -> str:
    filepath = os.path.join(WORKSPACE_DIR, filename)
    if not os.path.exists(filepath):
        return f"Error: File '{filename}' not found."
    with open(filepath, "r", encoding="utf-8") as f:
        add_log(f"Read file: '{filename}'")
        return f.read()

def tool_list_files() -> str:
    files = [f for f in os.listdir(WORKSPACE_DIR) if not f.startswith("_")]
    return f"Files in workspace: {', '.join(files) if files else 'Empty'}"

# Declarations for Gemini
TOOLS_DECLARATION = [
    {
        "function_declarations": [
            {
                "name": "web_search",
                "description": "Searches the live web for facts, docs, news, or answers.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"query": {"type": "STRING", "description": "Search query."}},
                    "required": ["query"]
                }
            },
            {
                "name": "run_python_code",
                "description": "Executes Python code in the robot workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"code": {"type": "STRING", "description": "Python code to execute."}},
                    "required": ["code"]
                }
            },
            {
                "name": "open_app_or_url",
                "description": "Launches a software application (e.g. notepad, calc, chrome) or opens a URL in the browser.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"target": {"type": "STRING", "description": "Application name or full website URL."}},
                    "required": ["target"]
                }
            },
            {
                "name": "write_file",
                "description": "Saves a file to workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filename": {"type": "STRING", "description": "File name."},
                        "content": {"type": "STRING", "description": "Content."}
                    },
                    "required": ["filename", "content"]
                }
            },
            {
                "name": "read_file",
                "description": "Reads a file from workspace.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"filename": {"type": "STRING", "description": "File name."}},
                    "required": ["filename"]
                }
            },
            {
                "name": "list_workspace",
                "description": "Lists all files currently in workspace.",
                "parameters": {"type": "OBJECT", "properties": {}}
            }
        ]
    }
]

# =====================================================================
# 3. JARVIS / EVE MULTI-PANEL HUD RENDERER (1280x720 Widescreen)
# =====================================================================
LATEST_CAMERA_FRAME = None

def capture_screen():
    """Captures the current laptop display."""
    add_log("Capturing primary screen buffer...")
    img = ImageGrab.grab()
    img_np = np.array(img)
    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    small = cv2.resize(img_bgr, (960, 540))
    _, buffer = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
    return buffer.tobytes()

def capture_webcam():
    """Captures a webcam frame."""
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

def draw_hud(state="idle"):
    w, h = 1280, 720
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    # Color Palette
    CYAN = (255, 220, 30)
    GLOW = (120, 90, 10)
    AMBER = (30, 160, 255)
    GREEN = (40, 240, 120)
    BORDER = (45, 50, 60)
    PANEL_BG = (15, 17, 22)

    # 1. Background Grid
    for x in range(0, w, 80):
        cv2.line(canvas, (x, 0), (x, h), (18, 20, 24), 1)
    for y in range(0, h, 80):
        cv2.line(canvas, (0, y), (w, y), (18, 20, 24), 1)

    # 2. Panel Outlines
    # Left Telemetry Panel
    cv2.rectangle(canvas, (20, 70), (280, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 70), (280, 460), BORDER, 1)

    # Right Action Stream Panel
    cv2.rectangle(canvas, (1000, 70), (1260, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), BORDER, 1)

    # Bottom Subtitle & Status Deck
    cv2.rectangle(canvas, (20, 480), (1260, 705), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), BORDER, 1)

    # 3. Top Header Bar
    now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cpu_usage = psutil.cpu_percent()
    mem_usage = psutil.virtual_memory().percent
    battery = psutil.sensors_battery()
    bat_str = f"{battery.percent}%" if battery else "AC"

    cv2.putText(canvas, "A.R.I.A. // ADAPTIVE ROBOTIC INTELLIGENCE AGENT", (30, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, CYAN, 2, cv2.LINE_AA)
    sys_stats = f"TIME: {now_str}  |  CPU: {cpu_usage}%  |  MEM: {mem_usage}%  |  PWR: {bat_str}"
    cv2.putText(canvas, sys_stats, (650, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 170, 180), 1, cv2.LINE_AA)
    cv2.line(canvas, (20, 55), (1260, 55), CYAN, 1)

    # 4. Left Panel: Active Subsystems
    cv2.putText(canvas, "[ SUBSYSTEMS ]", (35, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
    modules = [
        ("• Vision Optics", "ONLINE"),
        ("• Screen Perception", "ACTIVE"),
        ("• Web Grounding", "READY"),
        ("• Python Sandbox", "IDLE"),
        ("• Google Workspace", "STANDBY"),
        ("• GitHub Sync", "LINKED")
    ]
    for i, (mod, stat) in enumerate(modules):
        cv2.putText(canvas, mod, (35, 135 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(canvas, stat, (210, 135 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.4, GREEN, 1, cv2.LINE_AA)

    # Mini Optic Picture-in-Picture (Bottom-Left inside Left Panel)
    pip_x, pip_y, pip_w, pip_h = 35, 335, 230, 115
    cv2.rectangle(canvas, (pip_x, pip_y), (pip_x + pip_w, pip_y + pip_h), (30, 35, 45), -1)
    if LATEST_CAMERA_FRAME is not None:
        thumb = cv2.resize(LATEST_CAMERA_FRAME, (pip_w, pip_h))
        canvas[pip_y:pip_y+pip_h, pip_x:pip_x+pip_w] = thumb
    # Targeting Reticle overlay
    cv2.circle(canvas, (pip_x + pip_w // 2, pip_y + pip_h // 2), 15, CYAN, 1)
    cv2.line(canvas, (pip_x + pip_w // 2 - 25, pip_y + pip_h // 2), (pip_x + pip_w // 2 + 25, pip_y + pip_h // 2), CYAN, 1)
    cv2.line(canvas, (pip_x + pip_w // 2, pip_y + pip_h // 2 - 25), (pip_x + pip_w // 2, pip_y + pip_h // 2 + 25), CYAN, 1)
    cv2.putText(canvas, "CAM_01 // OPTIC PIP", (pip_x + 5, pip_y + pip_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.35, CYAN, 1, cv2.LINE_AA)

    # 5. Right Panel: Action & Tool Stream
    cv2.putText(canvas, "[ ACTION STREAM ]", (1015, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
    for i, log in enumerate(LOG_STREAM):
        cv2.putText(canvas, log[:30], (1015, 135 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (170, 190, 200), 1, cv2.LINE_AA)

    # 6. Central Visor (EVE Eyes + Waveform)
    lx, rx, cy = 520, 760, 230
    if state == "idle":
        for ex in (lx, rx):
            cv2.ellipse(canvas, (ex, cy), (55, 78), 0, 0, 360, GLOW, -1)
            cv2.ellipse(canvas, (ex, cy), (48, 70), 0, 0, 360, CYAN, -1)
            cv2.circle(canvas, (ex - 15, cy - 25), 8, (255, 255, 255), -1)
            apply_led_scanlines(canvas, ex - 60, cy - 80, ex + 60, cy + 80)
    elif state == "blink":
        for ex in (lx, rx):
            cv2.line(canvas, (ex - 50, cy), (ex + 50, cy), CYAN, 4)
    elif state == "listening":
        for ex in (lx, rx):
            cv2.circle(canvas, (ex, cy), 70, (255, 80, 255), -1)
            cv2.circle(canvas, (ex - 15, cy - 20), 10, (255, 255, 255), -1)
            apply_led_scanlines(canvas, ex - 70, cy - 70, ex + 70, cy + 70)
    elif state == "thinking":
        for ex, tilt, dy in ((lx, -12, -15), (rx, 8, -5)):
            cv2.ellipse(canvas, (ex, cy + dy), (48, 68), tilt, 0, 360, (0, 100, 200), -1)
            cv2.ellipse(canvas, (ex, cy + dy), (42, 60), tilt, 0, 360, AMBER, -1)
            cv2.circle(canvas, (ex - 12, cy + dy - 20), 7, (255, 255, 255), -1)
            apply_led_scanlines(canvas, ex - 60, cy + dy - 70, ex + 60, cy + dy + 70)
    elif state == "coding":
        for ex in (lx, rx):
            cv2.rectangle(canvas, (ex - 55, cy - 65), (ex + 55, cy + 65), (0, 100, 40), -1)
            cv2.rectangle(canvas, (ex - 50, cy - 60), (ex + 50, cy + 60), GREEN, 2)
            cv2.putText(canvas, "</>", (ex - 35, cy + 12), cv2.FONT_HERSHEY_SIMPLEX, 1.1, GREEN, 2, cv2.LINE_AA)
            apply_led_scanlines(canvas, ex - 60, cy - 70, ex + 60, cy + 70)
    elif state == "speaking":
        for ex in (lx, rx):
            cv2.ellipse(canvas, (ex, cy - 10), (52, 45), 0, 190, 350, CYAN, 10)
            apply_led_scanlines(canvas, ex - 60, cy - 60, ex + 60, cy + 40)
        t = time.time() * 12
        for i in range(-16, 17):
            bar_x = 640 + (i * 12)
            bar_h = int(abs(np.sin(t + i * 0.45)) * 34) + 4
            cv2.line(canvas, (bar_x, 370 - bar_h), (bar_x, 370 + bar_h), CYAN, 2)

    # 7. Bottom Subtitle & Conversation Panel (Auto-Wrapping, Anti-Aliased, Full View)
    cv2.putText(canvas, "[ A.R.I.A. VOCAL SUBTITLES ]", (40, 508), cv2.FONT_HERSHEY_SIMPLEX, 0.48, CYAN, 1, cv2.LINE_AA)
    
    # Proper word boundaries (never chops words mid-character)
    wrapped_lines = textwrap.wrap(SUBTITLE_TEXT, width=88)
    
    if len(wrapped_lines) <= 4:
        font_scale = 0.58
        line_height = 28
    elif len(wrapped_lines) <= 6:
        font_scale = 0.48
        line_height = 24
    else:
        # Re-wrap tighter for very long paragraphs
        wrapped_lines = textwrap.wrap(SUBTITLE_TEXT, width=105)
        font_scale = 0.42
        line_height = 20

    start_y = 538
    for idx, line in enumerate(wrapped_lines[:7]):
        cv2.putText(canvas, line, (40, start_y + idx * line_height), 
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (235, 242, 255), 1, cv2.LINE_AA)

    # Controls Helper at the bottom
    controls = "CONTROLS: [SPACE] Smart Voice  |  [S] Look at Screen  |  [V] Force Camera  |  [T] Type  |  [Q] Exit"
    cv2.putText(canvas, controls, (40, 692), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (130, 140, 150), 1, cv2.LINE_AA)

    cv2.imshow("A.R.I.A. Desktop Agent OS", canvas)
    cv2.waitKey(1)

def speak(text):
    global SUBTITLE_TEXT
    SUBTITLE_TEXT = text
    add_log(f"Speech: {text[:30]}...")
    draw_hud("speaking")
    tts.say(text)
    tts.runAndWait()
    draw_hud("idle")

# =====================================================================
# 4. MULTI-TURN AGENT ENGINE WITH CONVERSATION MEMORY
# =====================================================================
def run_agent(user_prompt, image_bytes=None, is_screen=False):
    global CONVERSATION_HISTORY
    draw_hud("thinking")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    current_time_str = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    system_instruction = (
        f"You are A.R.I.A. (Adaptive Robotic Intelligence Agent), an embodied desktop AI OS running on the user's laptop. "
        f"Current time: {current_time_str}. "
        "You can see through your camera, inspect what is on the user's laptop screen, browse the live web, "
        "launch desktop applications, write & run Python scripts, and manage files. "
        "When asked to inspect the screen or webcam, analyze the image thoroughly. "
        "Maintain conversation context across multiple turns. Keep vocal answers concise, refined, and intelligent (1-2 sentences)."
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

    # Tool Execution Loop
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
                    add_log("Rate limit cooldown (3s)...")
                    draw_hud("thinking")
                    time.sleep(3)
                else:
                    raise e
                    
        if not data:
            speak("Rate limit encountered. Please hold for a moment.")
            return

        candidate = data["candidates"][0]
        model_parts = candidate["content"]["parts"]
        contents.append({"role": "model", "parts": model_parts})
        
        function_call = next((p["functionCall"] for p in model_parts if "functionCall" in p), None)
        
        if not function_call:
            text_parts = [p["text"] for p in model_parts if "text" in p and not p.get("thought", False)]
            final_text = "".join(text_parts) if text_parts else "Directive complete."
            CONVERSATION_HISTORY.append({"role": "model", "parts": [{"text": final_text}]})
            speak(final_text)
            return

        # Execute Tool
        draw_hud("coding")
        fn_name = function_call["name"]
        args = function_call.get("args", {})
        tool_result = ""
        
        if fn_name == "web_search":
            tool_result = tool_web_search(args.get("query", ""))
        elif fn_name == "run_python_code":
            tool_result = tool_run_python(args.get("code", ""))
        elif fn_name == "open_app_or_url":
            tool_result = tool_open_app_or_url(args.get("target", ""))
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
        draw_hud("thinking")

def listen_and_act(mode="voice", typed_prompt=None):
    user_text = ""
    if typed_prompt:
        user_text = typed_prompt
        add_log(f"Typed directive: {user_text[:30]}")
    else:
        draw_hud("listening")
        add_log("Acoustic microphone engaged...")
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.6)
                audio = recognizer.listen(source, timeout=6, phrase_time_limit=10)
                user_text = recognizer.recognize_google(audio)
                add_log(f"Heard: '{user_text[:25]}...'")
        except sr.WaitTimeoutError:
            speak("No acoustic signal detected.")
            return
        except sr.UnknownValueError:
            speak("Audio signal distorted.")
            return
        except Exception as e:
            add_log(f"Mic Error: {e}")
            speak("Acoustic subsystem error.")
            return

    # Check Vision Modes
    image_bytes = None
    is_screen = False

    if mode == "screen" or any(k in user_text.lower() for k in ["screen", "display", "desktop", "my window"]):
        add_log("Capturing primary screen...")
        image_bytes = capture_screen()
        is_screen = True
    elif mode == "camera" or any(k in user_text.lower() for k in ["look", "see", "holding", "camera", "photo"]):
        add_log("Capturing optic camera...")
        image_bytes = capture_webcam()

    try:
        run_agent(user_text, image_bytes=image_bytes, is_screen=is_screen)
    except Exception as e:
        add_log(f"Agent Error: {e}")
        speak(f"Protocol error: {e}")

# =====================================================================
# MAIN EVENT LOOP
# =====================================================================
print("\n" + "="*70)
print(" A.R.I.A. DESKTOP AGENT OS (GOOGLE ASTRA + JARVIS ARCHITECTURE)")
print(" Controls:")
print("  - [SPACEBAR] : Audio Command (Auto-detects when to use camera)")
print("  - [S]        : Screen Perception (Voice + Captures your laptop screen!)")
print("  - [V]        : Optic Cam (Voice + Captures webcam)")
print("  - [T]        : Type command into terminal")
print("  - [Q]        : Disengage / Shutdown")
print("="*70 + "\n")

draw_hud("idle")
time.sleep(0.4)
draw_hud("blink")
time.sleep(0.2)
draw_hud("idle")

speak("A.R.I.A. Desktop Agent OS online. Systems synchronized. How may I assist you?")

while True:
    draw_hud("idle")
    key = cv2.waitKey(100) & 0xFF
    
    if key == ord(' '):  # Smart Voice
        listen_and_act(mode="voice")
    elif key == ord('s') or key == ord('S'):  # Screen Vision
        listen_and_act(mode="screen")
    elif key == ord('v') or key == ord('V'):  # Webcam Vision
        listen_and_act(mode="camera")
    elif key == ord('t') or key == ord('T'):  # Type
        draw_hud("listening")
        prompt = input("\nEnter protocol directive: ")
        if prompt.strip():
            listen_and_act(typed_prompt=prompt)
    elif key == ord('q') or key == 27:
        break

cv2.destroyAllWindows()