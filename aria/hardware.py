"""
ARIA Hardware Controller.
Manages physical USB serial connection to robot neck servos and microcontrollers.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Tuple, Dict, Any

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    serial = None
    SERIAL_AVAILABLE = False

from aria.config import add_log

_LOCK = threading.Lock()
SERIAL_CONN: Optional[Any] = None
HARDWARE_CONNECTED: bool = False
SERVO_PAN: int = 90
SERVO_TILT: int = 45
SERVO_POS: Dict[str, int] = {"pan": 90, "tilt": 45}


def init_hardware() -> bool:
    """Attempt connection to Arduino/CH340/USB Serial microcontroller."""
    global SERIAL_CONN, HARDWARE_CONNECTED
    with _LOCK:
        if not SERIAL_AVAILABLE:
            add_log("Hardware serial library (pyserial) offline.")
            return False
        
        try:
            for port in serial.tools.list_ports.comports():
                desc = port.description or ""
                if any(h in desc for h in ["Arduino", "CH340", "USB Serial"]):
                    try:
                        SERIAL_CONN = serial.Serial(port.device, 115200, timeout=1)
                        HARDWARE_CONNECTED = True
                        add_log(f"Physical hardware linked: {port.device}")
                        return True
                    except Exception as e:
                        add_log(f"Hardware error on {port.device}: {e}")
        except Exception as e:
            add_log(f"Hardware port enumeration error: {e}")

        HARDWARE_CONNECTED = False
        add_log("Hardware: Virtual Mode (no servos connected)")
        return False


def send_servo_command(pan: int, tilt: int) -> Tuple[int, int]:
    """Clamp and transmit pan/tilt command to connected servos."""
    global SERVO_PAN, SERVO_TILT, SERIAL_CONN, HARDWARE_CONNECTED
    with _LOCK:
        SERVO_PAN = max(0, min(180, int(pan)))
        SERVO_TILT = max(0, min(90, int(tilt)))
        SERVO_POS["pan"] = SERVO_PAN
        SERVO_POS["tilt"] = SERVO_TILT

        if HARDWARE_CONNECTED and SERIAL_CONN and getattr(SERIAL_CONN, "is_open", False):
            try:
                SERIAL_CONN.write(f"P{SERVO_PAN}T{SERVO_TILT}\n".encode("utf-8"))
            except Exception as e:
                add_log(f"Servo write failed: {e}")
                HARDWARE_CONNECTED = False

        add_log(f"Servos: Pan {SERVO_PAN}deg, Tilt {SERVO_TILT}deg")
        return SERVO_PAN, SERVO_TILT


def tool_move_head_servos(pan: int = 90, tilt: int = 45) -> str:
    """Tool implementation for moving robot neck servos."""
    p, t = send_servo_command(pan, tilt)
    if HARDWARE_CONNECTED:
        return f"Head servos repositioned to Pan {p}deg, Tilt {t}deg."
    return f"Virtual servos repositioned to Pan {p}deg, Tilt {t}deg (hardware in virtual mode)."


def get_hardware_status() -> Dict[str, Any]:
    return {
        "connected": HARDWARE_CONNECTED,
        "pan": SERVO_PAN,
        "tilt": SERVO_TILT,
        "wheels": dict(WHEEL_STATE),
        "serial_available": SERIAL_AVAILABLE
    }


# ---- Drive base (phase 2 of the robot body) ----
# Continuous-rotation servos on the Arduino (D5/D6 in aria_body.ino).
# Wire protocol: b"W<left>,<right>\n", each -100..100, 0 = stopped.

WHEEL_STATE: Dict[str, int] = {"left": 0, "right": 0}


def send_drive_command(left: int, right: int) -> Tuple[int, int]:
    """Clamp and transmit wheel speeds (-100..100) to the body Arduino."""
    global SERIAL_CONN, HARDWARE_CONNECTED
    with _LOCK:
        left = max(-100, min(100, int(left)))
        right = max(-100, min(100, int(right)))
        WHEEL_STATE["left"] = left
        WHEEL_STATE["right"] = right

        if HARDWARE_CONNECTED and SERIAL_CONN and getattr(SERIAL_CONN, "is_open", False):
            try:
                SERIAL_CONN.write(f"W{left},{right}\n".encode("utf-8"))
            except Exception as e:
                add_log(f"Drive write failed: {e}")
                HARDWARE_CONNECTED = False

        add_log(f"Wheels: L {left}, R {right}")
        return left, right


def tool_drive(left: int = 0, right: int = 0, seconds: float = 0) -> str:
    """Tool implementation for driving the robot body.

    left/right: -100..100 (negative = reverse). seconds > 0 auto-stops
    the wheels after that long so ARIA can't drive off the desk forever.
    """
    l, r = send_drive_command(left, right)
    mode = "hardware" if HARDWARE_CONNECTED else "virtual mode"
    if seconds and seconds > 0:
        secs = max(0.1, min(30.0, float(seconds)))
        timer = threading.Timer(secs, send_drive_command, args=(0, 0))
        timer.daemon = True
        timer.start()
        return f"Driving L {l} / R {r} for {secs:g}s ({mode}); auto-stop armed."
    return f"Wheels set to L {l} / R {r} ({mode})."


def tool_body_stop() -> str:
    """Stop the wheels and center the head."""
    send_drive_command(0, 0)
    send_servo_command(90, 45)
    mode = "hardware" if HARDWARE_CONNECTED else "virtual mode"
    return f"Body stopped, head centered ({mode})."
