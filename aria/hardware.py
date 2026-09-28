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
        "serial_available": SERIAL_AVAILABLE
    }
