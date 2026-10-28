"""
ARIA Phone Bridge Subsystem.
Enables local network control, mobile voice messaging (iOS/Android),
live MJPEG cyber-face stream, and web dashboard over HTTP / TLS HTTPS.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import io
import wave
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
from http.cookies import SimpleCookie
from aria.bridge_sessions import BridgeSessions, SESSION_SECONDS
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Callable, Any

from aria.config import PHONE_BRIDGE_PORT, BRIDGE_TOKEN, ROOT_DIR, WORKSPACE_DIR, add_log, lan_ip, get_setting
from aria.vision import (
    get_face_frame_jpeg, publish_phone_frame, get_phone_frame_jpeg,
    get_phone_frame_status, describe_phone_view,
)
from aria.speech import tts_bytes_for_bridge, transcribe_audio
from aria.tools.schemas import COMMAND_GUIDE
from aria import inbox as inbox_mod
from aria import config as _config_silent

BRIDGE_SCHEME = "http"
BRIDGE_CERT: Optional[str] = None
BRIDGE_KEY: Optional[str] = None

_BRIDGE_PROCESS_CALL: Optional[Callable[[str, bool], str]] = None
_CHAT_LOG_CALL: Optional[Callable[[], Any]] = None
_BRIDGE_SERVER: Optional[ThreadingHTTPServer] = None
_BRIDGE_ACTUAL_SCHEME = None
_PAIR_LOCK = threading.Lock()
_PAIR_TOKEN = ""
_PAIR_EXPIRES = 0.0
_SESSION_STORE = None
_SESSION_STORE_LOCK = threading.Lock()


def phone_sessions():
    global _SESSION_STORE
    path = os.path.join(WORKSPACE_DIR, 'phone_sessions.json')
    with _SESSION_STORE_LOCK:
        if _SESSION_STORE is None or _SESSION_STORE.path != path:
            _SESSION_STORE = BridgeSessions(path)
        return _SESSION_STORE


def revoke_phone_sessions():
    phone_sessions().revoke()
    add_log('Phone bridge: remembered phones disconnected.')


def phone_connection_status():
    return phone_sessions().status()


def create_pairing_url():
    """Create one short-lived, single-use pairing link from the desktop UI."""
    global _PAIR_TOKEN, _PAIR_EXPIRES
    if _BRIDGE_SERVER is None:
        raise RuntimeError("The phone bridge is not running.")
    with _PAIR_LOCK:
        _PAIR_TOKEN = secrets.token_urlsafe(24)
        _PAIR_EXPIRES = time.monotonic() + 300
        base = get_bridge_url().split("?", 1)[0]
        return base.rstrip("/") + "/auto-login?pair=" + _PAIR_TOKEN


def _consume_pairing(token):
    global _PAIR_TOKEN, _PAIR_EXPIRES
    with _PAIR_LOCK:
        if not token or not _PAIR_TOKEN or time.monotonic() >= _PAIR_EXPIRES:
            return False
        if not hmac.compare_digest(token, _PAIR_TOKEN):
            return False
        _PAIR_TOKEN, _PAIR_EXPIRES = "", 0.0
        return True


# ── Phone node WebSocket (/node/ws) ─────────────────────────────
# The ARIA Node Android app connects OUT here and speaks the
# node protocol (hello / welcome / command / result / ping /
# pong — aria/node_hub.py). The bridge server is a stdlib
# BaseHTTPRequestHandler, so the WebSocket is a minimal RFC 6455
# implementation: handshake + whole frames (client frames are
# masked, fragments reassembled). One node per connection thread.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_frame(opcode: int, payload: bytes) -> bytes:
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


def _ws_read_frame(rfile):
    """One whole frame: (opcode, payload). Raises on EOF."""
    payload_all = b""
    opcode0 = None
    while True:
        head = rfile.read(2)
        if len(head) < 2:
            raise ConnectionError("websocket closed")
        fin = head[0] & 0x80
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        n = head[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", rfile.read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", rfile.read(8))[0]
        mask = rfile.read(4) if masked else b""
        data = rfile.read(n) if n else b""
        if len(data) < n:
            raise ConnectionError("websocket truncated")
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode0 is None:
            opcode0 = opcode
        payload_all += data
        if fin:
            return opcode0, payload_all


def _node_key_ok(key: str) -> bool:
    """A node hello's key must be one of the bridge's existing
    credentials — never a second secret: the bridge token, a
    remembered phone session token, or a fresh pairing token
    (spent exactly as the browser pairing spends it)."""
    if not key:
        return False
    if BRIDGE_TOKEN and hmac.compare_digest(key, BRIDGE_TOKEN):
        return True
    try:
        if phone_sessions().valid(key):
            return True
    except Exception:
        pass
    return _consume_pairing(key)


def _generate_silent_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(8000)
        wf.writeframes(b'\x80' * 8000)
    return buf.getvalue()

_SILENT_WAV_BYTES = _generate_silent_wav()


_BRIDGE_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIDNjCCAh6gAwIBAgIUEVjzN4XTbazT0YrhRlehUUQhkfYwDQYJKoZIhvcNAQEL
BQAwFjEUMBIGA1UEAwwLYXJpYS1icmlkZ2UwHhcNMjYwOTI2MTQyNjU0WhcNMzYw
OTIzMTQyNjU0WjAWMRQwEgYDVQQDDAthcmlhLWJyaWRnZTCCASIwDQYJKoZIhvcN
AQEBBQADggEPADCCAQoCggEBAKOKT7xoLl+vSM3wd0q4cFH9p5aS5/Y7VyRm7N86
Ur8LHzkIXkR+DzXN6CwqziljLe2Ak2DP8fecaO3Yp9AlGHgK8E9zM4RLHU9FYGhX
wF/Jqr7UxYeapPOg0p/tANPhFGOr1wuC9o2WmYdVKhRPKTfv/mLN0O/yOS+6a7F4
wgMR8LUg2t7g+A/P8tixGXXFl4bxxs+Ff1MxYGl5oy0ZXvGzAN08XCFoiFJ6z1Bs
SwkbuR7k15+mr/W1dt0uQ5le/m/hs9AW2DMNDag6T2hZy/42X6pXmEoH4lrnjOk0
ow7lI074/RU1LNARApkTKt2HblJrV3b6iEZ5NN1eUVh/CnkCAwEAAaN8MHowHQYD
VR0OBBYEFJUGhX33gWoxlZ9guh+wwgnl5JTzMB8GA1UdIwQYMBaAFJUGhX33gWox
lZ9guh+wwgnl5JTzMA8GA1UdEwEB/wQFMAMBAf8wJwYDVR0RBCAwHoILYXJpYS1i
cmlkZ2WCCWxvY2FsaG9zdIcEfwAAATANBgkqhkiG9w0BAQsFAAOCAQEAbFocytna
OgBNGqpq9ZQbwj03DlxmGalRlH1qAnGZac+zb3oGFkNxNBlCXjOTen4Gnfik93nO
4U0ucjS1VHYaIj1ydt8CB7SDxDPlmschvoNUA4QcsR7NctA3oPnnr5Mc2OIJwnbH
Pjc8cx+e/26A0KWuO9QWy3StU5FjNVHgbSelGH53jwPn6tcQufHaKLRbFMM59mvu
kIpDTE+OvlADfd1lm4o5Xfqf59hk5SKjfFtXZAmTshpoOQCwpNOyhxMUP292I3+i
ZsdcP2cpAXZufOWa7ILyhiHfTNvm8rbWMcOl6XlANiL1RQJlq0gtg+YN3NLO5l5A
AnshztnTBNrCdw==
-----END CERTIFICATE-----"""

_BRIDGE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCjik+8aC5fr0jN
8HdKuHBR/aeWkuf2O1ckZuzfOlK/Cx85CF5Efg81zegsKs4pYy3tgJNgz/H3nGjt
2KfQJRh4CvBPczOESx1PRWBoV8Bfyaq+1MWHmqTzoNKf7QDT4RRjq9cLgvaNlpmH
VSoUTyk37/5izdDv8jkvumuxeMIDEfC1INre4PgPz/LYsRl1xZeG8cbPhX9TMWBp
eaMtGV7xswDdPFwhaIhSes9QbEsJG7ke5Nefpq/1tXbdLkOZXv5v4bPQFtgzDQ2o
Ok9oWcv2Nl+qV5hKByJa54zpNKMMySNO+P0VNSzQEQKZFSrdh25Sa1d2+ohGeTTd
XlFYfwp5AgMBAAECggEAbqZ6mU0RkO6bX6b9N+k65xTz1u361g10x8E8k8y+1g9L
4X4d9Z3569i8l6s55A8A0s13i5aD+s0wA7+8Yw/e92v0y1m0k6b9k4h51j7l0+g0
a1u6b5y1p3m7+8y0A1k5y0y3m8w1z7z6k4h9k4b8w0v0y2j1A9g1y4k8w0g3u9k=
-----END PRIVATE KEY-----"""


def set_bridge_processor(fn: Callable[[str, bool], str]):
    global _BRIDGE_PROCESS_CALL
    _BRIDGE_PROCESS_CALL = fn


def set_chat_log_provider(fn: Callable[[], Any]):
    global _CHAT_LOG_CALL
    _CHAT_LOG_CALL = fn


def _der_len(n: int) -> bytes:
    if n < 128:
        return bytes((n,))
    lb = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(lb),)) + lb


def _der_int(raw: bytes) -> bytes:
    raw = raw.lstrip(b"\x00") or b"\x00"
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return b"\x02" + _der_len(len(raw)) + raw


def _der_read_len(buf: bytes, pos: int) -> tuple[int, int]:
    first = buf[pos]
    if first < 128:
        return first, pos + 1
    n = first & 0x7F
    return int.from_bytes(buf[pos + 1:pos + 1 + n], "big"), pos + 1 + n


def _der_read_seq_of_ints(der: bytes) -> list[int]:
    """Parse DER SEQUENCE of INTEGERs; raises on anything malformed."""
    pos = 0
    if der[pos] != 0x30:
        raise ValueError("DER: not a SEQUENCE")
    ln, pos = _der_read_len(der, pos + 1)
    end = pos + ln
    out = []
    while pos < end:
        if der[pos] != 0x02:
            raise ValueError("DER: expected INTEGER")
        iln, pos = _der_read_len(der, pos + 1)
        out.append(int.from_bytes(der[pos:pos + iln], "big"))
        pos += iln
    if pos != end:
        raise ValueError("DER: trailing bytes")
    return out


def _pem_wrap(der: bytes, label: str) -> bytes:
    b64 = base64.b64encode(der).decode("ascii")
    lines = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n".encode("ascii")


# Windows-only: build the self-signed cert with PowerShell/.NET so a broken
# `cryptography` install can't block per-machine cert generation. Emits 9
# base64 lines on stdout: cert DER, then RSA n/e/d/p/q/dp/dq/qinv.
# NOTE: -DnsName already creates the SAN extension; do NOT also pass
# -TextExtension with OID 2.5.29.17 (duplicate extension -> cmdlet throws).
_PS_CERT_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$cert=New-SelfSignedCertificate -DnsName __DNS_NAMES__ "
    "-CertStoreLocation 'Cert:\\CurrentUser\\My' -KeyExportPolicy Exportable "
    "-KeyLength 2048 -HashAlgorithm SHA256 -NotAfter (Get-Date).AddYears(10);"
    "try{"
    "[Convert]::ToBase64String($cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert));"
    "$p=[System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert).ExportParameters($true);"
    "[Convert]::ToBase64String($p.Modulus);"
    "[Convert]::ToBase64String($p.Exponent);"
    "[Convert]::ToBase64String($p.D);"
    "[Convert]::ToBase64String($p.P);"
    "[Convert]::ToBase64String($p.Q);"
    "[Convert]::ToBase64String($p.DP);"
    "[Convert]::ToBase64String($p.DQ);"
    "[Convert]::ToBase64String($p.InverseQ)"
    "}finally{"
    "try{"
    "$s=New-Object System.Security.Cryptography.X509Certificates.X509Store('My','CurrentUser');"
    "$s.Open('ReadWrite');$s.Remove($cert);$s.Close()"
    "}catch{}}"
)


def _generate_machine_cert_powershell(cert_p: str, key_p: str) -> bool:
    """Windows-only fallback: self-signed cert via PowerShell/.NET.

    Used when `cryptography` can't be imported (e.g. its native DLLs fail to
    load). Needs no third-party packages: New-SelfSignedCertificate ships with
    Windows 10/11. Raises on any problem; caller falls back to the bundled cert.
    """
    if sys.platform != "win32":
        return False
    # -DnsName entries become the cert's SANs; the current LAN IP
    # must be among them or the phone's browser rejects the cert for
    # the very address the bridge advertises (see _cert_covers_machine).
    dns_names = ["aria-bridge", "localhost"]
    try:
        import ipaddress as _ipmod
        cur = lan_ip()
        if cur and not _ipmod.ip_address(cur).is_loopback:
            dns_names.append(cur)
    except Exception:
        pass
    script = _PS_CERT_SCRIPT.replace(
        "__DNS_NAMES__", ",".join(f"'{n}'" for n in dns_names))
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip() or f"powershell exit {r.returncode}")[-300:])
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if len(lines) < 9:
        raise RuntimeError(f"unexpected powershell output ({len(lines)} lines)")
    cert_der = base64.b64decode(lines[-9])
    nums = [int.from_bytes(base64.b64decode(x), "big") for x in lines[-8:]]
    body = b"".join([_der_int(b"\x00")] +
                     [_der_int(n.to_bytes((n.bit_length() + 7) // 8 or 1, "big"))
                      for n in nums])
    key_der = b"\x30" + _der_len(len(body)) + body
    # Round-trip check: the DER must decode to version 0 + the 8 inputs.
    if _der_read_seq_of_ints(key_der) != [0] + nums:
        raise RuntimeError("generated key failed DER round-trip check")
    if not cert_der.startswith(b"\x30"):
        raise RuntimeError("generated cert is not DER")
    with open(cert_p, "wb") as f:
        f.write(_pem_wrap(cert_der, "CERTIFICATE"))
    with open(key_p, "wb") as f:
        f.write(_pem_wrap(key_der, "RSA PRIVATE KEY"))
    return True


def _generate_machine_cert(cert_p: str, key_p: str) -> bool:
    """Generate a unique self-signed cert for THIS machine.

    The old bundled cert/key was identical on every deployment, so anyone
    with the repo could MITM any ARIA instance. A per-machine cert stored
    in the (gitignored) workspace dir fixes that.

    Preferred path is `cryptography`; on Windows, if that is unavailable or
    broken, falls back to PowerShell/.NET so no third-party package is needed.
    """
    try:
        import ipaddress
        import datetime as _dt
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "aria-bridge")])
        # SANs must cover the addresses the phone actually uses: the
        # machine's CURRENT LAN IP above all — a cert naming only
        # 127.0.0.1 is rejected by the phone's browser at the LAN
        # address the bridge advertises.
        sans = [
            x509.DNSName("aria-bridge"),
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
        try:
            cur_ip = ipaddress.ip_address(lan_ip())
            if not cur_ip.is_loopback:
                sans.append(x509.IPAddress(cur_ip))
        except Exception:
            pass
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_dt.datetime.now(_dt.timezone.utc))
            .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName(sans),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        with open(key_p, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()))
        with open(cert_p, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except Exception as e:
        crypto_err = e
    # Windows fallback: PowerShell/.NET needs no third-party packages.
    if sys.platform == "win32":
        try:
            if _generate_machine_cert_powershell(cert_p, key_p):
                return True
        except Exception as e2:
            # Chunked so the GUI's narrow action stream shows all of it.
            msg = str(e2)[:300]
            add_log("Bridge: Windows native cert fallback failed:")
            for i in range(0, len(msg), 60):
                add_log("Bridge: > " + msg[i:i + 60])
    hint = (" - installed but its native libraries failed to load. Fix: reinstall "
            "with the SAME python that runs ARIA "
            "(python -m pip install --force-reinstall --no-cache-dir cryptography); "
            "if it persists, install the Microsoft Visual C++ Redistributable"
            if "dll" in str(crypto_err).lower() else "")
    add_log(f"Bridge: cert gen python: {sys.executable}")
    add_log(f"Bridge: per-machine cert generation failed ({crypto_err}){hint}")
    return False


def _cert_san_names(cert_path: str):
    """(dns_names, ip_strings) from a PEM cert's SAN extension, or
    None when the cert can't be parsed at all. Prefers
    `cryptography`; falls back to ssl's built-in decoder so the
    coverage check still works where cryptography is broken."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        try:
            san = cert.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        except Exception:
            return set(), set()
        return (set(san.get_values_for_type(x509.DNSName)),
                {str(ip) for ip in san.get_values_for_type(x509.IPAddress)})
    except Exception:
        pass
    try:
        info = ssl._ssl._test_decode_cert(cert_path)
        dns, ips = set(), set()
        for kind, val in info.get("subjectAltName", ()):
            if kind == "DNS":
                dns.add(val)
            elif kind == "IP Address":
                ips.add(val)
        return dns, ips
    except Exception:
        return None


def _cert_covers_machine(cert_path: str, ip: Optional[str] = None) -> bool:
    """True when the cert's SANs cover every name/address the bridge
    is reached by: aria-bridge + localhost (DNS), 127.0.0.1, and the
    machine's CURRENT LAN IP. The pre-fix generator emitted only
    the first three, and ensure_bridge_cert reused that cert
    forever — so the phone's browser rejected the cert at the LAN
    address even though the bridge was up. An unparseable cert
    counts as not covering (regenerate)."""
    names = _cert_san_names(cert_path)
    if names is None:
        return False
    dns, ips = names
    if not {"aria-bridge", "localhost"} <= dns:
        return False
    if "127.0.0.1" not in ips:
        return False
    cur = ip if ip is not None else lan_ip()
    if cur and cur != "localhost" and cur not in ips:
        return False
    return True


def ensure_bridge_cert():
    global BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY
    # 1. Prefer a per-machine cert in the workspace dir (unique, gitignored).
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        mcert, mkey = (os.path.join(WORKSPACE_DIR, "aria_bridge_machine.pem"),
                       os.path.join(WORKSPACE_DIR, "aria_bridge_machine_key.pem"))
        have = os.path.exists(mcert) and os.path.exists(mkey)
        if have and not _cert_covers_machine(mcert):
            # Stale cert (e.g. generated before a LAN IP change, or
            # by a pre-fix build without the LAN IP in its SANs):
            # regenerate into temp files and swap in only on success,
            # so a failed refresh keeps the old cert rather than
            # dropping the bridge to the bundled fallback.
            add_log("Bridge: existing machine cert does not cover this "
                    "machine's current LAN address — regenerating.")
            tmp_c, tmp_k = mcert + ".new", mkey + ".new"
            if _generate_machine_cert(tmp_c, tmp_k):
                os.replace(tmp_c, mcert)
                os.replace(tmp_k, mkey)
                add_log("Bridge: refreshed per-machine HTTPS cert for "
                        "the current LAN address.")
            else:
                add_log("Bridge: cert refresh failed — keeping the "
                        "existing cert.")
                for tmp in (tmp_c, tmp_k):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
        elif not have:
            if _generate_machine_cert(mcert, mkey):
                add_log("Bridge: generated unique per-machine HTTPS cert.")
        if os.path.exists(mcert) and os.path.exists(mkey):
            BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY = "https", mcert, mkey
            return
    except Exception as _e_silent:
        _config_silent.log_silent("ensure_bridge_cert", _e_silent)
    # 2. Fall back to the bundled cert (shared across deployments — weaker).
    add_log("Bridge: WARNING - using bundled shared cert; install 'cryptography' "
            "for a unique per-machine certificate.")
    dirs = [ROOT_DIR, tempfile.gettempdir()]
    for d in dirs:
        base = os.path.join(d, "aria_bridge_bundled")
        cert_p, key_p = base + ".pem", base + "_key.pem"
        try:
            cur = open(cert_p).read().strip() if os.path.exists(cert_p) else ""
            if cur != _BRIDGE_CERT_PEM.strip() or not os.path.exists(key_p):
                with open(cert_p, "w", encoding="utf-8") as f:
                    f.write(_BRIDGE_CERT_PEM.strip() + "\n")
                with open(key_p, "w", encoding="utf-8") as f:
                    f.write(_BRIDGE_KEY_PEM.strip() + "\n")
                add_log(f"Bridge: wrote bundled HTTPS cert ({d})")
            BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY = "https", cert_p, key_p
            return
        except Exception as _e_silent:
            _config_silent.log_silent("ensure_bridge_cert", _e_silent)
    BRIDGE_SCHEME = "http"
    add_log("Bridge: cert write failed - HTTP fallback.")


def _bridge_bind_host() -> str:
    """Bind host for the bridge server. Default 0.0.0.0 — the bridge
    exists so the PHONE can reach it, and every route stays
    token-gated, so binding wide does not weaken auth. (Pre-fix the
    default was 127.0.0.1 while the startup banner printed the LAN
    URL: the phone connected to an address nothing listened on.)
    The 'phone_bridge_host' setting overrides in either direction —
    set it to 127.0.0.1 to confine the bridge to this machine."""
    host = get_setting("phone_bridge_host", "0.0.0.0")
    return host or "0.0.0.0"


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _advertised_url(host: str, port: int,
                    scheme: Optional[str] = None) -> str:
    """The phone-ready URL to advertise for a given bind: the LAN
    address when bound wide, localhost when bound loopback — never
    a LAN URL for a bridge that isn't listening on the LAN. Carries
    the ?token= the root route exchanges for the auth cookie, so
    opening it on the phone just works."""
    shown = "127.0.0.1" if host in _LOOPBACK_HOSTS else lan_ip()
    base = f"{scheme or BRIDGE_SCHEME}://{shown}:{port}"
    if BRIDGE_TOKEN:
        return f"{base}/?token={urllib.parse.quote(BRIDGE_TOKEN, safe='')}"
    return base


def get_bridge_url() -> str:
    port = _BRIDGE_SERVER.server_port if _BRIDGE_SERVER else PHONE_BRIDGE_PORT
    return _advertised_url(_bridge_bind_host(), port, scheme=_BRIDGE_ACTUAL_SCHEME)



with open(os.path.join(os.path.dirname(__file__), 'assets', 'phone.html'), encoding='utf-8') as _phone_page:
    BRIDGE_HTML = _phone_page.read()


# Home-screen / PWA icons (Alek, 2026-10-28): the SAME waveform-orb art
# as the desktop shortcut icon (aria.ico), rendered to PNG and
# composited on the page's own #080d14. Served unauthenticated like
# /sw.js — iOS fetches the apple-touch-icon outside the pairing flow,
# and an icon is not a secret.
def _load_asset_bytes(name):
    try:
        with open(os.path.join(os.path.dirname(__file__), 'assets', name), 'rb') as f:
            return f.read()
    except OSError:
        return None


_ICON_BYTES = {name: _load_asset_bytes(name)
               for name in ('icon-180.png', 'icon-192.png', 'icon-512.png')}

MANIFEST_JSON = (
    '{"name":"A.R.I.A. Bridge","short_name":"A.R.I.A.","display":"standalone",'
    '"background_color":"#080d14","theme_color":"#080d14","start_url":"/",'
    '"icons":[{"src":"/icon-192.png?v=orb1","sizes":"192x192","type":"image/png","purpose":"any"},'
    '{"src":"/icon-512.png?v=orb1","sizes":"512x512","type":"image/png","purpose":"any"}]}'
)



PAIR_EXPIRED_HTML = """<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARIA | Pairing link expired</title><body style="background:#080d14;color:#e2edf5;font:16px system-ui;padding:40px;line-height:1.6">
<h1>Get a fresh connection link</h1><p>This QR code has expired or has already paired another phone.</p>
<p>On your laptop, open <strong>Remote Control</strong> and select <strong>Refresh QR</strong>, then scan again.</p>
<p><a style="color:#67dce5" href="/">Open this phone's existing connection</a></p></body></html>"""

BRIDGE_LOGIN_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>A.R.I.A. Bridge - Login</title>
<style>
:root{--acc:#64d9e7;--bg:#080d14;--card:#0e1823;--line:#213548;--txt:#e4edf6;--dim:#8ea3b6;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(ellipse at top,#102432,transparent 65%),var(--bg);color:var(--txt);font:15px/1.5 system-ui,sans-serif;padding:24px 16px}
#app{max-width:560px;margin:0 auto}
.eyebrow{font:11px ui-monospace,monospace;letter-spacing:2px;color:var(--acc);text-transform:uppercase}
h1{font-size:28px;letter-spacing:-1px;margin:6px 0}
p{color:var(--dim)}
form{display:flex;gap:10px;margin-top:24px}
input{flex:1;min-width:0;background:#101e2b;border:1px solid var(--line);color:var(--txt);border-radius:12px;padding:14px 16px;font-size:16px;outline:none}
input:focus{border-color:var(--acc)}
button{padding:14px 22px;border-radius:12px;border:0;background:var(--acc);color:#06212a;font-weight:700;font-size:16px;cursor:pointer}
</style></head><body>
<div id="app">
<div class="eyebrow">A.R.I.A. Bridge</div>
<h1>Connect this phone</h1>
<p>Enter your bridge token to connect. Find it in the ARIA console, or ask ARIA for it.</p>
<form onsubmit="login();return false"><input id="t" type="password" placeholder="Bridge token..."
autocomplete="off"><button>Connect</button></form>
</div>
<script>
async function login(){
  const t=document.getElementById('t').value;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:t})});
  if(r.ok){location.href='/';}
  else{document.getElementById('t').value='';alert('Bad bridge token.');}
  return false;
}
</script></body></html>"""



def _commands_html() -> str:
    parts = []
    last = None
    for cat, tool, ex in COMMAND_GUIDE:
        if cat != last:
            if last is not None:
                parts.append("</div>")
            parts.append(f"<h3>{cat}</h3><div class='grp'>")
            last = cat
        parts.append(f"<div class='cmd' data-t='{tool} {ex} {cat}'><b>{tool}</b><span>&quot;{ex}&quot;</span></div>")
    parts.append("</div>")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>A.R.I.A. Commands</title><style>"
        ":root{--acc:#64d9e7;--bg:#080d14;--card:#0e1823;--line:#213548;--txt:#e4edf6;--dim:#8ea3b6;color-scheme:dark}"
        "*{box-sizing:border-box}"
        "body{margin:0;background:radial-gradient(ellipse at top,#102432,transparent 65%),var(--bg);color:var(--txt);font:15px/1.5 system-ui,sans-serif;padding:24px 16px}"
        "#app{max-width:720px;margin:0 auto}"
        ".eyebrow{font:11px ui-monospace,monospace;letter-spacing:2px;color:var(--acc);text-transform:uppercase}"
        "h1{font-size:28px;letter-spacing:-1px;margin:6px 0 4px}"
        "h3{font:11px ui-monospace,monospace;letter-spacing:2px;color:var(--acc);text-transform:uppercase;margin:24px 0 8px}"
        ".cmd{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:8px}"
        ".cmd b{color:var(--txt);display:block}.cmd span{color:var(--dim);font-size:14px}"
        "#q{width:100%;background:#101e2b;border:1px solid var(--line);color:var(--txt);border-radius:12px;padding:14px 16px;font-size:16px;outline:none;margin:16px 0 4px}"
        "#q:focus{border-color:var(--acc)}"
        "a{color:var(--acc);text-decoration:none}</style></head><body>"
        "<div id='app'>"
        "<div class='eyebrow'>A.R.I.A. Bridge</div>"
        "<h1>Command reference</h1>"
        "<input id='q' placeholder='Filter commands...' oninput='f()'>"
        f"<div id='list'>{''.join(parts)}</div>"
        "<p><a href='/'>&larr; Bridge</a></p></div>"
        "<script>function f(){var q=document.getElementById('q').value.toLowerCase();"
        "document.querySelectorAll('.cmd').forEach(function(e){"
        "e.style.display=e.getAttribute('data-t').toLowerCase().indexOf(q)>=0?'':'none';});}</script></body></html>"
    )


UPLOAD_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1">
<title>A.R.I.A. Upload</title>
<style>
:root{--acc:#64d9e7;--bg:#080d14;--card:#0e1823;--line:#213548;--txt:#e4edf6;--dim:#8ea3b6;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(ellipse at top,#102432,transparent 65%),var(--bg);color:var(--txt);font:15px/1.5 system-ui,sans-serif;padding:24px 16px}
#app{max-width:560px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.eyebrow{font:11px ui-monospace,monospace;letter-spacing:2px;color:var(--acc);text-transform:uppercase}
h1{font-size:28px;letter-spacing:-1px;margin:6px 0 0}
h2{font-size:18px;margin:0 0 12px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
#drop{border:2px dashed var(--line);border-radius:14px;padding:28px 14px;text-align:center;color:var(--dim);cursor:pointer}
#drop.over{border-color:var(--acc);color:var(--txt)}
#file{position:absolute;left:-9999px}
#upbtn{padding:14px;border-radius:12px;border:0;background:var(--acc);color:#06212a;font-weight:700;font-size:16px;cursor:pointer;width:100%}
#upbtn:disabled{opacity:.5}
#status{font-size:14px;color:var(--dim);min-height:20px}
#status.ok{color:#78deb5}#status.err{color:#ffa3a7}
.item{font-size:14px;padding:8px 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:8px}
.item span:last-child{color:var(--dim);font-size:12px;white-space:nowrap}
a{color:var(--acc);text-decoration:none}
</style></head><body>
<div id="app">
<div class="eyebrow">A.R.I.A. Bridge</div>
<h1>Send a file</h1>
<div class="panel">
<div id="drop">Tap to pick photos or files<br><small>multiple allowed, up to 50 MB each</small></div>
<input id="file" type="file" multiple>
</div>
<button id="upbtn" type="button" disabled>Upload</button>
<div id="status"></div>
<div class="panel">
<h2>Received files</h2>
<div id="list"><div class="item"><span>Loading...</span><span></span></div></div>
</div>
<p><a href="/">&larr; Bridge</a></p>
</div>
<script>
var drop=document.getElementById('drop'),file=document.getElementById('file'),
    upbtn=document.getElementById('upbtn'),status=document.getElementById('status'),
    list=document.getElementById('list'),chosen=[];
drop.onclick=function(){file.click();};
file.onchange=function(){chosen=Array.prototype.slice.call(file.files);renderChosen();};
function renderChosen(){
  upbtn.disabled=!chosen.length;
  drop.innerHTML=chosen.length?chosen.map(function(f){return f.name;}).join('<br>'):'Tap to pick photos or files<br><small>multiple allowed, up to 50 MB each</small>';
}
function note(msg,cls){status.className=cls||'';status.textContent=msg;}
async function refresh(){
  try{
    var r=await fetch('/api/inbox');
    if(r.status===401){location.href='/';return;}
    var items=await r.json();
    list.innerHTML=items.length?items.map(function(f){
      return '<div class="item"><span>'+f.name+'</span><span>'+f.size+' &middot; '+f.when+'</span></div>';
    }).join(''):'<div class="item"><span>Empty - send something up.</span><span></span></div>';
  }catch(e){list.innerHTML='<div class="item"><span>Could not load inbox.</span><span></span></div>';}
}
upbtn.onclick=async function(){
  if(!chosen.length)return;
  upbtn.disabled=true;note('Uploading '+chosen.length+' file(s)...');
  var fd=new FormData();
  chosen.forEach(function(f){fd.append('files',f,f.name);});
  try{
    var r=await fetch('/api/upload',{method:'POST',body:fd});
    if(r.status===401){location.href='/';return;}
    var j=await r.json();
    if(j.ok){
      note('Saved: '+(j.saved.join(', ')||'nothing')+(j.skipped&&j.skipped.length?' - skipped: '+j.skipped.join(', '):''),'ok');
      chosen=[];file.value='';renderChosen();refresh();
    }else{note('Upload failed: '+(j.error||'unknown'),'err');}
  }catch(e){note('Upload failed: '+e,'err');}
  upbtn.disabled=!chosen.length;
};
refresh();
</script></body></html>"""


# Web Push service worker (served at /sw.js): shows the push payload
# as a notification and focuses the bridge on click. Static and
# secret-free, so it is served without auth — a service worker must
# be fetchable before the page's session exists on some browsers.
SW_JS = """self.addEventListener('push', function(event) {
  var data = {title: 'ARIA', body: ''};
  try { if (event.data) data = event.data.json(); } catch (e) {
    try { data = {title: 'ARIA', body: event.data.text()}; } catch (e2) {}
  }
  var title = data.title || 'ARIA';
  var options = {body: data.body || '', tag: data.tag || undefined,
                 data: {url: '/'}};
  event.waitUntil(self.registration.showNotification(title, options));
});
self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(function(list) {
    for (var i = 0; i < list.length; i++) {
      if ('focus' in list[i]) return list[i].focus();
    }
    if (clients.openWindow) return clients.openWindow('/');
  }));
});
"""


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        self.end_headers()

    def _token_from_request(self) -> str:
        # 1. Explicit header (API clients, page fetch calls).
        tok = self.headers.get("X-Bridge-Token", "")
        # 2. HttpOnly auth cookie (set by /api/login; sent automatically by
        #    <img> and <audio> tags, so media URLs carry no token).
        if not tok:
            for part in self.headers.get("Cookie", "").split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip() == "aria_bridge_token":
                        tok = urllib.parse.unquote(v.strip())
                        break
        # 3. Query param (legacy bookmarks; still validated, never generated
        #    by the page anymore).
        if not tok and "?" in self.path:
            qs = self.path.split("?", 1)[1]
            tok = urllib.parse.unquote_plus(
                dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("token", ""))
        return tok

    def _phone_session(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookies.get('aria_phone_session')
            return value.value if value else ''
        except Exception:
            return ''

    def _authed(self):
        if phone_sessions().valid(self._phone_session()):
            return True
        return bool(BRIDGE_TOKEN) and hmac.compare_digest(
            self._token_from_request(), BRIDGE_TOKEN)

    def _bridge_cookie(self) -> str:
        token = phone_sessions().issue()
        cookie = ("aria_phone_session=" + token
                  + f"; HttpOnly; Path=/; SameSite=Lax; Max-Age={SESSION_SECONDS}")
        if (_BRIDGE_ACTUAL_SCHEME or BRIDGE_SCHEME) == "https":
            cookie += "; Secure"
        return cookie

    def _login_redirect(self):
        try:
            cookie = self._bridge_cookie()
        except OSError:
            self._send(503, b'{"error":"Could not remember this phone. Check laptop storage, then refresh the QR code."}')
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()
        add_log('Phone bridge: phone paired and connected automatically.')

    def _handle_node_ws(self):
        """The phone node's WebSocket: hello (pairing key) →
        welcome → command/result pump into aria.node_hub.HUB.
        A bad, missing or misshapen hello closes with 4401 — the
        node's cue to ask for a fresh pairing."""
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._send(400, b'{"error":"websocket upgrade required"}')
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self._send(400, b'{"error":"missing Sec-WebSocket-Key"}')
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept(key))
        self.end_headers()
        self.close_connection = True
        conn = self.connection
        send_lock = threading.Lock()

        def _send(obj):
            frame = _ws_frame(0x1, json.dumps(obj).encode("utf-8"))
            with send_lock:
                conn.sendall(frame)

        def _close():
            try:
                with send_lock:
                    conn.sendall(_ws_frame(0x8, struct.pack("!H", 1000)))
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

        hub = None
        name = ""
        try:
            conn.settimeout(30)
            opcode, payload = _ws_read_frame(self.rfile)
            hello = None
            if opcode in (0x1, 0x2):
                try:
                    hello = json.loads(payload.decode("utf-8"))
                except Exception:
                    hello = None
            ok = isinstance(hello, dict) and hello.get("type") == "hello"
            if ok:
                name = str(hello.get("node") or "").strip()
                ok = bool(name) and _node_key_ok(str(hello.get("key") or ""))
            if not ok:
                try:
                    conn.sendall(_ws_frame(0x8, struct.pack("!H", 4401)))
                except Exception:
                    pass
                return
            from aria import node_hub
            hub = node_hub.HUB
            hub.register(name, kind=str(hello.get("kind") or ""),
                         capabilities=hello.get("capabilities") or [],
                         send=_send, close=_close)
            _send({"type": "welcome", "ok": True})
            add_log(f"Phone bridge: phone node '{name}' connected.")
            conn.settimeout(120)
            while True:
                opcode, payload = _ws_read_frame(self.rfile)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    with send_lock:
                        conn.sendall(_ws_frame(0xA, payload))
                    continue
                if opcode not in (0x1, 0x2):
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "result":
                    hub.handle_result(msg)
                elif msg.get("type") == "ping":
                    _send({"type": "pong"})
        except Exception:
            pass
        finally:
            if hub is not None and name:
                hub.unregister(name, send=_send)
                add_log(f"Phone bridge: phone node '{name}' disconnected.")
            try:
                conn.close()
            except Exception:
                pass

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/node/ws":
            self._handle_node_ws()
            return
        if self.path.startswith("/api/"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            if self.path.split('?', 1)[0] == '/api/briefing':
                from aria.briefing import latest_briefing
                self._send(200, json.dumps({'report': latest_briefing()}).encode('utf-8'))
                return
            if self.path.split('?', 1)[0] == '/api/status':
                from aria import hud
                from aria.agent import providers
                state = str(getattr(hud, 'CURRENT_STATE', 'idle'))
                try:
                    from aria import applock as _applock_st
                    _locked = bool(_applock_st.is_locked())
                except Exception:
                    _locked = False
                self._send(200, json.dumps({'connected': True, 'state': state,
                    'provider': providers.get_active_provider() or 'Waiting for first response',
                    'transport': _BRIDGE_ACTUAL_SCHEME or BRIDGE_SCHEME,
                    'locked': _locked}).encode('utf-8'))
                return
            if self.path.split('?', 1)[0] == '/api/mail':
                from aria.mailview import fetch_message, fetch_messages
                uid = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('uid', [''])[0]
                result = fetch_message(uid) if uid else fetch_messages(limit=30)
                self._send(200 if result.get('ok') else 400, json.dumps(result).encode('utf-8'))
                return
            if self.path.startswith("/api/log"):
                logs = _CHAT_LOG_CALL() if _CHAT_LOG_CALL else []
                self._send(200, json.dumps(logs[-30:]).encode("utf-8"))
                return
            if self.path.startswith("/api/camstatus"):
                self._send(200, json.dumps(get_phone_frame_status()).encode("utf-8"))
                return
            if self.path.startswith("/api/inbox"):
                files = inbox_mod.list_inbox()
                self._send(200, json.dumps(
                    [{"name": f["name"], "size": f["size_h"], "when": f["when"]}
                     for f in files]).encode("utf-8"))
                return
            if self.path.split('?', 1)[0] == '/api/push/vapid':
                from aria import notify as _notify_mod
                self._send(200, json.dumps({
                    'public_key': _notify_mod.vapid_public_key(),
                    'webpush_available': _notify_mod.webpush_available(),
                }).encode('utf-8'))
                return
            if self.path.split('?', 1)[0] == '/api/notifications':
                from aria import notify as _notify_mod
                self._send(200, json.dumps({
                    'notifications': _notify_mod.queued(),
                    'status': _notify_mod.status(),
                }).encode('utf-8'))
                return
            if self.path.startswith("/api/say"):
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                text = urllib.parse.unquote_plus(params.get("text", ""))
                if not text.strip():
                    self._send(400, b'{"error":"missing text"}')
                    return
                try:
                    audio, ctype = tts_bytes_for_bridge(text[:500])
                except Exception as e:
                    add_log(f"Bridge TTS failed: {e}")
                    self._send(500, json.dumps(
                        {"error": f"tts unavailable: {str(e)[:150]}"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("X-TTS-Engine", "edge" if ctype == "audio/mpeg" else "sapi")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Expose-Headers", "X-TTS-Engine")
                self.end_headers()
                self.wfile.write(audio)
                return
            self._send(404, b'{"error":"not found"}')
            return

        if self.path.split("?", 1)[0] == "/sw.js":
            self._send(200, SW_JS.encode("utf-8"),
                       "application/javascript; charset=utf-8")
            return

        _clean_path = self.path.split("?", 1)[0]
        if _clean_path == "/manifest.webmanifest":
            self._send(200, MANIFEST_JSON.encode("utf-8"),
                       "application/manifest+json; charset=utf-8")
            return
        if _clean_path in ("/icon-180.png", "/icon-192.png", "/icon-512.png",
                           "/apple-touch-icon.png",
                           "/apple-touch-icon-precomposed.png"):
            # iOS also probes the well-known /apple-touch-icon.png at
            # the root on its own; answer it with the 180 icon so the
            # orb lands even if the page's link tag was cached stale.
            _icon_name = ("icon-180.png" if _clean_path.startswith("/apple")
                          else _clean_path.lstrip("/"))
            _icon = _ICON_BYTES.get(_icon_name)
            if _icon:
                self._send(200, _icon, "image/png")
            else:
                self._send(404, b'{"error":"icon missing"}')
            return

        if self.path.startswith("/silent.wav"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(_SILENT_WAV_BYTES)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(_SILENT_WAV_BYTES)
            return

        if self.path.startswith("/phone_cam.mjpg"):
            # Live view of what ARIA's phone camera sees (needs Camera: ON).
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            if not get_phone_frame_jpeg():
                self._send(404, b'{"error":"no camera frames yet - turn on Camera in settings"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while self._authed():
                    jpg = get_phone_frame_jpeg()
                    if jpg:
                        chunk = (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.write(chunk)
                    time.sleep(0.25)
            except Exception:
                return

        if self.path.startswith("/face.mjpg"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while self._authed():
                    jpg = get_face_frame_jpeg()
                    if jpg:
                        chunk = (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.write(chunk)
                    time.sleep(0.08)
            except Exception:
                return

        path = self.path.split("?", 1)[0]
        if path in ("/", "/auto-login"):
            pair = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("pair", [""])[0]
            if pair:
                if self._authed():
                    _consume_pairing(pair)
                    self.send_response(303)
                    self.send_header('Location', '/')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if not _consume_pairing(pair):
                    self._send(401, PAIR_EXPIRED_HTML.encode('utf-8'), 'text/html; charset=utf-8')
                    return
                self._login_redirect()
                return
            # One-time upgrade FIRST: a valid ?token= becomes a cookie and
            # redirects to the clean URL, so the page's API calls (cookie /
            # header auth only) stay authed. Must run before _authed() because
            # the query param feeds _authed() - otherwise this branch is dead.
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            qtok = urllib.parse.unquote_plus(
                dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("token", ""))
            if BRIDGE_TOKEN and qtok and qtok == BRIDGE_TOKEN:
                self._login_redirect()
                return
            if self._authed():
                self._send(200, BRIDGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(200, BRIDGE_LOGIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/commands":
            if not self._authed():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._send(200, _commands_html().encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/upload":
            if not self._authed():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._send(200, UPLOAD_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return

        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path == '/api/briefing':
            if not self._authed():
                self._send(401, b'{"error":"not connected"}')
                return
            from aria.briefing import build_briefing
            self._send(200, json.dumps({'report': build_briefing()}).encode('utf-8'))
            return
        if self.path == '/api/interrupt':
            if not self._authed():
                self._send(401, b'{"error":"not connected"}')
                return
            from aria.speech import interrupt_speech
            interrupt_speech()
            self._send(200, b'{"ok":true}')
            return
        if self.path == '/api/lock':
            if not self._authed():
                self._send(401, b'{"error":"not connected"}')
                return
            # Phone-initiated app lock (Alek, 2026-10-27): the
            # phone is the exempt path, so it can also LOCK the
            # laptop — this flips the same shared state as the
            # Settings Lock button, and the dashboard's lock
            # watcher does the gray-out. Refused when no lock
            # password is set: unlocking needs that password, so
            # locking without one would strand the laptop until a
            # restart (the Settings flow never allows it either).
            from aria import applock as _applock_br
            if not _applock_br.password_is_set():
                self._send(409, b'{"error":"no lock password set"}')
                return
            _applock_br.lock()
            self._send(200, b'{"ok":true,"locked":true}')
            return
        if self.path == '/api/logout':
            if not self._authed():
                self._send(401, b'{"error":"not connected"}')
                return
            token = self._phone_session()
            try:
                if token:
                    phone_sessions().revoke(token)
            except OSError:
                self._send(503, b'{"error":"Could not disconnect this phone. Try again."}')
                return
            self.send_response(303)
            self.send_header('Set-Cookie', 'aria_phone_session=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0')
            self.send_header('Set-Cookie', 'aria_bridge_token=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0')
            self.send_header('Location', '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path.startswith("/api/login"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length < 0 or length > 4096:
                    self._send(413, b'{"error":"login too large"}')
                    return
            except ValueError:
                self._send(400, b'{"error":"bad content length"}')
                return
            try:
                tok = json.loads(self.rfile.read(length).decode("utf-8")).get("token", "")
            except Exception:
                tok = ""
            if BRIDGE_TOKEN and tok == BRIDGE_TOKEN:
                try:
                    cookie = self._bridge_cookie()
                except OSError:
                    self._send(503, b'{"error":"Could not remember this phone"}')
                    return
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(body)
            else:
                if BRIDGE_TOKEN:
                    add_log("Bridge: failed login attempt.")
                self._send(401, b'{"error":"bad bridge token"}')
            return
        if self.path == '/api/push/subscribe':
            if not self._authed():
                self._send(401, b'{"error":"not connected"}')
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if length < 0 or length > 16384:
                self._send(413, b'{"error":"subscription too large"}')
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = None
            sub = body.get("subscription") if isinstance(body, dict) \
                and isinstance(body.get("subscription"), dict) else body
            from aria import notify as _notify_mod
            if _notify_mod.add_subscription(sub):
                self._send(200, b'{"ok":true}')
            else:
                self._send(400, b'{"error":"not a push subscription"}')
            return
        if self.path == '/api/notifications/dismiss':
            if not self._authed():
                self._send(401, b'{"error":"not connected"}')
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if length < 0 or length > 4096:
                self._send(413, b'{"error":"dismiss body too large"}')
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) \
                    if length else {}
            except Exception:
                body = {}
            ids = body.get("ids", body.get("id")) if isinstance(body, dict) else None
            from aria import notify as _notify_mod
            removed = _notify_mod.dismiss(ids)
            self._send(200, json.dumps(
                {"ok": True, "removed": removed}).encode("utf-8"))
            return
        if not (self.path.startswith("/api/ask") or self.path.startswith("/api/voice")
                or self.path.startswith("/api/camframe") or self.path.startswith("/api/look")
                or self.path.startswith("/api/upload")):
            self.send_response(404)
            self.end_headers()
            return
        if not self._authed():
            self._send(401, b'{"error":"bad or missing bridge token"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        if self.path.startswith("/api/upload"):
            # Files from the bridge /upload page land in ARIA's inbox.
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                self._send(400, b'{"error":"multipart/form-data required"}')
                return
            if length > inbox_mod.MAX_UPLOAD_BYTES:
                self._send(413, b'{"error":"upload too large (100 MB cap)"}')
                return
            body = self.rfile.read(length)
            try:
                parts = inbox_mod.parse_multipart(body, ctype)
            except Exception as e:
                add_log(f"Bridge upload parse failed: {e}")
                self._send(400, b'{"error":"could not parse upload"}')
                return
            if not parts:
                self._send(400, b'{"error":"no files in upload"}')
                return
            saved, skipped = [], []
            for fname, data in parts:
                if not data:
                    skipped.append(fname or "unnamed")
                    continue
                if len(data) > inbox_mod.MAX_FILE_BYTES:
                    skipped.append(fname or "unnamed")
                    continue
                try:
                    saved.append(inbox_mod.save_upload(fname, data))
                except Exception as e:
                    add_log(f"Bridge upload save failed: {e}")
                    skipped.append(fname or "unnamed")
            add_log(f"Bridge upload: saved {len(saved)}, skipped {len(skipped)}")
            self._send(200, json.dumps(
                {"ok": True, "saved": saved, "skipped": skipped}).encode("utf-8"))
            return
        if self.path.startswith("/api/look"):
            # Snapshot + describe: what does ARIA's camera see right now?
            try:
                reply = describe_phone_view()
            except Exception as e:
                self._send(500, json.dumps({"error": f"look failed: {e}"}).encode("utf-8"))
                return
            self._send(200, json.dumps({"reply": reply}).encode("utf-8"))
            return
        if self.path.startswith("/api/camframe"):
            # Camera frame uploaded by the bridge page (ARIA_BODY_CAMERA=bridge).
            if length > 1_000_000:
                self._send(413, b'{"error":"frame too large"}')
                return
            body = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
            if ctype not in ("image/jpeg", "application/octet-stream") \
                    or not publish_phone_frame(body):
                self._send(400, b'{"error":"bad frame"}')
                return
            self._send(200, b'{"ok":true}')
            return
        if self.path.startswith("/api/voice"):
            audio_in = self.rfile.read(length)
            mime = self.headers.get("Content-Type", "audio/webm")
            try:
                text = transcribe_audio(audio_in, mime)
            except Exception as e:
                self._send(500, json.dumps({"error": f"transcribe failed: {e}"}).encode("utf-8"))
                return
            add_log(f"Bridge voice: {text[:30]}")
            reply = _BRIDGE_PROCESS_CALL(text, True) if _BRIDGE_PROCESS_CALL else ""
            try:
                audio_out, ctype = tts_bytes_for_bridge(reply[:2000])
            except Exception as e:
                add_log(f"Bridge TTS failed: {e}")
                self._send(500, json.dumps({"error": f"tts failed: {str(e)[:150]}", "reply": reply[:500]}).encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(audio_out)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("X-TTS-Engine", "edge" if ctype == "audio/mpeg" else "sapi")
            self.send_header("X-Transcript", urllib.parse.quote(text[:300]))
            self.send_header("X-Reply", urllib.parse.quote(reply[:500]))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Expose-Headers", "X-TTS-Engine, X-Transcript, X-Reply")
            self.end_headers()
            self.wfile.write(audio_out)
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            text = payload.get("text", "")
            want_audio = bool(payload.get("want_audio", False))
        except Exception:
            text = ""
            want_audio = False

        if text.strip():
            add_log(f"Bridge directive: {text[:25]}")
            reply = _BRIDGE_PROCESS_CALL(text.strip(), True) if _BRIDGE_PROCESS_CALL else ""
        else:
            reply = ""

        # Only synthesize when the phone asked for audio (its Speak toggle).
        # Unconditional TTS here added seconds of latency to every message
        # and burned Edge calls for users who muted speech.
        audio_b64 = ""
        audio_mime = ""
        if reply.strip() and want_audio:
            try:
                audio_out, audio_mime = tts_bytes_for_bridge(reply[:2000])
                if audio_out:
                    audio_b64 = base64.b64encode(audio_out).decode("ascii")
            except Exception as e:
                add_log(f"Bridge ask TTS failed: {e}")
                audio_mime = ""

        self._send(200, json.dumps(
            {"reply": [reply], "audio": audio_b64, "audio_mime": audio_mime}
        ).encode("utf-8"))


def start_bridge_server(port: int = PHONE_BRIDGE_PORT) -> ThreadingHTTPServer:
    global _BRIDGE_SERVER, _BRIDGE_ACTUAL_SCHEME
    ensure_bridge_cert()
    # Bind host: 0.0.0.0 by default (the phone must be able to reach
    # it; every route requires the bridge token regardless). The
    # "phone_bridge_host" setting overrides in either direction.
    host = _bridge_bind_host()
    srv = ThreadingHTTPServer((host, port), BridgeHandler)
    online_scheme = BRIDGE_SCHEME
    if BRIDGE_SCHEME == "https" and BRIDGE_CERT and BRIDGE_KEY:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=BRIDGE_CERT, keyfile=BRIDGE_KEY)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        except Exception as e:
            add_log(f"Bridge TLS wrap failed: {e}")
            print(f"[ARIA] Bridge TLS wrap failed: {e}", flush=True)
            online_scheme = "http"  # the socket stayed plain
    _BRIDGE_ACTUAL_SCHEME = online_scheme
    _BRIDGE_SERVER = srv
    # Advertise the address ACTUALLY bound, in its phone-ready
    # (token-carrying) form — never a LAN URL for a localhost bind.
    url = _advertised_url(host, port, scheme=online_scheme)
    kind = "HTTPS TLS" if online_scheme == "https" else "HTTP"
    add_log(f"Phone bridge ({kind}): {url}")
    print(f"[ARIA] Phone bridge online: {url}", flush=True)
    if host in _LOOPBACK_HOSTS:
        warn = ("Bridge is bound to localhost only — a phone cannot "
                "reach it. Set phone_bridge_host to 0.0.0.0 to use "
                "the phone bridge.")
        add_log(f"Bridge: {warn}")
        print(f"[ARIA] {warn}", flush=True)
    try:
        srv.serve_forever()
    except Exception as e:
        add_log(f"Bridge server stopped: {e}")
    return srv
