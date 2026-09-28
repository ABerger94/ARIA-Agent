"""
ARIA Phone Bridge Subsystem.
Enables local network control, mobile voice messaging (iOS/Android),
live MJPEG cyber-face stream, and web dashboard over HTTP / TLS HTTPS.
"""

from __future__ import annotations

import json
import os
import ssl
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Callable, Any

from aria.config import PHONE_BRIDGE_PORT, BRIDGE_TOKEN, ROOT_DIR, WORKSPACE_DIR, add_log, lan_ip
from aria.vision import get_face_frame_jpeg
from aria.speech import edge_tts_bytes, transcribe_audio
from aria.tools.schemas import COMMAND_GUIDE

BRIDGE_SCHEME = "http"
BRIDGE_CERT: Optional[str] = None
BRIDGE_KEY: Optional[str] = None

_BRIDGE_PROCESS_CALL: Optional[Callable[[str, bool], str]] = None
_CHAT_LOG_CALL: Optional[Callable[[], Any]] = None
_BRIDGE_SERVER: Optional[ThreadingHTTPServer] = None

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


def _generate_machine_cert(cert_p: str, key_p: str) -> bool:
    """Generate a unique self-signed cert for THIS machine.

    The old bundled cert/key was identical on every deployment, so anyone
    with the repo could MITM any ARIA instance. A per-machine cert stored
    in the (gitignored) workspace dir fixes that.
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
        add_log(f"Bridge: per-machine cert generation failed ({e})")
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
content="width=device-width,initial-scale=1"><title>A.R.I.A. Bridge</title>
<style>body{background:#0b0e12;color:#e8f4ff;font-family:sans-serif;margin:0;padding:16px}
h2{color:#ff5fa2}#log{border:1px solid #2a3138;border-radius:8px;padding:10px;
height:44vh;overflow-y:auto;margin-bottom:12px;font-size:14px}
.you{color:#ff5fa2}.aria{color:#28f078}
form{display:flex;gap:8px;margin-bottom:10px}input{flex:1;padding:12px;border-radius:8px;border:1px
solid #2a3138;background:#14181d;color:#fff;font-size:16px}
button{padding:12px 18px;border-radius:8px;border:0;background:#ff5fa2;color:#fff;
font-weight:bold;font-size:16px}#talk{width:100%;padding:16px;touch-action:none;
user-select:none;-webkit-user-select:none}</style></head><body>
<h2>A.R.I.A. // Phone Bridge</h2>
<div style="text-align:center;margin-bottom:12px"><img id="face" alt="A.R.I.A." style="border-radius:12px;max-width:100%;width:320px;border:1px solid #2a3138"></div>
<div style="margin-bottom:12px"><a href="/commands" style="color:#ff5fa2">Command reference</a></div>
<div id="log"></div>
<form onsubmit="return send()"><input id="t" placeholder="Directive..."
autocomplete="off"><button>Send</button></form>
<button id="talk">Hold to talk</button>
<button id="spk" style="width:100%;margin-top:8px">Speak replies: ON</button>
<script>
let token=new URLSearchParams(window.location.search).get('token')||localStorage.getItem('aria_bridge_token')||'';
if(token){localStorage.setItem('aria_bridge_token',token);}
function ensureToken(){
  if(!token){
    token=prompt('Bridge token (shown in ARIA console / commands):')||'';
    if(token) localStorage.setItem('aria_bridge_token',token);
  }
}
async function api(path,opts){
  ensureToken();opts=opts||{};
  opts.headers=Object.assign({},opts.headers,{'X-Bridge-Token':token});
  const r=await fetch(path,opts);
  if(r.status===401){
    localStorage.removeItem('aria_bridge_token');token='';
    alert('Bad bridge token - check ARIA console and reload.');
  }
  return r;
}
let spkOn=localStorage.getItem('spk')!=='0';
function updateSpkBtn(){document.getElementById('spk').innerText='Speak replies: '+(spkOn?'ON':'OFF');}
updateSpkBtn();
document.getElementById('spk').onclick=()=>{spkOn=!spkOn;localStorage.setItem('spk',spkOn?'1':'0');updateSpkBtn();};
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
document.getElementById('face').src='/face.mjpg?token='+encodeURIComponent(token||localStorage.getItem('aria_bridge_token')||'');
async function send(){
  const i=document.getElementById('t');const t=i.value.trim();
  if(!t)return false;i.value='';add('you',t);
  const r=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})});
  if(r.ok){
    const d=await r.json();(d.reply||[]).forEach(s=>{
      add('aria',s);
      if(spkOn&&s)new Audio('/api/say?text='+encodeURIComponent(s)+'&token='+encodeURIComponent(token)).play().catch(e=>console.log(e));
    });
  }
  return false;
}
let mr=null,chunks=[];
const talkBtn=document.getElementById('talk');
talkBtn.onpointerdown=async(e)=>{
  e.preventDefault();
  ensureToken();
  chunks=[];
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
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
      const recType=(mr&&mr.mimeType)||mimeType||'audio/webm';
      const blob=new Blob(chunks,{type:recType});
      talkBtn.innerText='Processing...';
      try{
        const r=await api('/api/voice',{method:'POST',headers:{'Content-Type':recType},body:blob});
        if(r.ok){
          const wav=await r.blob();
          const transcript=decodeURIComponent(r.headers.get('X-Transcript')||'');
          const reply=decodeURIComponent(r.headers.get('X-Reply')||'');
          if(transcript)add('you',transcript);
          if(reply)add('aria',reply);
          if(spkOn&&wav.size>0){
            new Audio(URL.createObjectURL(wav)).play().catch(e=>console.log(e));
          }
        }else{
          alert('Voice request failed: '+r.status);
        }
      }catch(err){alert('Voice error: '+err);}
      talkBtn.innerText='Hold to talk';
    };
    mr.start();
    talkBtn.innerText='Listening...';
  }catch(err){
    alert('Mic error ('+err+'). Ensure HTTPS certificate is accepted.');
  }
};
talkBtn.onpointerup=(e)=>{e.preventDefault();if(mr&&mr.state==='recording')mr.stop();};
talkBtn.onpointercancel=talkBtn.onpointerup;
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

    def _authed(self):
        tok = self.headers.get("X-Bridge-Token", "")
        if not tok and "?" in self.path:
            qs = self.path.split("?", 1)[1]
            tok = dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("token", "")
        return bool(BRIDGE_TOKEN) and tok == BRIDGE_TOKEN

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
                    self._send(200, edge_tts_bytes(text[:500]), "audio/mpeg")
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)[:200]}).encode("utf-8"))
                return
            self._send(404, b'{"error":"not found"}')
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

        if self.path == "/commands" or self.path.startswith("/commands?"):
            self._send(200, _commands_html().encode("utf-8"), "text/html")
            return

        self._send(200, BRIDGE_HTML.encode("utf-8"), "text/html")

    def do_POST(self):
        if not (self.path.startswith("/api/ask") or self.path.startswith("/api/voice")):
            self.send_response(404)
            self.end_headers()
            return
        if not self._authed():
            self._send(401, b'{"error":"bad or missing bridge token"}')
            return

        length = int(self.headers.get("Content-Length", 0))
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
                audio_out = edge_tts_bytes(reply[:2000])
            except Exception as e:
                self._send(500, json.dumps({"error": f"tts failed: {e}", "reply": reply[:500]}).encode("utf-8"))
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio_out)))
            self.send_header("X-Transcript", urllib.parse.quote(text[:300]))
            self.send_header("X-Reply", urllib.parse.quote(reply[:500]))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Expose-Headers", "*")
            self.end_headers()
            self.wfile.write(audio_out)
            return

        try:
            text = json.loads(self.rfile.read(length).decode("utf-8")).get("text", "")
        except Exception:
            text = ""

        if text.strip():
            add_log(f"Bridge directive: {text[:25]}")
            reply = _BRIDGE_PROCESS_CALL(text.strip(), False) if _BRIDGE_PROCESS_CALL else ""
        else:
            reply = ""
        self._send(200, json.dumps({"reply": [reply]}).encode("utf-8"))


def start_bridge_server(port: int = PHONE_BRIDGE_PORT) -> ThreadingHTTPServer:
    global _BRIDGE_SERVER
    ensure_bridge_cert()
    srv = ThreadingHTTPServer(("0.0.0.0", port), BridgeHandler)
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
