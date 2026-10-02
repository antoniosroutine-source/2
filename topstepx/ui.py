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
        self.levels = None         # MyLevels, attached by the bot

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
            out = {"status": dict(self.status), "pending": pend, "events": list(self.events)}
        out["levels"] = self.levels.view() if self.levels else []
        return out

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
            if self.path == "/levels/add" and desk.levels:
                from mylevels import parse_price
                try:
                    price = parse_price(body.get("price"))
                except (TypeError, ValueError):
                    return self._send(400, {"error": "enter a price like 30900 or 30,900.25"})
                lv = desk.levels.add(price, str(body.get("note", ""))[:120])
                desk.event(f"level added: {lv['price']:,.2f} {lv['note']}")
                return self._send(200, {"ok": True})
            if self.path == "/levels/remove" and desk.levels:
                return self._send(200, {"ok": desk.levels.remove(int(body.get("id", 0)))})
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
:root{--bg:#f4f6f9;--card:#fff;--ink:#141821;--muted:#5b6474;--line:#dde2ea;--up:#0f7b4a;--down:#b42318;--accent:#2457d6;--warn:#a15c00;--soft:#eef2f8}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--card:#161a22;--ink:#e7eaf0;--muted:#98a1b2;--line:#283040;--up:#3ccf8e;--down:#ff6b5e;--accent:#6c9bff;--warn:#f0b35b;--soft:#1d2330}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:14px 16px 32px}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px}
h1{font-size:18px;margin:0 8px 0 0}h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 10px;font-weight:600}
.pill{padding:3px 10px;border-radius:999px;background:var(--soft);font-size:13px;white-space:nowrap}
.pill.live{background:var(--down);color:#fff}.pill.paper{background:var(--accent);color:#fff}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--up)}.dot.bad{background:var(--down)}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.hero{display:flex;flex-wrap:wrap;gap:22px;align-items:baseline}
.big{font-size:32px;font-weight:600;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
.k{color:var(--muted);font-size:12px}.v{font-size:16px;font-variant-numeric:tabular-nums}
.long,.pos{color:var(--up)}.short,.neg{color:var(--down)}.warn{color:var(--warn)}.muted{color:var(--muted)}
.sig{border:2px solid var(--accent);margin-bottom:12px}.sig h3{margin:0 0 8px;font-size:20px}
.bar{height:8px;border-radius:4px;background:var(--soft);overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:var(--up)}
button{font:inherit;padding:10px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button.yes{background:var(--up);border-color:var(--up);color:#fff}button.no{background:var(--down);border-color:var(--down);color:#fff}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input{font:inherit;padding:9px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink);flex:1;min-width:140px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td{padding:5px 4px;border-top:1px solid var(--line);vertical-align:top}
tr:first-child td{border-top:0}.num{text-align:right}
ul{list-style:none;margin:0;padding:0;max-height:260px;overflow:auto}li{padding:4px 0;border-bottom:1px solid var(--line);font-size:13px}li span{color:var(--muted);margin-right:8px}
.scroll{overflow-x:auto}
</style></head><body><main>
<header><h1>Bot Desk</h1><span id="mode" class="pill">connecting...</span><span id="clock" class="pill"></span><span id="sess" class="pill"></span>
<span id="health" class="pill"></span><span id="strat" class="pill"></span></header>
<div id="signals"></div>
<div class="cols">
 <div class="card"><h2>Market</h2><div class="hero"><div><div class="k">MNQ price</div><div class="big" id="price">-</div></div>
  <div><div class="k">Aggression (15 min)</div><div class="big" id="agg">-</div><div class="k" id="aggsrc"></div></div></div>
  <p id="note" class="muted" style="margin:10px 0 0"></p></div>
 <div class="card"><h2>Position</h2><div id="trade"></div></div>
 <div class="card"><h2>Today</h2><div class="grid" id="today"></div><div id="combine" style="margin-top:12px"></div></div>
 <div class="card"><h2>Tonight's setup</h2><div class="grid" id="plan"></div></div>
 <div class="card"><h2>Gamma levels</h2><div id="gamma"></div></div>
 <div class="card"><h2>Order book walls</h2><div class="scroll"><table id="walls"></table></div><p class="k" style="margin:8px 0 0">Resting orders of 100+ contracts and 4x the typical size, nearest first.</p></div>
</div>
<div class="card" style="margin-top:12px"><h2>My levels</h2>
 <p class="k" style="margin:0 0 8px">Type a level from a stream or your chart. The bot alerts you as price gets close and scores every test as HELD or BROKE.</p>
 <form class="row" onsubmit="addLevel(event)"><input id="lprice" inputmode="decimal" placeholder="price, e.g. 30,900" style="max-width:170px" required>
 <input id="lnote" placeholder="note, e.g. sell wall from stream"><button type="submit">Add level</button></form>
 <p id="lerr" class="short" style="margin:6px 0 0"></p><div class="scroll"><table id="levels" style="margin-top:6px"></table></div></div>
<div class="cols" style="margin-top:12px">
 <div class="card"><h2>Mark your setup</h2><p class="k" style="margin:0 0 8px">Saved with a full market snapshot, to compare with the bot later.</p>
  <div class="row"><input id="mnote" placeholder="optional note, e.g. absorption at London low">
  <button class="yes" onclick="mark('long')">My long</button><button class="no" onclick="mark('short')">My short</button></div></div>
 <div class="card"><h2>Recent</h2><ul id="events"></ul></div>
</div>
<p class="muted">Click anywhere once so new signals can play a sound.</p>
</main><script>
let seen=new Set(),ctx=null;document.addEventListener('click',()=>{if(!ctx)ctx=new (window.AudioContext||window.webkitAudioContext)()});
function beep(){if(!ctx)return;const o=ctx.createOscillator(),g=ctx.createGain();o.connect(g);g.connect(ctx.destination);o.frequency.value=880;g.gain.value=.15;o.start();o.stop(ctx.currentTime+.4)}
const esc=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,d=2)=>x==null||x===''?'-':Number(x).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const money=x=>x==null?'-':(x<0?'-$':'$')+fmt(Math.abs(x));
const sgn=x=>x==null?'':(x>0?'pos':x<0?'neg':'');
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
async function decide(id,a){await post('/decide',{id,accept:a});tick()}
async function mark(side){const n=document.getElementById('mnote');await post('/mark',{side,note:n.value});n.value='';tick()}
async function addLevel(e){e.preventDefault();const p=document.getElementById('lprice'),n=document.getElementById('lnote'),er=document.getElementById('lerr');
const r=await post('/levels/add',{price:p.value,note:n.value});er.textContent=r.error||'';if(r.ok){p.value='';n.value=''}tick()}
async function delLevel(id){await post('/levels/remove',{id});tick()}
function stat(k,v,c=''){return `<div><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`}
function set(id,h){document.getElementById(id).innerHTML=h}
function levelRows(ls,price){if(!ls.length)return '<tr><td class="muted">No levels yet.</td></tr>';
return '<tr class="k"><td>Level</td><td>Note</td><td class="num">Distance</td><td>Status</td><td class="num">Held / broke</td><td></td></tr>'+ls.map(l=>`<tr>
<td><b>${fmt(l.price)}</b></td><td>${esc(l.note)}</td><td class="num">${l.dist==null?'-':(l.dist>0?'+':'')+fmt(l.dist)}</td>
<td>${esc(l.state)}${l.last?` <span class="${l.last==='HELD'?'pos':'neg'}">(last ${esc(l.last)})</span>`:''}</td><td class="num">${l.held} / ${l.broke}</td>
<td class="num"><button style="padding:4px 10px" onclick="delLevel(${l.id})">Remove</button></td></tr>`).join('')}
async function tick(){let s;try{s=await (await fetch('/state')).json()}catch(e){set('mode','bot offline');return}
const st=s.status||{},m=document.getElementById('mode');m.textContent=st.mode||'waiting for the bot';m.className='pill '+((st.mode||'').startsWith('LIVE')?'live':'paper');
set('clock',esc(st.clock||''));set('sess',st.session?'Session: '+esc(st.session):'');set('strat',st.strategy?'Strategy: '+esc(st.strategy):'');
const ok=st.stream_ok&&(st.data_age==null||st.data_age<180);
set('health',`<span class="dot ${ok?'':'bad'}"></span>${st.stream_ok?'tape live':'tape down'} · data ${st.data_age==null?'-':st.data_age+'s old'}`);
set('price',fmt(st.price));const a=document.getElementById('agg');a.textContent=st.agg==null?'-':(st.agg>0?'+':'')+fmt(st.agg,3);a.className='big '+(st.agg>0?'pos':st.agg<0?'neg':'');
set('aggsrc',st.agg_source?`from ${esc(st.agg_source)} · ${st.agg>0.1?'buyers in control':st.agg<-0.1?'sellers in control':'balanced'}`:'');
set('note',esc(st.note||''));
const t=st.trade;set('trade',t?`<div class="hero"><div class="big ${t.side}">${t.side.toUpperCase()} ${t.size}</div><div><div class="k">Open P&L</div><div class="big ${sgn(t.open_pnl)}">${money(t.open_pnl)}</div><div class="k">${t.open_r==null?'':(t.open_r>0?'+':'')+fmt(t.open_r)+'R'} · ${t.minutes} min</div></div></div>
<div class="grid" style="margin-top:10px">${stat('Entry',fmt(t.entry))}${stat('Stop',fmt(t.stop),'neg')}${stat('Target',fmt(t.target),'pos')}</div>`
:`<div class="big muted">Flat</div><p class="k" style="margin:6px 0 0">${st.can_enter?'New entries allowed now.':'No new entries: '+esc(st.why_not||'')}</p>`);
set('today',stat('Day P&L',money(st.day_pnl),sgn(st.day_pnl))+stat('Trades',st.trades??'-')+stat('Losses',`${st.losses??'-'} / ${st.max_losses??'-'}`,st.losses>=st.max_losses?'neg':'')
+stat('Day cap',money(st.day_cap)));
const c=st.combine;set('combine',c?`<div class="k">Combine progress: ${money(c.profit)} of ${money(c.target)} · balance ${money(c.balance)}</div><div class="bar"><i style="width:${Math.max(0,Math.min(100,c.profit/c.target*100))}%"></i></div>`:'');
const b=st.bias||{};set('plan',stat('NY session',b.why?esc(b.why):'-')+stat('Bot allows',b.allow==null?'-':b.allow==='both'?'longs and shorts':b.allow==='none'?'no trade':esc(b.allow)+'s only',b.allow==='long'?'long':b.allow==='short'?'short':'')
+stat('Asia range high',fmt(st.range_high))+stat('Asia range low',fmt(st.range_low))+stat('Range width',st.range_high&&st.range_low?fmt(st.range_high-st.range_low)+' pts':'-'));
const g=st.gamma||{},d=x=>st.price&&x?` <span class="k">(${x-st.price>0?'+':''}${fmt(x-st.price)})</span>`:'';
set('gamma',g.flip?`<div class="grid">${stat('Regime',esc((g.regime||'').replace('_',' ')),g.regime==='positive_gamma'?'pos':'neg')}${stat('Gamma flip',fmt(g.flip)+d(g.flip))}${stat('Call wall',fmt(g.call_wall)+d(g.call_wall))}${stat('Put wall',fmt(g.put_wall)+d(g.put_wall))}</div>
<p class="k" style="margin:8px 0 0">${st.price?(st.price>g.flip?'Price above the flip: a short fades toward it.':'Price below the flip: a long fades toward it.'):''} Snapshot ${esc((g.computed||'').slice(0,16).replace('T',' '))} UTC.</p>`:'<p class="muted" style="margin:0">No gamma snapshot yet (updates around midday ET).</p>');
const w=st.walls||{above:[],below:[]};const wr=(arr,cls)=>arr.map(x=>`<tr><td class="${cls}">${fmt(x[0])}</td><td class="num">${fmt(x[1],0)} contracts</td><td class="num k">${st.price?((x[0]-st.price>0?'+':'')+fmt(x[0]-st.price)+' pts'):''}</td></tr>`).join('');
set('walls',(w.above.length||w.below.length)?`<tr class="k"><td colspan="3">Above (sell walls)</td></tr>${wr([...w.above].reverse(),'short')||'<tr><td class="muted" colspan="3">none</td></tr>'}<tr class="k"><td colspan="3">Below (buy walls)</td></tr>${wr(w.below,'long')||'<tr><td class="muted" colspan="3">none</td></tr>'}`:'<tr><td class="muted">No big resting orders right now (or the order book stream is down).</td></tr>');
set('levels',levelRows(s.levels||[],st.price));
set('signals',s.pending.map(p=>{const g=p.signal,q=p.mode==='confirm';if(!seen.has(p.id)){seen.add(p.id);beep()}
return `<div class="card sig"><h3 class="${g.side}">${g.side.toUpperCase()} ${g.size} MNQ @ ${fmt(g.entry)}</h3>
<div class="grid">${stat('Stop',fmt(g.stop))}${stat('Target',fmt(g.target))}${stat('Risk',money(g.risk_usd))}${stat('Reward',money(g.reward_usd))}${stat('Gamma',g.gamma?(g.gamma.toward_flip?'toward flip':'away from flip'):'-',g.gamma?(g.gamma.toward_flip?'pos':'neg'):'')}${stat('Time left',p.left+'s')}</div>
<p class="muted">${q?'The bot will place this trade only if you accept.':'The bot is taking this one itself. Would you take it? Your answer is logged.'}</p>
<div class="row"><button class="yes" onclick="decide(${p.id},true)">${q?'Accept':'Yes, I would'}</button><button class="no" onclick="decide(${p.id},false)">${q?'Reject':'No'}</button></div></div>`}).join(''));
set('events',s.events.map(e=>`<li><span>${esc(e.t)}</span>${esc(e.text)}</li>`).join(''))}
tick();setInterval(tick,1000);
</script></body></html>
"""
