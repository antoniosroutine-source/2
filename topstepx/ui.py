"""The desk: a local web page (http://127.0.0.1:8766/) for alerts, Accept/Reject and marking your own setups.

Every signal, every answer and every "my setup" mark is appended to decisions.jsonl with a market
snapshot. That file is the training data for the trader's discretionary filter.
Optional phone alerts: set PX_NTFY_TOPIC and install the free ntfy app subscribed to that topic.
"""
import itertools
import json
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Desk:
    def __init__(self, decisions_file, log, ntfy_topic=None):
        self.file = decisions_file
        self.log = log
        self.ntfy_topic = ntfy_topic
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.pending = {}          # id -> {"signal", "mode", "asked", "expires", "answer"}
        self.status = {}
        self.events = deque(maxlen=40)

    # -- called by the bot ---------------------------------------------------------
    def ask(self, signal, mode, timeout, snapshot):
        """Show a signal. mode "confirm": the bot waits for Accept. mode "paper": the answer is only logged."""
        sid = next(self.ids)
        now = time.time()
        with self.lock:
            self.pending[sid] = {"signal": signal, "mode": mode, "asked": now, "expires": now + timeout,
                                 "answer": None, "snapshot": snapshot}
        self._write({"kind": "signal", "id": sid, "mode": mode, "signal": signal, "snapshot": snapshot})
        side = signal["side"].upper()
        self._push(f"{side} setup MNQ @ {signal['entry']}  stop {signal['stop']}  target {signal['target']}"
                   + ("  - Accept on the desk" if mode == "confirm" else ""))
        return sid

    def answer(self, sid):
        """"accept", "reject", "expired" or None while still waiting."""
        with self.lock:
            p = self.pending.get(sid)
            if not p:
                return "expired"
            if p["answer"]:
                return p["answer"]
            if time.time() > p["expires"]:
                p["answer"] = "expired"
                self._write({"kind": "answer", "id": sid, "answer": "expired"})
                return "expired"
            return None

    def done(self, sid):
        with self.lock:
            self.pending.pop(sid, None)

    def update(self, **status):
        with self.lock:
            self.status.update(status)

    def event(self, text):
        with self.lock:
            self.events.appendleft({"t": time.strftime("%H:%M:%S"), "text": text})

    # -- called by the page ----------------------------------------------------------
    def decide(self, sid, accept):
        with self.lock:
            p = self.pending.get(sid)
            if not p or p["answer"]:
                return False
            p["answer"] = "accept" if accept else "reject"
            if p["mode"] == "paper":      # nothing waits on a paper answer
                self.pending.pop(sid, None)
        self._write({"kind": "answer", "id": sid, "answer": "accept" if accept else "reject"})
        self.event(f"signal {sid}: {'accepted' if accept else 'rejected'}")
        return True

    def mark(self, side, note):
        with self.lock:
            snap = dict(self.status)
        self._write({"kind": "my_setup", "side": side, "note": note, "snapshot": snap})
        self.event(f"your {side} setup marked" + (f": {note}" if note else ""))

    def view(self):
        now = time.time()
        with self.lock:
            pend = [{"id": k, "mode": v["mode"], "signal": v["signal"], "left": max(0, round(v["expires"] - now))}
                    for k, v in self.pending.items() if not v["answer"] and v["expires"] > now]
            return {"status": dict(self.status), "pending": pend, "events": list(self.events)}

    # -- helpers ------------------------------------------------------------------------
    def _write(self, rec):
        rec = {"ts": round(time.time(), 3), **rec}
        with open(self.file, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def _push(self, text):
        self.event(text)
        if not self.ntfy_topic:
            return

        def send():
            try:
                req = urllib.request.Request(f"https://ntfy.sh/{self.ntfy_topic}", data=text.encode(),
                                             headers={"Title": "TopstepX bot", "Priority": "high"})
                urllib.request.urlopen(req, timeout=10).read()
            except OSError as e:
                self.log("error", error=f"phone alert failed: {e}")
        threading.Thread(target=send, daemon=True).start()


def serve(desk, host, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if self.path == "/state":
                return self._send(200, desk.view())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            if self.path == "/decide":
                return self._send(200, {"ok": desk.decide(int(body.get("id", 0)), bool(body.get("accept")))})
            if self.path == "/mark" and body.get("side") in ("long", "short"):
                desk.mark(body["side"], str(body.get("note", ""))[:300])
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot Desk</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5d6472;--line:#dde1e7;--up:#0f7b4a;--down:#b42318;--accent:#2457d6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--muted:#9aa2b1;--line:#2a2f3a;--up:#3ccf8e;--down:#ff6b5e;--accent:#6c9bff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:18px;margin:0 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.k{color:var(--muted);font-size:12px}.v{font-size:17px;font-variant-numeric:tabular-nums}
.sig{border:2px solid var(--accent)}.sig h2{margin:0 0 8px;font-size:20px}
.long{color:var(--up)}.short{color:var(--down)}
button{font:inherit;padding:12px 18px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;min-width:120px}
button.yes{background:var(--up);border-color:var(--up);color:#fff}button.no{background:var(--down);border-color:var(--down);color:#fff}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}input{font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink);flex:1;min-width:160px}
ul{list-style:none;margin:0;padding:0}li{padding:4px 0;border-bottom:1px solid var(--line);font-size:13px}li span{color:var(--muted);margin-right:8px}
.muted{color:var(--muted)}
</style></head><body><main>
<h1>Bot Desk <span id="mode" class="muted"></span></h1>
<div id="signals"></div>
<div class="card"><div class="grid" id="stats"></div><p id="note" class="muted" style="margin:10px 0 0"></p></div>
<div class="card"><div class="k" style="margin-bottom:8px">Mark YOUR setup right now (saved with a market snapshot)</div>
<div class="row"><input id="mnote" placeholder="optional note, e.g. absorption at London low">
<button class="yes" onclick="mark('long')">My long</button><button class="no" onclick="mark('short')">My short</button></div></div>
<div class="card"><div class="k" style="margin-bottom:6px">Recent</div><ul id="events"></ul></div>
<p class="muted">Click anywhere once so new signals can play a sound.</p>
</main><script>
let seen=new Set(),ctx=null;document.addEventListener('click',()=>{if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)()});
function beep(){if(!ctx)return;const o=ctx.createOscillator(),g=ctx.createGain();o.connect(g);g.connect(ctx.destination);o.frequency.value=880;g.gain.value=.15;o.start();o.stop(ctx.currentTime+.4)}
const esc=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,d=2)=>x==null?'-':Number(x).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
async function decide(id,a){await post('/decide',{id,accept:a});tick()}
async function mark(side){const n=document.getElementById('mnote');await post('/mark',{side,note:n.value});n.value='';tick()}
function stat(k,v,c=''){return `<div><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`}
async function tick(){let s;try{s=await (await fetch('/state')).json()}catch(e){document.getElementById('mode').textContent='(bot offline)';return}
const st=s.status||{};document.getElementById('mode').textContent=st.mode?`- ${st.mode}`:'';
const pos=st.position?`${st.position.side} ${st.position.size} @ ${fmt(st.position.entry)}`:'flat';
document.getElementById('stats').innerHTML=stat('Price',fmt(st.price))+stat('Aggression',st.agg==null?'-':(st.agg>0?'+':'')+fmt(st.agg,3)+` (${st.agg_source||''})`,st.agg>0?'long':st.agg<0?'short':'')
+stat('Position',pos)+stat('Stop',fmt(st.stop))+stat('Day P&L','$'+fmt(st.day_pnl),st.day_pnl>=0?'long':'short')+stat('Losses today',st.losses??'-');
document.getElementById('note').textContent=st.note||'';
document.getElementById('signals').innerHTML=s.pending.map(p=>{const g=p.signal,q=p.mode==='confirm';if(!seen.has(p.id)){seen.add(p.id);beep()}
return `<div class="card sig"><h2 class="${g.side}">${g.side.toUpperCase()} ${g.size} MNQ @ ${fmt(g.entry)}</h2>
<div class="grid">${stat('Stop',fmt(g.stop))}${stat('Target',fmt(g.target))}${stat('Risk','$'+fmt(g.risk_usd,0))}${stat('Reward','$'+fmt(g.reward_usd,0))}${stat('Aggression',fmt(g.agg,3))}${stat('Time left',p.left+'s')}</div>
<p class="muted">${q?'The bot will place this trade only if you accept.':'The bot is taking this one itself. Would you take it? Your answer is logged.'}</p>
<div class="row"><button class="yes" onclick="decide(${p.id},true)">${q?'Accept':'Yes, I would'}</button><button class="no" onclick="decide(${p.id},false)">${q?'Reject':'No'}</button></div></div>`}).join('');
document.getElementById('events').innerHTML=s.events.map(e=>`<li><span>${esc(e.t)}</span>${esc(e.text)}</li>`).join('')}
tick();setInterval(tick,1000);
</script></body></html>"""
