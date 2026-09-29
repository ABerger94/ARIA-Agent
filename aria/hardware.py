"""
ARIA Hardware Controller.
Manages physical USB serial connection to robot neck servos and microcontrollers,
featuring Exponential Moving Average (EMA) smoothing, deadband micro-jitter suppression,
packet rate limiting, and virtual mode fallback.
"""

from __future__ import annotations

import logging
import threading
import time
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

SERVO_PAN: float = 90.0
SERVO_TILT: float = 45.0
SERVO_POS: Dict[str, int] = {"pan": 90, "tilt": 45}

# Jitter suppression and smoothing parameters
SERVO_ALPHA: float = 0.35            # EMA weight (0.0 to 1.0)
SERVO_DEADBAND: float = 1.5          # Minimum degrees required to trigger serial movement
_MIN_PACKET_INTERVAL: float = 0.030  # Max ~33Hz to prevent serial buffer bloat
_LAST_TRANSMIT_TIME: float = 0.0
_LAST_LOG_PAN: float = 90.0
_LAST_LOG_TILT: float = 45.0


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


def send_servo_command(pan: float, tilt: float, force: bool = False) -> Tuple[int, int]:
    """Clamp, smooth (EMA), deadband-filter, and transmit pan/tilt command to connected servos."""
    global SERVO_PAN, SERVO_TILT, SERIAL_CONN, HARDWARE_CONNECTED, _LAST_TRANSMIT_TIME, _LAST_LOG_PAN, _LAST_LOG_TILT
    with _LOCK:
        target_pan = max(0.0, min(180.0, float(pan)))
        target_tilt = max(0.0, min(180.0, float(tilt)))

        d_pan = abs(target_pan - SERVO_PAN)
        d_tilt = abs(target_tilt - SERVO_TILT)

        now = time.time()
        # Deadband & rate limit check (unless forced by explicit user tool call)
        if not force:
            if d_pan < SERVO_DEADBAND and d_tilt < SERVO_DEADBAND:
                return int(round(SERVO_PAN)), int(round(SERVO_TILT))
            if (now - _LAST_TRANSMIT_TIME) < _MIN_PACKET_INTERVAL:
                return int(round(SERVO_PAN)), int(round(SERVO_TILT))

        # Exponential Moving Average smoothing
        if force:
            SERVO_PAN = target_pan
            SERVO_TILT = target_tilt
        else:
            SERVO_PAN = (SERVO_ALPHA * target_pan) + ((1.0 - SERVO_ALPHA) * SERVO_PAN)
            SERVO_TILT = (SERVO_ALPHA * target_tilt) + ((1.0 - SERVO_ALPHA) * SERVO_TILT)

        out_pan = int(round(SERVO_PAN))
        out_tilt = int(round(SERVO_TILT))

        SERVO_POS["pan"] = out_pan
        SERVO_POS["tilt"] = out_tilt
        _LAST_TRANSMIT_TIME = now

        if HARDWARE_CONNECTED and SERIAL_CONN and getattr(SERIAL_CONN, "is_open", False):
            try:
                SERIAL_CONN.write(f"P{out_pan}T{out_tilt}\n".encode("utf-8"))
            except Exception as e:
                add_log(f"Servo write failed: {e}")
                HARDWARE_CONNECTED = False

        # Suppress log spam: only log when position changes significantly or when forced
        if force or abs(out_pan - _LAST_LOG_PAN) >= 5 or abs(out_tilt - _LAST_LOG_TILT) >= 5:
            add_log(f"Servos: Pan {out_pan}deg, Tilt {out_tilt}deg")
            _LAST_LOG_PAN = float(out_pan)
            _LAST_LOG_TILT = float(out_tilt)

        return out_pan, out_tilt


def tool_move_head_servos(pan: int = 90, tilt: int = 45) -> str:
    """Tool implementation for moving robot neck servos."""
    p, t = send_servo_command(pan, tilt, force=True)
    if HARDWARE_CONNECTED:
        return f"Head servos repositioned to Pan {p}deg, Tilt {t}deg."
    return f"Virtual servos repositioned to Pan {p}deg, Tilt {t}deg (hardware in virtual mode)."


def get_hardware_status() -> Dict[str, Any]:
    return {
        "connected": HARDWARE_CONNECTED,
        "pan": int(round(SERVO_PAN)),
        "tilt": int(round(SERVO_TILT)),
        "serial_available": SERIAL_AVAILABLE
    }
