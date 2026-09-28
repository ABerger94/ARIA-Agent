"""
A.R.I.A. System Tray Controller
Runs ARIA in the background with a Windows system tray icon and status menu.
"""
import os
import sys
import webbrowser
import subprocess
from PIL import Image, ImageDraw
import pystray

def create_tray_icon():
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(img)
    # Cyan outer ring
    dc.ellipse((4, 4, 60, 60), outline=(0, 220, 255), width=4)
    # Glowing core
    dc.ellipse((18, 18, 46, 46), fill=(0, 220, 255))
    return img

def open_bridge(icon, item):
    webbrowser.open("http://localhost:8000")

def open_chat_log(icon, item):
    log_path = os.path.join(os.path.dirname(__file__), "workspace", "chat_history.md")
    if os.path.exists(log_path):
        os.startfile(log_path)

def open_workspace(icon, item):
    ws = os.path.join(os.path.dirname(__file__), "workspace")
    if os.path.exists(ws):
        os.startfile(ws)

def quit_aria(icon, item):
    icon.stop()

def setup_tray():
    icon_img = create_tray_icon()
    menu = pystray.Menu(
        pystray.MenuItem("A.R.I.A. Status: Active", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Phone Bridge", open_bridge),
        pystray.MenuItem("View Chat History", open_chat_log),
        pystray.MenuItem("Open Workspace Folder", open_workspace),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Tray Controller", quit_aria)
    )
    return pystray.Icon("ARIA", icon_img, "A.R.I.A. Agent", menu)

if __name__ == "__main__":
    tray = setup_tray()
    print("Tray initialized.")
    # Run non-blocking test
    tray.run_detached()
    import time
    time.sleep(1)
    tray.stop()
    print("Tray test passed.")
