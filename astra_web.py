#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astra_web - interfata web pentru binarul Astra Cesbo (free).
Nu modifica binarul: doar genereaza config Lua, porneste/opreste procesul,
citeste statisticile din log si le afiseaza.

Dependente: NICIUNA (doar Python 3 standard library). Merge pe Windows si Ubuntu.
Rulare:
    python astra_web.py --astra "C:\\astra\\astra.exe" --port 8080
Prima pornire creeaza fisierele de config langa script.
"""

import os, sys, json, re, time, threading, subprocess, hashlib, secrets, argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie

IS_WIN = os.name == "nt"
BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE, "settings.json")
CHANNELS_FILE = os.path.join(BASE, "channels.json")
CONFIG_LUA    = os.path.join(BASE, "astra_config.lua")
LOG_FILE      = os.path.join(BASE, "astra_web.log")

# ------------------------------------------------------------------ state
LOG = deque(maxlen=4000)          # (ts, line)
LOG_SEQ = 0
STATS = {}                        # name -> {bitrate, cc, pes, scrambled, ts, raw}
STATS_LOCK = threading.Lock()
TOKENS = set()
PROC = None
PROC_START = 0
PROC_LOCK = threading.Lock()

DEFAULT_SETTINGS = {
    "astra_path": "",
    "port": 8080,
    "bind": "0.0.0.0",
    "username": "admin",
    "salt": "",
    "pass_hash": "",          # gol = parola implicita "admin"
}

# ------------------------------------------------------------------ utils
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def hpass(salt, pw):
    return hashlib.sha256((salt + ":" + pw).encode("utf-8")).hexdigest()

def check_login(settings, user, pw):
    if user != settings.get("username", "admin"):
        return False
    if not settings.get("pass_hash"):
        return pw == "admin"           # implicit pana la prima schimbare
    return hpass(settings.get("salt", ""), pw) == settings["pass_hash"]

def logline(line):
    global LOG_SEQ
    line = line.rstrip("\r\n")
    if not line:
        return
    LOG.append((time.strftime("%H:%M:%S"), line))
    LOG_SEQ += 1
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass
    parse_stats(line)

# Astra logheaza analize de forma: [nume_canal ...] Bitrate:... CC error:...
RE_NAME = re.compile(r"\[([^\]]+?)(?:\s+[a-z]+://[^\]]*)?\]")
RE_BR   = re.compile(r"[Bb]itrate[:=]?\s*([\d.]+)\s*([KkMm]?)", )
RE_CC   = re.compile(r"CC\s*[Ee]rror[s]?[:=]?\s*(\d+)")
RE_PES  = re.compile(r"PES\s*[Ee]rror[s]?[:=]?\s*(\d+)")
RE_SCR  = re.compile(r"[Ss]crambled")

def parse_stats(line):
    if "Bitrate" not in line and "CC" not in line and "Scrambled" not in line:
        return
    m = RE_NAME.search(line)
    name = m.group(1).split()[0] if m else "?"
    with STATS_LOCK:
        s = STATS.setdefault(name, {"bitrate": None, "cc": 0, "pes": 0,
                                    "scrambled": False, "ts": 0, "raw": ""})
        mb = RE_BR.search(line)
        if mb:
            val = float(mb.group(1)); unit = mb.group(2).lower()
            if unit == "k": val /= 1000.0
            s["bitrate"] = round(val, 2)
        mc = RE_CC.search(line)
        if mc: s["cc"] = int(mc.group(1))
        mp = RE_PES.search(line)
        if mp: s["pes"] = int(mp.group(1))
        s["scrambled"] = bool(RE_SCR.search(line))
        s["ts"] = time.time()
        s["raw"] = line[:300]

# ------------------------------------------------------------------ lua config
def lua_escape(u):
    return u.replace("\\", "\\\\").replace('"', '\\"')

def build_config(channels):
    out = ["-- generat automat de astra_web - nu edita manual\n"]
    for ch in channels:
        if not ch.get("enabled", True):
            continue
        ins  = ", ".join('"%s"' % lua_escape(x) for x in ch.get("input", [])  if x.strip())
        outs = ", ".join('"%s"' % lua_escape(x) for x in ch.get("output", []) if x.strip())
        if not ins:
            continue
        out.append("make_channel({")
        out.append('    name = "%s",' % lua_escape(ch.get("name", "chan")))
        out.append("    input = { %s }," % ins)
        if outs:
            out.append("    output = { %s }," % outs)
        out.append("})\n")
    return "\n".join(out)

# ------------------------------------------------------------------ process control
def astra_running():
    return PROC is not None and PROC.poll() is None

def start_astra(settings, channels):
    global PROC, PROC_START
    with PROC_LOCK:
        if astra_running():
            return False, "deja pornit"
        ap = settings.get("astra_path", "").strip().strip('"')
        if not ap:
            return False, "seteaza calea catre astra.exe in Setari"
        if not os.path.exists(ap):
            return False, "calea nu exista: %r" % ap
        if os.path.isdir(ap):
            return False, "calea e un folder, nu fisierul - pune calea completa pana la astra.exe"
        cfg = build_config(channels)
        with open(CONFIG_LUA, "w", encoding="utf-8") as f:
            f.write(cfg)
        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      bufsize=1, universal_newlines=True,
                      cwd=os.path.dirname(ap) or None)
        if IS_WIN:
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            PROC = subprocess.Popen([ap, CONFIG_LUA], **kwargs)
        except PermissionError as e:
            return False, ("[WinError 5] Access denied. Verifica: right-click pe astra.exe "
                           "-> Properties -> Unblock; exclude folderul din antivirus; "
                           "sau porneste start.bat ca Administrator.")
        except Exception as e:
            return False, "nu pot porni: %s" % e
        PROC_START = time.time()
        with STATS_LOCK:
            STATS.clear()
        threading.Thread(target=reader, args=(PROC,), daemon=True).start()
        logline("=== astra pornit (pid %s) ===" % PROC.pid)
        return True, "pornit"

def stop_astra():
    global PROC
    with PROC_LOCK:
        if not astra_running():
            return False, "nu rula"
        try:
            PROC.terminate()
            try: PROC.wait(timeout=5)
            except Exception:
                PROC.kill()
        except Exception as e:
            return False, str(e)
        logline("=== astra oprit ===")
        return True, "oprit"

def reader(proc):
    try:
        for line in proc.stdout:
            logline(line)
    except Exception:
        pass

# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    server_version = "astra_web"
    def log_message(self, *a): pass

    # ---- helpers
    def _settings(self): return self.server.settings
    def _channels(self): return self.server.channels

    def _auth_ok(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        tok = c["tok"].value if "tok" in c else None
        return tok in TOKENS

    def _send(self, code, body, ctype="application/json", cookie=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        try: return json.loads(raw.decode("utf-8"))
        except Exception: return {}

    # ---- GET
    def do_GET(self):
        p = urlparse(self.path)
        path = p.path
        if path == "/":
            return self._send(200, PAGE if self._auth_ok() else LOGIN, "text/html")
        if path.startswith("/api/") and not self._auth_ok():
            return self._send(401, {"error": "auth"})
        if path == "/api/status":
            return self._send(200, {
                "running": astra_running(),
                "pid": PROC.pid if astra_running() else None,
                "uptime": int(time.time() - PROC_START) if astra_running() else 0,
                "astra_path": self._settings().get("astra_path", ""),
                "astra_exists": os.path.exists(self._settings().get("astra_path", "")),
            })
        if path == "/api/channels":
            return self._send(200, self._channels())
        if path == "/api/stats":
            with STATS_LOCK:
                return self._send(200, {k: dict(v) for k, v in STATS.items()})
        if path == "/api/config":
            return self._send(200, {"lua": build_config(self._channels())}, )
        if path == "/api/logs":
            q = parse_qs(p.query)
            since = int(q.get("since", ["0"])[0])
            lines = list(LOG)
            start = max(0, len(lines) - 500) if since == 0 else 0
            return self._send(200, {"seq": LOG_SEQ,
                                    "lines": [{"t": t, "l": l} for t, l in lines[start:]]})
        if path == "/api/settings":
            s = dict(self._settings()); s.pop("pass_hash", None); s.pop("salt", None)
            s["pass_set"] = bool(self._settings().get("pass_hash"))
            return self._send(200, s)
        return self._send(404, {"error": "not found"})

    # ---- POST
    def do_POST(self):
        p = urlparse(self.path); path = p.path
        if path == "/api/login":
            d = self._body()
            if check_login(self._settings(), d.get("user", ""), d.get("pass", "")):
                tok = secrets.token_hex(16); TOKENS.add(tok)
                return self._send(200, {"ok": True},
                                  cookie="tok=%s; HttpOnly; Path=/; Max-Age=86400" % tok)
            return self._send(200, {"ok": False})
        if not self._auth_ok():
            return self._send(401, {"error": "auth"})
        if path == "/api/logout":
            c = SimpleCookie(self.headers.get("Cookie", ""))
            if "tok" in c: TOKENS.discard(c["tok"].value)
            return self._send(200, {"ok": True})
        if path == "/api/start":
            ok, msg = start_astra(self._settings(), self._channels())
            return self._send(200, {"ok": ok, "msg": msg})
        if path == "/api/stop":
            ok, msg = stop_astra()
            return self._send(200, {"ok": ok, "msg": msg})
        if path == "/api/restart":
            stop_astra(); time.sleep(0.5)
            ok, msg = start_astra(self._settings(), self._channels())
            return self._send(200, {"ok": ok, "msg": msg})
        if path == "/api/channels":
            chans = self._body()
            if isinstance(chans, list):
                self.server.channels = chans
                save_json(CHANNELS_FILE, chans)
                return self._send(200, {"ok": True})
            return self._send(200, {"ok": False, "msg": "format invalid"})
        if path == "/api/settings":
            d = self._body(); s = self._settings()
            s["astra_path"] = d.get("astra_path", s.get("astra_path", "")).strip()
            s["username"]   = d.get("username", s.get("username", "admin")).strip() or "admin"
            try: s["port"] = int(d.get("port", s.get("port", 8080)))
            except Exception: pass
            s["bind"] = d.get("bind", s.get("bind", "0.0.0.0")).strip() or "0.0.0.0"
            newpw = d.get("password", "")
            if newpw:
                s["salt"] = secrets.token_hex(8)
                s["pass_hash"] = hpass(s["salt"], newpw)
            save_json(SETTINGS_FILE, s)
            return self._send(200, {"ok": True, "note": "portul/bind se aplica la repornirea scriptului"})
        return self._send(404, {"error": "not found"})

# ------------------------------------------------------------------ frontend
STYLE = """
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;
background:#0d1117;color:#c9d1d9}a{color:#58a6ff}
header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 20px;display:flex;
align-items:center;gap:16px}header h1{font-size:16px;margin:0;color:#e6edf3}
.dot{width:10px;height:10px;border-radius:50%;background:#f85149;display:inline-block}
.dot.on{background:#3fb950}.spacer{flex:1}
button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;
padding:7px 12px;cursor:pointer;font-size:13px}button:hover{border-color:#8b949e}
button.p{background:#238636;border-color:#2ea043;color:#fff}button.d{background:#da3633;border-color:#f85149;color:#fff}
nav{display:flex;gap:4px;padding:10px 20px;background:#0d1117;border-bottom:1px solid #30363d}
nav button.act{background:#1f6feb;border-color:#1f6feb;color:#fff}
main{padding:20px;max-width:1100px;margin:0 auto}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:600;font-size:12px;text-transform:uppercase}
input,textarea{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;
padding:7px;width:100%;font:13px monospace}label{display:block;color:#8b949e;font-size:12px;margin:8px 0 3px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
#log{background:#010409;border:1px solid #30363d;border-radius:6px;padding:10px;height:60vh;
overflow:auto;font:12px/1.4 monospace;white-space:pre-wrap}
.badge{padding:2px 7px;border-radius:10px;font-size:11px}.b-ok{background:#1a3d26;color:#3fb950}
.b-err{background:#4d1f1f;color:#f85149}.b-off{background:#30363d;color:#8b949e}
.row-ch{display:grid;grid-template-columns:150px 1fr 1fr 70px 60px;gap:8px;align-items:start;
padding:8px 0;border-bottom:1px solid #21262d}
.mut{color:#8b949e;font-size:12px}
.ovl{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:9}
.modal{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;max-width:520px;width:90%}
.modal h3{margin:0 0 10px;color:#e6edf3}
.urlbox{display:flex;gap:6px;margin:6px 0}.urlbox input{font-size:12px}
button.sm{padding:4px 8px;font-size:12px}
"""

LOGIN = """<!doctype html><meta charset=utf-8><title>astra_web login</title><style>__STYLE__
.box{max-width:300px;margin:12vh auto;padding:24px}</style>
<div class="card box"><h1 style="color:#e6edf3">astra_web</h1>
<label>Utilizator</label><input id=u value=admin>
<label>Parola</label><input id=p type=password>
<div style="margin-top:14px"><button class=p style=width:100% onclick=go()>Autentificare</button></div>
<div id=e class=b-err style="display:none;margin-top:10px;padding:6px">Date gresite</div></div>
<script>
async function go(){let r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({user:u.value,pass:p.value})});let j=await r.json();
if(j.ok)location.reload();else e.style.display='block'}
p.addEventListener('keydown',e=>{if(e.key=='Enter')go()})
</script>""".replace("__STYLE__", STYLE)

PAGE = """<!doctype html><meta charset=utf-8><title>astra_web</title><style>__STYLE__</style>
<header><span id=dot class=dot></span><h1>astra_web</h1>
<span id=st class=mut></span><span class=spacer></span>
<button class=p id=bStart onclick=ctl('start')>Start</button>
<button class=d id=bStop onclick=ctl('stop')>Stop</button>
<button onclick=ctl('restart')>Restart</button>
<button onclick=logout()>Iesire</button></header>
<nav>
<button class=act data-t=dash onclick=tab('dash')>Dashboard</button>
<button data-t=chan onclick=tab('chan')>Canale</button>
<button data-t=log onclick=tab('log')>Loguri</button>
<button data-t=cfg onclick=tab('cfg')>Config Lua</button>
<button data-t=set onclick=tab('set')>Setari</button></nav>
<main>
<div id=dash class=view><div class=card><h3>Statistici live</h3>
<table><thead><tr><th>Canal</th><th>Bitrate</th><th>CC err</th><th>PES err</th><th>Stare</th><th>Actualizat</th><th>Preview</th></tr></thead>
<tbody id=stats></tbody></table><div class=mut id=statnote style=margin-top:8px></div></div></div>

<div id=chan class=view style=display:none><div class=card>
<h3>Canale</h3>
<div class=row-ch style=font-weight:600;color:#8b949e><div>Nume</div><div>Input (1/linie)</div><div>Output (1/linie)</div><div>Activ</div><div></div></div>
<div id=chans></div>
<div style=margin-top:12px><button onclick=addCh()>+ Canal</button>
<button class=p onclick=saveCh()>Salveaza</button>
<span class=mut> &nbsp;(dupa salvare: Restart ca sa se aplice)</span></div></div></div>

<div id=log class=view style=display:none><div class=card><h3>Loguri astra</h3><div id=logbox></div></div></div>

<div id=cfg class=view style=display:none><div class=card><h3>Config Lua generat (read-only)</h3>
<textarea id=cfgtext readonly style=height:60vh></textarea></div></div>

<div class=ovl id=ovl onclick="if(event.target==this)closeModal()"><div class=modal>
<h3 id=mTitle>Preview</h3>
<div class=mut>Stream MPEG-TS - deschide-l in VLC (Media &gt; Open Network Stream) sau cu butonul de mai jos.</div>
<div id=mUrls></div>
<div style=margin-top:12px><button onclick=closeModal()>Inchide</button></div></div></div>

<div id=set class=view style=display:none><div class=card><h3>Setari</h3>
<label>Cale catre astra.exe</label><input id=s_path placeholder="C:\\astra\\astra.exe">
<div class=grid>
<div><label>Port web</label><input id=s_port></div>
<div><label>Bind (0.0.0.0 = acces extern)</label><input id=s_bind></div></div>
<div class=grid>
<div><label>Utilizator</label><input id=s_user></div>
<div><label>Parola noua (gol = neschimbata)</label><input id=s_pass type=password></div></div>
<div style=margin-top:12px><button class=p onclick=saveSet()>Salveaza setari</button>
<span class=mut id=setnote></span></div>
<p class=mut>Portul si bind se aplica dupa repornirea scriptului.</p></div></div>
</main>
<script>
let CH=[];
function tab(t){document.querySelectorAll('.view').forEach(v=>v.style.display='none');
document.getElementById(t).style.display='';
document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('act',b.dataset.t==t));
if(t=='cfg')loadCfg();if(t=='set')loadSet();if(t=='chan')renderCh()}
async function j(u,o){let r=await fetch(u,o);return r.json()}
async function ctl(a){let r=await j('/api/'+a,{method:'POST'});if(!r.ok&&r.msg)alert(r.msg);refresh()}
async function logout(){await fetch('/api/logout',{method:'POST'});location.reload()}

async function refresh(){
let s=await j('/api/status');
document.getElementById('dot').className='dot'+(s.running?' on':'');
document.getElementById('st').textContent=s.running?('ruleaza · pid '+s.pid+' · '+fmtup(s.uptime)):
(s.astra_path?(s.astra_exists?'oprit':'astra.exe negasit'):'seteaza calea astra.exe in Setari');
let st=await j('/api/stats');let b='';let names=Object.keys(st).sort();
for(let n of names){let c=st[n];let age=Math.round(Date.now()/1000-c.ts);
let stare=c.scrambled?'<span class="badge b-err">scrambled</span>':
(c.cc>0?'<span class="badge b-err">CC</span>':'<span class="badge b-ok">OK</span>');
b+='<tr><td>'+esc(n)+'</td><td>'+(c.bitrate!=null?c.bitrate+' Mbit/s':'-')+
'</td><td>'+c.cc+'</td><td>'+c.pes+'</td><td>'+stare+'</td><td class=mut>'+age+'s</td>'+
'<td><button class=sm onclick="preview(\\''+esc(n).replace(/'/g,"\\\\'")+'\\')">&#9654;</button></td></tr>'}
document.getElementById('stats').innerHTML=b||'<tr><td colspan=7 class=mut>fara date (porneste astra sau verifica logurile)</td></tr>';
document.getElementById('statnote').textContent=names.length?'':
'Daca raman goale cand astra ruleaza, formatul de log al versiunii tale difera - trimite-mi cateva linii din tab-ul Loguri.';
}
function fmtup(s){let h=Math.floor(s/3600),m=Math.floor(s%3600/60);return(h?h+'h ':'')+m+'m '+(s%60)+'s'}
function esc(x){return(x+'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}

async function loadChans(){CH=await j('/api/channels');renderCh()}
function updName(i,v){CH[i].name=v}
function updArr(i,f,v){CH[i][f]=v.split('\\n')}
function updEn(i,v){CH[i].enabled=v}
function delCh(i){CH.splice(i,1);renderCh()}
function renderCh(){let h='';CH.forEach((c,i)=>{
h+='<div class=row-ch>'+
'<input value="'+esc(c.name||'')+'" oninput="updName('+i+',this.value)">'+
'<textarea rows=2 placeholder="udp://@239.1.1.1:1234" oninput="updArr('+i+',\\'input\\',this.value)">'+esc((c.input||[]).join('\\n'))+'</textarea>'+
'<textarea rows=2 placeholder="http://0.0.0.0:8000/canal" oninput="updArr('+i+',\\'output\\',this.value)">'+esc((c.output||[]).join('\\n'))+'</textarea>'+
'<input type=checkbox '+((c.enabled!==false)?'checked':'')+' onchange="updEn('+i+',this.checked)">'+
'<button class=d onclick="delCh('+i+')">x</button></div>'});
document.getElementById('chans').innerHTML=h||'<div class=mut>niciun canal</div>'}
function addCh(){CH.push({name:'canal'+(CH.length+1),input:['udp://@239.1.1.1:1234'],
output:['http://0.0.0.0:8000/canal'+(CH.length+1)],enabled:true});renderCh()}
async function saveCh(){let clean=CH.map(c=>({name:c.name,
input:(c.input||[]).filter(x=>x.trim()),output:(c.output||[]).filter(x=>x.trim()),
enabled:c.enabled!==false}));let r=await j('/api/channels',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(clean)});
if(r.ok)alert('Salvat. Da Restart ca sa se aplice.')}

function fixHost(u){return u.replace('0.0.0.0',location.hostname).replace('://127.0.0.1','://'+location.hostname)}
function preview(name){let c=CH.find(x=>x.name==name)||CH.find(x=>(x.name||'').split(' ')[0]==name);
let outs=(c&&c.output||[]).filter(x=>x.trim());
if(!outs.length){alert('Canalul nu are output definit.');return}
document.getElementById('mTitle').textContent='Preview: '+name;
document.getElementById('mUrls').innerHTML=outs.map(u=>{let fu=fixHost(u);
return '<div class=urlbox><input readonly value="'+esc(fu)+'">'+
'<button class=sm onclick="navigator.clipboard.writeText(\\''+esc(fu).replace(/'/g,"\\\\'")+'\\');this.textContent=\\'OK\\'">Copiaza</button>'+
'<button class=sm onclick="window.open(\\''+esc(fu).replace(/'/g,"\\\\'")+'\\')">Deschide</button></div>'}).join('');
document.getElementById('ovl').style.display='flex'}
function closeModal(){document.getElementById('ovl').style.display='none'}

let logseq=-1;
async function loadLog(){let d=await j('/api/logs');if(d.seq==logseq)return;logseq=d.seq;
let box=document.getElementById('logbox');if(!box.firstChild){box.innerHTML='<div id=log></div>'}
document.getElementById('log').innerHTML=d.lines.map(x=>'<span class=mut>'+x.t+'</span>  '+esc(x.l)).join('\\n');
let l=document.getElementById('log');l.scrollTop=l.scrollHeight}
async function loadCfg(){let d=await j('/api/config');document.getElementById('cfgtext').value=d.lua}
async function loadSet(){let s=await j('/api/settings');s_path.value=s.astra_path||'';
s_port.value=s.port;s_bind.value=s.bind;s_user.value=s.username;s_pass.value='';
setnote.textContent=s.pass_set?'':' · parola implicita: admin'}
async function saveSet(){let r=await j('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({astra_path:s_path.value,port:s_port.value,bind:s_bind.value,
username:s_user.value,password:s_pass.value})});
if(r.ok){setnote.textContent=' · '+(r.note||'salvat');s_pass.value=''}}

loadChans();refresh();
setInterval(refresh,2000);
setInterval(()=>{if(document.getElementById('log').style.display!='none')loadLog()},1500);
</script>""".replace("__STYLE__", STYLE)

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--astra", help="cale catre astra.exe / astra")
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind")
    args = ap.parse_args()

    settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        settings.setdefault(k, v)
    if args.astra: settings["astra_path"] = args.astra
    if args.port:  settings["port"] = args.port
    if args.bind:  settings["bind"] = args.bind
    save_json(SETTINGS_FILE, settings)
    channels = load_json(CHANNELS_FILE, [])

    # incearca portul configurat, apoi alternative (Windows rezerva/blocheaza unele porturi -> WinError 10013)
    candidates = [settings["port"]] + [p for p in (8099, 8181, 8282, 8700, 8900, 9123, 8010)
                                       if p != settings["port"]]
    httpd = None
    for port in candidates:
        try:
            httpd = ThreadingHTTPServer((settings["bind"], port), H)
            settings["port"] = port
            break
        except OSError as e:
            print("  portul %s indisponibil (%s), incerc altul..." % (port, getattr(e, "winerror", e.errno)))
    if httpd is None:
        print("Niciun port liber din lista. Schimba 'port' in settings.json sau in start.bat.")
        print("Verifica intervalele rezervate: netsh interface ipv4 show excludedportrange protocol=tcp")
        sys.exit(1)
    if settings["port"] != candidates[0]:
        save_json(SETTINGS_FILE, settings)
    httpd.settings = settings
    httpd.channels = channels
    url = "http://%s:%s" % ("127.0.0.1" if settings["bind"] in ("0.0.0.0", "") else settings["bind"], settings["port"])
    print("astra_web ruleaza pe %s  (user: %s)" % (url, settings["username"]))
    if not settings.get("pass_hash"):
        print("  parola implicita: admin  (schimb-o din Setari)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        stop_astra()
        print("\noprit.")

if __name__ == "__main__":
    main()
