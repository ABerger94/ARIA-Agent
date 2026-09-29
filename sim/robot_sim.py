#!/usr/bin/env python3
"""
Virtual ARIA robot body — a software stand-in for the Arduino firmware
(arduino/aria_body/aria_body.ino).

Lets you test ARIA's move_head / drive_wheels / body_stop tools end-to-end
with zero hardware. Run it on any machine, then point ARIA at it:

    python sim/robot_sim.py --port 9999

    # in another terminal (same machine or across the LAN):
    set ARIA_BODY_SERIAL_URL=socket://127.0.0.1:9999   (Windows)
    export ARIA_BODY_SERIAL_URL=socket://127.0.0.1:9999  (Linux/macOS)

ARIA's init_hardware() opens socket:// URLs via pyserial, so the agent talks
to this simulator exactly like it talks to the real Nano.

Protocol (mirrors the firmware):
    P<pan>T<tilt>\\n    head move, pan 0-180, tilt 0-90
    W<left>,<right>\\n  wheels, each -100..100
    S\\n               stop everything
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from datetime import datetime


class VirtualBody:
    def __init__(self) -> None:
        self.pan = 90
        self.tilt = 45
        self.left = 0
        self.right = 0
        self.lock = threading.Lock()

    def _stamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _wheels_ascii(self) -> str:
        def arrow(v: int) -> str:
            if v > 0:
                return "▲"
            if v < 0:
                return "▼"
            return "·"

        with self.lock:
            l, r = self.left, self.right
        return f"[L{arrow(l)}{l:+4d} R{arrow(r)}{r:+4d}]"

    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if line.startswith("P"):
            try:
                t = line.index("T")
                pan = max(0, min(180, int(line[1:t])))
                tilt = max(0, min(90, int(line[t + 1:])))
                with self.lock:
                    self.pan, self.tilt = pan, tilt
                print(f"[{self._stamp()}] HEAD  pan={pan:3d} tilt={tilt:2d}", flush=True)
            except ValueError:
                print(f"[{self._stamp()}] BAD head cmd: {line!r}", flush=True)
        elif line.startswith("W"):
            try:
                left_s, right_s = line[1:].split(",")
                left = max(-100, min(100, int(left_s)))
                right = max(-100, min(100, int(right_s)))
                with self.lock:
                    self.left, self.right = left, right
                print(f"[{self._stamp()}] DRIVE {self._wheels_ascii()}", flush=True)
            except ValueError:
                print(f"[{self._stamp()}] BAD drive cmd: {line!r}", flush=True)
        elif line == "S":
            with self.lock:
                self.left = self.right = 0
            print(f"[{self._stamp()}] STOP  {self._wheels_ascii()}", flush=True)
        else:
            print(f"[{self._stamp()}] UNKNOWN: {line!r}", flush=True)


def serve_client(conn: socket.socket, body: VirtualBody) -> None:
    with conn:
        buf = b""
        try:
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    body.handle_line(raw.decode("utf-8", "replace"))
        except ConnectionResetError:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Virtual ARIA robot body (Arduino simulator)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()

    body = VirtualBody()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(5)
    print(f"Virtual ARIA body listening on {args.host}:{args.port}")
    print("Protocol: P<pan>T<tilt> | W<left>,<right> | S   (Ctrl+C to quit)", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            print(f"[{body._stamp()}] ARIA connected from {addr[0]}", flush=True)
            threading.Thread(target=serve_client, args=(conn, body), daemon=True).start()
    except KeyboardInterrupt:
        print("\nVirtual body shutting down.")


if __name__ == "__main__":
    main()
