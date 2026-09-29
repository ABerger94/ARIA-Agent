"""
ARIA Phone Bridge Subsystem.
Enables local network control, mobile voice messaging (iOS/Android),
live MJPEG cyber-face stream, and web dashboard over HTTP / TLS HTTPS.
"""

from __future__ import annotations

import base64
import io
import wave
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Callable, Any

from aria.config import PHONE_BRIDGE_PORT, BRIDGE_TOKEN, ROOT_DIR, WORKSPACE_DIR, add_log, lan_ip, get_setting
from aria.vision import get_face_frame_jpeg, publish_phone_frame
from aria.speech import tts_bytes_for_bridge, transcribe_audio
from aria.tools.schemas import COMMAND_GUIDE

BRIDGE_SCHEME = "http"
BRIDGE_CERT: Optional[str] = None
BRIDGE_KEY: Optional[str] = None

_BRIDGE_PROCESS_CALL: Optional[Callable[[str, bool], str]] = None
_CHAT_LOG_CALL: Optional[Callable[[], Any]] = None
_BRIDGE_SERVER: Optional[ThreadingHTTPServer] = None

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
    "$cert=New-SelfSignedCertificate -DnsName 'aria-bridge','localhost' "
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
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", _PS_CERT_SCRIPT],
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
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_dt.datetime.now(_dt.timezone.utc))
            .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("aria-bridge"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]),
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


def ensure_bridge_cert():
    global BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY
    # 1. Prefer a per-machine cert in the workspace dir (unique, gitignored).
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        mcert, mkey = (os.path.join(WORKSPACE_DIR, "aria_bridge_machine.pem"),
                       os.path.join(WORKSPACE_DIR, "aria_bridge_machine_key.pem"))
        if not (os.path.exists(mcert) and os.path.exists(mkey)):
            if _generate_machine_cert(mcert, mkey):
                add_log("Bridge: generated unique per-machine HTTPS cert.")
        if os.path.exists(mcert) and os.path.exists(mkey):
            BRIDGE_SCHEME, BRIDGE_CERT, BRIDGE_KEY = "https", mcert, mkey
            return
    except Exception:
        pass
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
        except Exception:
            pass
    BRIDGE_SCHEME = "http"
    add_log("Bridge: cert write failed - HTTP fallback.")


def get_bridge_url() -> str:
    return f"{BRIDGE_SCHEME}://{lan_ip()}:{PHONE_BRIDGE_PORT}"



BRIDGE_HTML = """<!DOCTYPE html><html><head><meta name="viewport"
content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>A.R.I.A. Bridge</title>
<style>body{background:#0b0e12;color:#e8f4ff;font-family:sans-serif;margin:0;padding:16px}
h2{color:#ff5fa2;margin-top:0}#log{border:1px solid #2a3138;border-radius:8px;padding:10px;
height:42vh;overflow-y:auto;margin-bottom:12px;font-size:14px}
.you{color:#ff5fa2}.aria{color:#28f078}
form{display:flex;gap:8px;margin-bottom:10px}input{flex:1;padding:12px;border-radius:8px;border:1px
solid #2a3138;background:#14181d;color:#fff;font-size:16px}
button{padding:12px 18px;border-radius:8px;border:0;background:#ff5fa2;color:#fff;
font-weight:bold;font-size:16px;cursor:pointer}#talk{width:100%;padding:16px;touch-action:none;
user-select:none;-webkit-user-select:none}
.btn-row{display:flex;gap:8px;margin-top:8px}
.secondary-btn{flex:1;background:#1e242b;color:#e8f4ff;font-size:13px;padding:10px;border-radius:8px;border:1px solid #2a3138}
</style></head><body>
<h2>A.R.I.A. // Phone Bridge</h2>
<div style="text-align:center;margin-bottom:12px"><img id="face" alt="A.R.I.A." style="border-radius:12px;max-width:100%;width:320px;border:1px solid #2a3138"></div>
<div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:center">
  <a href="/commands" style="color:#ff5fa2;font-size:14px">Command reference</a>
  <span id="astat" style="font-size:12px;color:#8ba2b5">Audio: ready</span>
</div>
<div id="log"></div>
<form id="msgform">
  <input id="t" placeholder="Directive..." autocomplete="off">
  <button type="submit" id="sendbtn">Send</button>
</form>
<button id="talk">Hold to talk</button>
<div class="btn-row">
  <button id="spk" type="button" class="secondary-btn">Speak replies: ON</button>
  <button id="testspk" type="button" class="secondary-btn">Test Audio</button>
  <button id="cam" type="button" class="secondary-btn">Camera: OFF</button>
</div>
<audio id="aria-audio" playsinline webkit-playsinline preload="auto" style="display:none"></audio>
<audio id="aria-bg" loop playsinline webkit-playsinline preload="auto" style="display:none" src="/silent.wav"></audio>
<script>
async function api(path,opts){
  opts=opts||{};
  const r=await fetch(path,opts);
  if(r.status===401){ location.href='/'; }
  return r;
}
let spkOn=localStorage.getItem('spk')!=='0';
function updateSpkBtn(){document.getElementById('spk').innerText='Speak replies: '+(spkOn?'ON':'OFF');}
updateSpkBtn();

let actx=null,curSrc=null,voiceErrT=null;

function b64ToArrayBuffer(b64){
  const bin=window.atob(b64);
  const len=bin.length;
  const bytes=new Uint8Array(len);
  for(let i=0;i<len;i++){bytes[i]=bin.charCodeAt(i);}
  return bytes.buffer;
}

function ensureAudio(){
  if(!actx){
    try{
      const AC=window.AudioContext||window.webkitAudioContext;
      if(AC)actx=new AC();
    }catch(e){console.log('actx init err',e);}
  }
  if(actx&&(actx.state==='suspended'||actx.state==='interrupted')){
    try{actx.resume().catch(()=>{});}catch(e){}
  }
  return actx;
}

function setAudioSessionType(t){
  try{
    if(navigator.audioSession){navigator.audioSession.type=t;}
  }catch(e){}
}

function unlockAudio(){
  try{
    const bg=document.getElementById('aria-bg');
    if(bg&&bg.paused){
      bg.play().catch(()=>{});
    }
  }catch(e){}
  // NOTE: the audio session category is NOT set here. It is set at the
  // point of use instead — 'playback' in playAudio(), 'playAndRecord'
  // around hold-to-talk capture. Setting it globally per-gesture raced
  // with getUserMedia and broke recording on iOS.
  try{
    const ctx=ensureAudio();
    if(ctx){
      if(ctx.state==='suspended'||ctx.state==='interrupted'){
        ctx.resume().catch(()=>{});
      }
      const b=ctx.createBuffer(1,1,22050);
      const s=ctx.createBufferSource();
      s.buffer=b;
      s.connect(ctx.destination);
      s.start(0);
    }
  }catch(e){}
  const st=document.getElementById('astat');
  if(st&&st.innerText.indexOf('error')===-1)st.innerText='Audio: active';
}

['touchstart','touchend','pointerdown','click','keydown'].forEach(evt=>{
  document.addEventListener(evt,unlockAudio,{passive:true});
});

let lastVoiceErr='';
function voiceError(msg,detail){
  lastVoiceErr=detail||msg||'';
  const st=document.getElementById('astat');
  if(st)st.innerText='Audio error: '+(msg||'tap Speak for details');
  const b=document.getElementById('spk');
  b.innerText='Speak replies: ERROR - tap for details';
  // Sticky on purpose: the old auto-clear hid total TTS outages behind
  // "Audio: ready". Clears on next successful playback or when tapped.
  clearTimeout(voiceErrT);
  b.onclick=()=>{
    alert('Voice failed. '+(lastVoiceErr?('PC says: '+lastVoiceErr+' '):'')+
      'On the PC, check the ARIA action stream for the line starting '+
      '"Bridge TTS failed:" — it names the exact synthesis error.');
    clearVoiceError();
  };
}
function clearVoiceError(){
  lastVoiceErr='';
  const b=document.getElementById('spk');
  clearTimeout(voiceErrT);
  b.onclick=spkToggle;
  updateSpkBtn();
  const st=document.getElementById('astat');
  if(st)st.innerText='Audio: ready';
}

function spkToggle(){
  spkOn=!spkOn;
  localStorage.setItem('spk',spkOn?'1':'0');
  updateSpkBtn();
  if(spkOn)unlockAudio();
}
document.getElementById('spk').onclick=spkToggle;

function playViaWebAudio(buf){
  return new Promise((resolve,reject)=>{
    const ctx=ensureAudio();
    if(!ctx){reject(new Error('no audio context'));return;}
    if(ctx.state==='suspended'||ctx.state==='interrupted'){
      ctx.resume().catch(()=>{});
    }
    if(curSrc){try{curSrc.stop();}catch(e){}curSrc=null;}
    const copy=buf.slice(0);
    ctx.decodeAudioData(copy,(decoded)=>{
      try{
        const src=ctx.createBufferSource();
        src.buffer=decoded;
        src.connect(ctx.destination);
        src.onended=()=>{curSrc=null;resolve();};
        src.start(0);
        curSrc=src;
      }catch(err){reject(err);}
    },(err)=>{reject(err);});
  });
}

function playAudio(buf,mime){
  return new Promise((resolve)=>{
    if(!spkOn||!buf||!buf.byteLength){resolve();return;}
    // iOS: 'playback' keeps output audible with the Ring/Silent switch on.
    setAudioSessionType('playback');
    unlockAudio();
    const st=document.getElementById('astat');
    if(st)st.innerText='Audio: playing...';

    let resolved=false;
    const finish=(ok)=>{
      if(!resolved){
        resolved=true;
        if(ok){clearVoiceError();}
        else if(st)st.innerText='Audio: ready';
        resolve();
      }
    };

    const player=document.getElementById('aria-audio');
    let blobUrl=null;
    try{
      // Use the server's real content type. The old hardcoded
      // 'audio/mpeg' mislabeled SAPI WAV bytes and broke the primary
      // playback path whenever Edge was down.
      const blob=new Blob([buf],{type:mime||'audio/mpeg'});
      blobUrl=URL.createObjectURL(blob);
      player.src=blobUrl;

      const cleanupPlayer=()=>{
        if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}
        player.onended=null;
        player.onerror=null;
        finish(true);
      };

      player.onended=cleanupPlayer;
      player.onerror=()=>{
        if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}
        player.onended=null;
        player.onerror=null;
        playViaWebAudio(buf).then(()=>finish(true)).catch((e)=>{
          console.log('web audio fallback error',e);
          voiceError('playback failed',String(e&&e.message||e));
          finish(false);
        });
      };

      const p=player.play();
      if(p!==undefined){
        p.catch((err)=>{
          console.log('HTMLAudio play rejected, using WebAudio fallback',err);
          if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}
          player.onended=null;
          player.onerror=null;
          playViaWebAudio(buf).then(()=>finish(true)).catch((e)=>{
            console.log('web audio fallback error',e);
            voiceError('playback failed',String(e&&e.message||e));
            finish(false);
          });
        });
      }
    }catch(err){
      console.log('HTMLAudio error, using WebAudio fallback',err);
      playViaWebAudio(buf).then(()=>finish(true)).catch((e)=>{
        voiceError('playback failed',String(e&&e.message||e));
        finish(false);
      });
    }
  });
}

async function playReply(text){
  if(!spkOn||!text)return;
  try{
    const r=await api('/api/say?text='+encodeURIComponent(text.slice(0,500)));
    if(!r.ok){
      // Surface the PC's real TTS reason (e.g. "tts unavailable: edge-tts
      // failed (...); SAPI fallback failed (...)") instead of a bare status.
      let srv='tts http '+r.status;
      try{const j=await r.json();if(j&&j.error)srv=j.error;}catch(e){}
      throw new Error(srv);
    }
    const mime=r.headers.get('Content-Type')||'audio/mpeg';
    const buf=await r.arrayBuffer();
    await playAudio(buf,mime);
  }catch(e){console.log('voice:',e);voiceError('tts failed',String(e&&e.message||e));}
}

document.getElementById('testspk').onclick=async()=>{
  unlockAudio();
  const st=document.getElementById('astat');
  if(st)st.innerText='Audio: synthesizing test...';
  try{
    const r=await api('/api/say?text='+encodeURIComponent('Speech test successful. Phone audio is active.'));
    if(!r.ok){
      let srv='http '+r.status;
      try{const j=await r.json();if(j&&j.error)srv=j.error;}catch(e){}
      throw new Error(srv);
    }
    const mime=r.headers.get('Content-Type')||'audio/mpeg';
    const buf=await r.arrayBuffer();
    await playAudio(buf,mime);
  }catch(e){
    alert('Test failed: '+(e&&e.message||e));
    voiceError('test failed',String(e&&e.message||e));
  }
};

const logEl=document.getElementById('log');
function add(s,m){
  const d=document.createElement('div');
  d.innerHTML='<b class="'+s+'">'+s.toUpperCase()+':</b> '+m.replace(/</g,'&lt;');
  logEl.appendChild(d);logEl.scrollTop=logEl.scrollHeight;
}
async function refreshLog(){
  try{
    const r=await api('/api/log');
    if(!r.ok)return;
    const rows=await r.json();logEl.innerHTML='';
    rows.forEach(x=>add(x[1].toLowerCase()==='user'?'you':'aria',x[2]));
  }catch(e){}
}
refreshLog();
setInterval(refreshLog,3000);
document.getElementById('face').src='/face.mjpg';

const msgForm=document.getElementById('msgform');
msgForm.addEventListener('submit',async function(e){
  e.preventDefault();
  e.stopPropagation();
  unlockAudio();
  const inp=document.getElementById('t');
  const t=inp.value.trim();
  if(!t)return false;
  inp.value='';
  add('you',t);
  const btn=document.getElementById('sendbtn');
  btn.disabled=true;
  try{
    const r=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t,want_audio:spkOn})});
    btn.disabled=false;
    if(r.ok){
      const d=await r.json();
      const replies=d.reply||[];
      for(const s of replies){
        if(s&&s.trim())add('aria',s);
      }
      if(d.audio&&spkOn){
        try{
          const buf=b64ToArrayBuffer(d.audio);
          await playAudio(buf,d.audio_mime||'audio/mpeg');
        }catch(err){
          console.log('play audio err',err);
          voiceError('audio decode',String(err&&err.message||err));
        }
      }else if(replies.length>0&&spkOn){
        await playReply(replies[0]);
      }
    }else{
      let rd={};try{rd=await r.json();}catch(e){}
      alert('Directive failed: '+(rd.error||r.status));
    }
  }catch(err){
    btn.disabled=false;
    alert('Network error: '+err);
  }
  return false;
});

let mr=null,chunks=[],isHolding=false;
const talkBtn=document.getElementById('talk');
talkBtn.onpointerdown=async(e)=>{
  e.preventDefault();
  unlockAudio();
  isHolding=true;
  chunks=[];
  // 'playback' forbids capture on iOS — allow recording for the hold duration.
  setAudioSessionType('playAndRecord');
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(!isHolding){
      stream.getTracks().forEach(t=>t.stop());
      setAudioSessionType('playback');
      talkBtn.innerText='Hold to talk';
      return;
    }
    let mimeType='';
    if(window.MediaRecorder&&typeof MediaRecorder.isTypeSupported==='function'){
      if(MediaRecorder.isTypeSupported('audio/webm;codecs=opus'))mimeType='audio/webm;codecs=opus';
      else if(MediaRecorder.isTypeSupported('audio/webm'))mimeType='audio/webm';
      else if(MediaRecorder.isTypeSupported('audio/mp4'))mimeType='audio/mp4';
      else if(MediaRecorder.isTypeSupported('audio/aac'))mimeType='audio/aac';
    }
    mr=mimeType?new MediaRecorder(stream,{mimeType}):new MediaRecorder(stream);
    mr.ondataavailable=ev=>{if(ev.data&&ev.data.size>0)chunks.push(ev.data);};
    mr.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop());
      setAudioSessionType('playback');
      const recType=(mr&&mr.mimeType)||mimeType||'audio/webm';
      const blob=new Blob(chunks,{type:recType});
      talkBtn.innerText='Processing...';
      try{
        const r=await api('/api/voice',{method:'POST',headers:{'Content-Type':recType},body:blob});
        if(r.ok){
          const mime=r.headers.get('Content-Type')||'audio/mpeg';
          const buf=await r.arrayBuffer();
          const transcript=decodeURIComponent(r.headers.get('X-Transcript')||'');
          const reply=decodeURIComponent(r.headers.get('X-Reply')||'');
          if(transcript)add('you',transcript);
          if(reply)add('aria',reply);
          try{await playAudio(buf,mime);}catch(e){console.log('voice:',e);voiceError('voice play',String(e&&e.message||e));}
        }else{
          let rd={};try{rd=await r.json();}catch(e){}
          if(rd.reply)add('aria',rd.reply);
          voiceError('voice error',rd.error||('http '+r.status));
          if(!rd.reply)alert('Voice request failed: '+(rd.error||r.status));
        }
      }catch(err){alert('Voice error: '+err);}
      talkBtn.innerText='Hold to talk';
    };
    mr.start();
    talkBtn.innerText='Listening...';
  }catch(err){
    isHolding=false;
    setAudioSessionType('playback');
    alert('Mic error ('+err+'). Ensure HTTPS certificate is accepted.');
  }
};
talkBtn.onpointerup=(e)=>{
  e.preventDefault();
  unlockAudio();
  isHolding=false;
  if(mr&&mr.state==='recording')mr.stop();
};
talkBtn.onpointercancel=talkBtn.onpointerup;

// Phone-as-eyes: stream this page's camera to the laptop as ARIA's body
// camera (set ARIA_BODY_CAMERA=bridge on the laptop first).
let camOn=false,camStream=null,camTimer=null;
const camBtn=document.getElementById('cam');
const camVideo=document.createElement('video');
camVideo.setAttribute('playsinline','');camVideo.muted=true;camVideo.style.display='none';
document.body.appendChild(camVideo);
const camCanvas=document.createElement('canvas');camCanvas.width=480;camCanvas.height=360;
function updateCamBtn(){camBtn.innerText='Camera: '+(camOn?'ON':'OFF');}
async function toggleCam(){
  if(camOn){
    camOn=false;updateCamBtn();
    if(camTimer){clearInterval(camTimer);camTimer=null;}
    if(camStream){camStream.getTracks().forEach(t=>t.stop());camStream=null;}
    return;
  }
  try{
    camStream=await navigator.mediaDevices.getUserMedia(
      {video:{facingMode:'user',width:{ideal:480},height:{ideal:360}},audio:false});
    camVideo.srcObject=camStream;
    await camVideo.play();
    camOn=true;updateCamBtn();
    const ctx=camCanvas.getContext('2d');
    let posting=false;
    camTimer=setInterval(()=>{
      if(!camOn||posting)return;
      if(camVideo.readyState<2||camVideo.videoWidth===0)return;
      ctx.drawImage(camVideo,0,0,camCanvas.width,camCanvas.height);
      posting=true;
      camCanvas.toBlob(async(blob)=>{
        posting=false;
        if(!blob||!camOn)return;
        try{await api('/api/camframe',{method:'POST',headers:{'Content-Type':'image/jpeg'},body:blob});}catch(e){}
      },'image/jpeg',0.6);
    },350);
  }catch(err){
    alert('Camera error ('+err+'). Grant camera permission and ensure the HTTPS certificate is accepted.');
  }
}
camBtn.addEventListener('click',()=>{unlockAudio();toggleCam();});
</script></body></html>"""


BRIDGE_LOGIN_HTML = """<!DOCTYPE html><html><head><meta name="viewport"
content="width=device-width,initial-scale=1"><title>A.R.I.A. Bridge - Login</title>
<style>body{background:#0b0e12;color:#e8f4ff;font-family:sans-serif;margin:0;padding:16px}
h2{color:#ff5fa2}p{color:#9fb2c3;font-size:14px}
form{display:flex;gap:8px;margin-top:24px}input{flex:1;padding:12px;border-radius:8px;border:1px
solid #2a3138;background:#14181d;color:#fff;font-size:16px}
button{padding:12px 18px;border-radius:8px;border:0;background:#ff5fa2;color:#fff;
font-weight:bold;font-size:16px}</style></head><body>
<h2>A.R.I.A. // Phone Bridge</h2>
<p>Enter your bridge token to connect. Find it in the ARIA console, or ask ARIA for it.</p>
<form onsubmit="return login()"><input id="t" type="password" placeholder="Bridge token..."
autocomplete="off"><button>Connect</button></form>
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
        "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>A.R.I.A. Commands</title><style>"
        "body{background:#0b0e12;color:#e8f4ff;font-family:sans-serif;margin:0;padding:16px}"
        "h2{color:#ff5fa2}h3{color:#1edcff;margin:18px 0 6px}"
        ".cmd{background:#14181d;border:1px solid #2a3138;border-radius:8px;padding:10px;margin-bottom:6px}"
        ".cmd b{color:#1edcff;display:block}.cmd span{color:#9fb2c3;font-size:14px}"
        "#q{width:100%;padding:12px;border-radius:8px;border:1px solid #2a3138;background:#14181d;color:#fff;font-size:16px;box-sizing:border-box}"
        "a{color:#ff5fa2}</style></head><body>"
        "<h2>A.R.I.A. // Commands</h2>"
        "<input id='q' placeholder='Filter commands...' oninput='f()'>"
        f"<div id='list'>{''.join(parts)}</div>"
        "<p><a href='/'>&larr; Bridge</a></p>"
        "<script>function f(){var q=document.getElementById('q').value.toLowerCase();"
        "document.querySelectorAll('.cmd').forEach(function(e){"
        "e.style.display=e.getAttribute('data-t').toLowerCase().indexOf(q)>=0?'':'none';});}</script></body></html>"
    )



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

    def _authed(self):
        return bool(BRIDGE_TOKEN) and self._token_from_request() == BRIDGE_TOKEN

    def _bridge_cookie(self) -> str:
        cookie = ("aria_bridge_token=" + urllib.parse.quote(BRIDGE_TOKEN or "", safe="")
                  + "; HttpOnly; Path=/; SameSite=Strict")
        if BRIDGE_SCHEME == "https":
            cookie += "; Secure"
        return cookie

    def do_GET(self):
        if self.path.startswith("/api/"):
            if not self._authed():
                self._send(401, b'{"error":"bad or missing bridge token"}')
                return
            if self.path.startswith("/api/log"):
                logs = _CHAT_LOG_CALL() if _CHAT_LOG_CALL else []
                self._send(200, json.dumps(logs[-30:]).encode("utf-8"))
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
                while True:
                    jpg = get_face_frame_jpeg()
                    if jpg:
                        chunk = (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.write(chunk)
                    time.sleep(0.08)
            except Exception:
                return

        path = self.path.split("?", 1)[0]
        if path == "/":
            if self._authed():
                self._send(200, BRIDGE_HTML.encode("utf-8"), "text/html")
                return
            # One-time upgrade: a valid legacy ?token= bookmark becomes a
            # cookie, then redirects to the clean URL.
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            qtok = urllib.parse.unquote_plus(
                dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("token", ""))
            if BRIDGE_TOKEN and qtok == BRIDGE_TOKEN:
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", self._bridge_cookie())
                self.end_headers()
                return
            self._send(200, BRIDGE_LOGIN_HTML.encode("utf-8"), "text/html")
            return

        if path == "/commands":
            if not self._authed():
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._send(200, _commands_html().encode("utf-8"), "text/html")
            return

        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path.startswith("/api/login"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                tok = json.loads(self.rfile.read(length).decode("utf-8")).get("token", "")
            except Exception:
                tok = ""
            if BRIDGE_TOKEN and tok == BRIDGE_TOKEN:
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", self._bridge_cookie())
                self.end_headers()
                self.wfile.write(body)
            else:
                if BRIDGE_TOKEN:
                    add_log("Bridge: failed login attempt.")
                self._send(401, b'{"error":"bad bridge token"}')
            return
        if not (self.path.startswith("/api/ask") or self.path.startswith("/api/voice")
                or self.path.startswith("/api/camframe")):
            self.send_response(404)
            self.end_headers()
            return
        if not self._authed():
            self._send(401, b'{"error":"bad or missing bridge token"}')
            return

        length = int(self.headers.get("Content-Length", 0))
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
    global _BRIDGE_SERVER
    ensure_bridge_cert()
    # Configurable bind host ("phone_bridge_host" setting). Defaults to all
    # interfaces because the phone reaches the bridge over the LAN; set to
    # 127.0.0.1 to lock it to this machine only. Every route requires the
    # bridge token regardless of bind address.
    host = get_setting("phone_bridge_host", "0.0.0.0")
    srv = ThreadingHTTPServer((host, port), BridgeHandler)
    _BRIDGE_SERVER = srv
    if BRIDGE_SCHEME == "https" and BRIDGE_CERT and BRIDGE_KEY:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=BRIDGE_CERT, keyfile=BRIDGE_KEY)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            add_log(f"Phone bridge (HTTPS TLS): https://{lan_ip()}:{port}")
            print(f"[ARIA] Phone bridge online: https://{lan_ip()}:{port}", flush=True)
        except Exception as e:
            add_log(f"Bridge TLS wrap failed: {e}")
            print(f"[ARIA] Bridge TLS wrap failed: {e}", flush=True)
    else:
        add_log(f"Phone bridge (HTTP): http://{lan_ip()}:{port}")
        print(f"[ARIA] Phone bridge online: http://{lan_ip()}:{port}", flush=True)
    try:
        srv.serve_forever()
    except Exception as e:
        add_log(f"Bridge server stopped: {e}")
    return srv
