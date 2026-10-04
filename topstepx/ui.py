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
        self.bias_file = None      # set by the bot: where the trader's bias choice is saved
        self.bias_choice = {}      # {"allow": "long"|"short"|"both", "day": "YYYY-MM-DD"}

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

    def set_bias(self, allow, day):
        """The trader's bias for one trading day: "long", "short", "both", or "auto" (the bot's rule)."""
        with self.lock:
            self.bias_choice = {} if allow == "auto" else {"allow": allow, "day": day}
            if self.bias_file:
                with open(self.bias_file, "w") as f:
                    json.dump(self.bias_choice, f)
        self._write({"kind": "bias", "allow": allow, "day": day})
        self.event("bias set by you: " + ({"long": "longs only", "short": "shorts only", "both": "longs and shorts",
                                            "auto": "auto (bot rules)"}[allow]))

    def bias_for(self, day):
        with self.lock:
            c = self.bias_choice
            return c.get("allow") if c.get("day") == day else None

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
            if self.path == "/bias" and body.get("allow") in ("auto", "long", "short", "both"):
                desk.set_bias(body["allow"], desk.status.get("trading_day"))
                return self._send(200, {"ok": True})
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

/* ---- the office scene ---- */
:root{--wall:#d9e2ec;--wall2:#c9d4e0;--floor:#a88f72;--desk:#5c3f2b;--desk2:#4a3122;--suit:#1f2a44;--skin:#e9b991;--hair:#2b2118;--shirt:#f5f7fa;--tie:#b4232c;--chair:#262b33;--frame:#3a4350}
@media (prefers-color-scheme:dark){:root{--wall:#1c2533;--wall2:#162030;--floor:#3b3025;--desk:#4a3324;--desk2:#38261a;--chair:#11151b;--frame:#566070}}
.office{padding:0;overflow:hidden;position:relative;margin-bottom:12px}
.office svg{display:block;width:100%;height:auto;max-height:420px;background:var(--wall)}
.office .scr{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.bubble{position:absolute;left:50%;top:10px;transform:translateX(-10%);max-width:min(46%,380px);background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:14px;padding:8px 12px;font-size:14px;box-shadow:0 4px 14px rgba(0,0,0,.12)}
.bubble:after{content:"";position:absolute;left:18px;bottom:-8px;border:8px solid transparent;border-top-color:var(--card);border-bottom:0}
.ticker{background:#0b1220;color:#cfe3ff;font:13px ui-monospace,Menlo,Consolas,monospace;white-space:nowrap;overflow:hidden;padding:6px 0;border-top:1px solid #1f2b40}
.ticker span{display:inline-block;padding-left:100%;animation:tick 28s linear infinite}
@keyframes tick{to{transform:translateX(-100%)}}
#mgr .pose{display:none}
#mgr.work .p-work,#mgr.coffee .p-coffee,#mgr.alert .p-alert,#mgr.focus .p-work,#mgr.win .p-win,#mgr.loss .p-loss,#mgr.sleep .p-sleep{display:inline}
#mgr .face{display:none}
#mgr .eyes,#mgr .f-sleep[d^="M557"]{visibility:hidden}
#mgr.work .f-calm,#mgr.coffee .f-calm,#mgr.focus .f-focus,#mgr.alert .f-wow,#mgr.win .f-happy,#mgr.loss .f-sad,#mgr.sleep .f-sleep{display:inline}
.breathe{animation:breathe 4s ease-in-out infinite;transform-origin:570px 260px}
@keyframes breathe{50%{transform:translateY(1.5px)}}
#mgr.work .armL{animation:typeL .35s ease-in-out infinite alternate}
#mgr.work .armR{animation:typeR .35s ease-in-out infinite alternate .17s}
#mgr.focus .armL{animation:typeL .9s ease-in-out infinite alternate}
#mgr.focus .armR{animation:typeR .9s ease-in-out infinite alternate .45s}
@keyframes typeL{to{transform:translateY(-3px)}}@keyframes typeR{to{transform:translateY(-3px)}}
.eyes{animation:blink 5s infinite;transform-origin:570px 168px}
@keyframes blink{0%,94%,100%{transform:scaleY(1)}96%{transform:scaleY(.1)}}
#mgr.coffee .sip{animation:sip 6s ease-in-out infinite;transform-origin:612px 214px}
@keyframes sip{0%,60%,100%{transform:rotate(0)}70%,85%{transform:rotate(-38deg) translate(-6px,-14px)}}
#mgr.alert .bang{animation:pop .6s ease-in-out infinite alternate}
@keyframes pop{to{transform:translateY(-6px)}}
#mgr.win .p-win{animation:cheer .5s ease-in-out infinite alternate;transform-origin:570px 210px}
@keyframes cheer{to{transform:translateY(-4px)}}
.zz text{animation:zz 3s ease-in infinite;opacity:0}.zz text:nth-child(2){animation-delay:1s}.zz text:nth-child(3){animation-delay:2s}
@keyframes zz{0%{opacity:0;transform:translate(0,0)}30%{opacity:1}100%{opacity:0;transform:translate(18px,-30px)}}
.confetti rect{animation:fall 2.4s linear infinite}
@keyframes fall{from{transform:translateY(-40px) rotate(0)}to{transform:translateY(360px) rotate(540deg)}}
.flash{animation:flash 1s steps(2) infinite}@keyframes flash{50%{opacity:.35}}
.glow-long{filter:drop-shadow(0 0 10px rgba(60,207,142,.7))}.glow-short{filter:drop-shadow(0 0 10px rgba(255,107,94,.7))}
.dim{opacity:.55}
#office:has(#mgr.coffee) #cup{display:none}
@media (prefers-reduced-motion:reduce){.office *{animation:none!important}}
</style></head><body><main>
<header><h1>Bot Desk</h1><span id="mode" class="pill">connecting...</span><span id="clock" class="pill"></span><span id="sess" class="pill"></span>
<span id="health" class="pill"></span><span id="strat" class="pill"></span></header>
<div class="card office" id="office">
<div class="bubble" id="bubble">Morning. Pulling up the book...</div>
<svg viewBox="0 0 900 380" role="img" aria-label="The trading office: the portfolio manager at his desk, reacting to what the bot is doing">
 <defs>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop id="sky1" offset="0" stop-color="#0d1b3a"/><stop id="sky2" offset="1" stop-color="#27406e"/></linearGradient>
  <pattern id="mono" width="14" height="14" patternUnits="userSpaceOnUse"><rect width="14" height="14" fill="#2a1d14"/>
   <circle cx="3.5" cy="3.5" r="1.6" fill="none" stroke="#c9a24a" stroke-width=".9"/><path d="M10.5,8 l2,2.5 -2,2.5 -2,-2.5 z" fill="#c9a24a"/>
   <path d="M10.5,1.5 v3 M9,3 h3" stroke="#c9a24a" stroke-width=".8"/><circle cx="3.5" cy="10.5" r=".9" fill="#c9a24a"/></pattern>
  <linearGradient id="lean" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b06ad9"/><stop offset="1" stop-color="#6b2a99"/></linearGradient>
  <clipPath id="win"><rect x="40" y="40" width="250" height="170" rx="4"/></clipPath>
 </defs>
 <rect width="900" height="380" fill="var(--wall)"/>
 <rect y="300" width="900" height="80" fill="var(--floor)"/>
 <rect y="296" width="900" height="6" fill="var(--wall2)"/>
 <!-- window with skyline -->
 <g clip-path="url(#win)">
  <rect x="40" y="40" width="250" height="170" fill="url(#sky)"/>
  <circle id="sun" cx="240" cy="78" r="16" fill="#ffd36b"/>
  <circle id="moon" cx="240" cy="74" r="12" fill="#f3f1e6"/>
  <g fill="#0f1a2c" id="skyline">
   <rect x="40" y="140" width="34" height="70"/><rect x="78" y="112" width="26" height="98"/><rect x="108" y="128" width="40" height="82"/>
   <rect x="152" y="96" width="22" height="114"/><polygon points="152,96 163,74 174,96"/><rect x="178" y="134" width="36" height="76"/>
   <rect x="218" y="118" width="28" height="92"/><rect x="250" y="146" width="40" height="64"/>
  </g>
  <g id="lights" fill="#ffd977">
   <rect x="84" y="124" width="4" height="5"/><rect x="94" y="140" width="4" height="5"/><rect x="116" y="140" width="4" height="5"/><rect x="132" y="156" width="4" height="5"/>
   <rect x="158" y="110" width="4" height="5"/><rect x="158" y="134" width="4" height="5"/><rect x="186" y="148" width="4" height="5"/><rect x="224" y="130" width="4" height="5"/>
   <rect x="234" y="160" width="4" height="5"/><rect x="258" y="160" width="4" height="5"/><rect x="48" y="156" width="4" height="5"/><rect x="200" y="170" width="4" height="5"/>
  </g>
 </g>
 <rect x="40" y="40" width="250" height="170" rx="4" fill="none" stroke="var(--frame)" stroke-width="8"/>
 <line x1="165" y1="40" x2="165" y2="210" stroke="var(--frame)" stroke-width="5"/>
 <!-- wall clock (New York time) -->
 <g transform="translate(800,78)"><circle r="30" fill="#fff" stroke="var(--frame)" stroke-width="5"/>
  <line id="hHand" y2="-15" stroke="#1d2433" stroke-width="4" stroke-linecap="round"/><line id="mHand" y2="-23" stroke="#1d2433" stroke-width="3" stroke-linecap="round"/><circle r="3" fill="#b4232c"/>
  <text y="48" text-anchor="middle" font-size="11" fill="var(--muted)" font-family="system-ui">NEW YORK</text></g>
 <!-- framed chart on the wall -->
 <g transform="translate(640,40)"><rect width="110" height="76" fill="#fff" stroke="var(--frame)" stroke-width="5"/>
  <polyline points="10,62 30,50 45,56 62,34 78,40 98,16" fill="none" stroke="#13804e" stroke-width="3"/></g>
 <!-- chair -->
 <rect x="515" y="128" width="110" height="150" rx="22" fill="var(--chair)"/>
 <!-- the manager -->
 <g id="mgr" class="work"><g class="breathe">
  <path d="M520,262 L528,214 Q570,190 612,214 L620,262 Z" fill="url(#mono)"/>
  <path d="M556,206 L548,250 M584,206 L592,250" stroke="#c9a24a" stroke-width="1.5" opacity=".7"/>
  <path d="M556,206 L570,240 L584,206 Z" fill="var(--shirt)"/>
  <path d="M566,212 L574,212 L577,238 L570,248 L563,238 Z" fill="var(--tie)"/>
  <rect x="562" y="186" width="16" height="16" fill="var(--skin)"/>
  <circle cx="570" cy="168" r="25" fill="var(--skin)"/>
  <path d="M545,164 Q546,138 572,140 Q596,141 596,162 Q588,150 572,151 Q556,151 545,164 Z" fill="var(--hair)"/>
  <circle cx="545" cy="170" r="4" fill="var(--skin)"/><circle cx="595" cy="170" r="4" fill="var(--skin)"/>
  <circle cx="545" cy="176" r="2.6" fill="#dff6ff" stroke="#9fd8ff" stroke-width=".8"/><circle cx="595" cy="176" r="2.6" fill="#dff6ff" stroke="#9fd8ff" stroke-width=".8"/>
  <path d="M553,204 Q570,236 587,204" fill="none" stroke="#e2b23a" stroke-width="3.2" stroke-dasharray="2.4 1.6"/>
  <path d="M570,228 l7,9 -7,9 -7,-9 z" fill="#dff6ff" stroke="#e2b23a" stroke-width="1.6"/><path d="M567,233 l3,-2" stroke="#fff" stroke-width="1"/>
  <g class="shades"><rect x="551" y="161" width="17" height="11" rx="4" fill="#0d0f14"/><rect x="572" y="161" width="17" height="11" rx="4" fill="#0d0f14"/>
   <path d="M568,165 h4 M551,164 l-6,-2 M589,164 l6,-2" stroke="#c9a24a" stroke-width="2"/><path d="M554,164 l5,-1.5 M575,164 l5,-1.5" stroke="#8fa3c0" stroke-width="1.4"/></g>
  <g class="eyes"><g class="face f-calm f-focus f-wow f-happy f-sad"><circle cx="561" cy="168" r="2.6" fill="#1d2433"/><circle cx="579" cy="168" r="2.6" fill="#1d2433"/></g></g>
  <g class="face f-sleep"><path d="M557,169 q4,3 8,0 M575,169 q4,3 8,0" stroke="#1d2433" stroke-width="2" fill="none"/></g>
  <path class="face f-calm" d="M563,180 q7,4 14,0" stroke="#7a3b2e" stroke-width="2" fill="none" stroke-linecap="round"/>
  <path class="face f-focus" d="M563,181 h14" stroke="#7a3b2e" stroke-width="2" stroke-linecap="round"/>
  <path class="face f-happy" d="M560,178 q10,10 20,0 z" fill="#7a3b2e"/>
  <ellipse class="face f-wow" cx="570" cy="182" rx="4" ry="5" fill="#7a3b2e"/>
  <path class="face f-sad" d="M563,184 q7,-5 14,0" stroke="#7a3b2e" stroke-width="2" fill="none" stroke-linecap="round"/>
  <path class="face f-sleep" d="M564,182 h12" stroke="#7a3b2e" stroke-width="2" stroke-linecap="round"/>
  <!-- poses: arms -->
  <g class="pose p-work"><g class="armL"><path d="M530,220 Q516,246 546,252" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="548" cy="252" r="7" fill="var(--skin)"/></g>
   <g class="armR"><path d="M610,220 Q624,246 594,252" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="592" cy="252" r="7" fill="var(--skin)"/><rect x="597" y="242" width="7" height="9" rx="2" fill="#e2b23a" stroke="#a87b16"/></g></g>
  <g class="pose p-coffee"><path d="M530,220 Q516,246 552,254" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="554" cy="254" r="7" fill="var(--skin)"/>
   <g class="sip"><path d="M610,220 Q630,236 614,220" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><path d="M610,222 Q634,238 616,214" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/>
    <path d="M603,192 L625,192 L621,222 L607,222 Z" fill="#fbfbfb" stroke="#c7ccd4"/><path d="M604,197 L624,197" stroke="#dfe3e8" stroke-width="2"/>
    <ellipse cx="614" cy="193" rx="10" ry="2.6" fill="url(#lean)"/><circle cx="614" cy="214" r="7" fill="var(--skin)"/></g></g>
  <g class="pose p-alert"><path d="M530,220 Q516,246 548,252" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="548" cy="252" r="7" fill="var(--skin)"/>
   <path d="M610,218 Q650,200 690,190" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="694" cy="189" r="7" fill="var(--skin)"/>
   <g class="bang"><circle cx="570" cy="112" r="15" fill="#e8a400"/><text x="570" y="119" text-anchor="middle" font-size="22" font-weight="700" fill="#fff" font-family="system-ui">!</text></g></g>
  <g class="pose p-win"><path d="M530,218 Q512,186 520,150" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="520" cy="146" r="8" fill="var(--skin)"/>
   <path d="M610,218 Q628,186 620,150" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="620" cy="146" r="8" fill="var(--skin)"/></g>
  <g class="pose p-loss"><path d="M530,220 Q526,190 552,152" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="553" cy="150" r="8" fill="var(--skin)"/>
   <path d="M610,220 Q614,190 588,152" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><circle cx="587" cy="150" r="8" fill="var(--skin)"/></g>
  <g class="pose p-sleep"><path d="M530,220 Q520,246 560,250" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/><path d="M610,220 Q620,246 580,250" stroke="url(#mono)" stroke-width="14" fill="none" stroke-linecap="round"/>
   <g class="zz" font-family="system-ui" font-weight="700" fill="var(--muted)"><text x="600" y="140" font-size="14">z</text><text x="608" y="128" font-size="17">z</text><text x="618" y="114" font-size="21">Z</text></g></g>
 </g></g>
 <!-- desk -->
 <rect x="250" y="255" width="640" height="16" rx="3" fill="var(--desk)"/>
 <rect x="270" y="271" width="600" height="70" fill="var(--desk2)"/>
 <rect x="510" y="282" width="120" height="26" rx="3" fill="#c9a24a"/><text x="570" y="300" text-anchor="middle" font-size="11" font-weight="700" fill="#3b2a12" font-family="system-ui" letter-spacing="1">PORTFOLIO MGR</text>
 <rect x="532" y="248" width="76" height="8" rx="2" fill="#2c323c"/>
 <g transform="translate(262,223)" id="cup"><path d="M0,0 L24,0 L20,32 L4,32 Z" fill="#fbfbfb" stroke="#c7ccd4"/><path d="M1,6 L23,6" stroke="#dfe3e8" stroke-width="2.5"/>
  <path d="M2,-3 L22,-3 L24,0 L0,0 Z" fill="#f1f3f5" stroke="#c7ccd4"/><ellipse cx="12" cy="-3" rx="10" ry="2.6" fill="url(#lean)"/>
  <rect x="15" y="-16" width="2.5" height="14" rx="1" fill="#ff4fa3" transform="rotate(14 16 -9)"/></g>
 <!-- monitors -->
 <g id="monL"><rect x="300" y="128" width="190" height="118" rx="6" fill="#1a1f27"/><rect x="306" y="134" width="178" height="104" fill="#0b1220"/>
  <rect x="384" y="246" width="22" height="9" fill="#1a1f27"/>
  <text x="314" y="152" class="scr" font-size="11" fill="#6c9bff">MNQ</text><text id="mPrice" x="476" y="152" text-anchor="end" class="scr" font-size="14" font-weight="700" fill="#e8eef8">-</text>
  <polyline id="spark" points="" fill="none" stroke="#3ccf8e" stroke-width="2"/>
  <text id="mAgg" x="314" y="232" class="scr" font-size="10" fill="#98a1b2">aggression -</text></g>
 <g id="monR"><rect x="650" y="128" width="190" height="118" rx="6" fill="#1a1f27"/><rect x="656" y="134" width="178" height="104" fill="#0b1220"/>
  <rect x="734" y="246" width="22" height="9" fill="#1a1f27"/>
  <text x="664" y="152" class="scr" font-size="11" fill="#6c9bff" id="rTitle">POSITION</text>
  <text id="rMain" x="745" y="186" text-anchor="middle" class="scr" font-size="20" font-weight="700" fill="#e8eef8">FLAT</text>
  <text id="rSub" x="745" y="208" text-anchor="middle" class="scr" font-size="12" fill="#98a1b2">-</text>
  <text id="rDay" x="745" y="230" text-anchor="middle" class="scr" font-size="11" fill="#98a1b2">day -</text></g>
 <g class="confetti" id="confetti"></g>
 <!-- plant -->
 <g transform="translate(70,300)"><rect x="-18" y="-6" width="36" height="40" rx="4" fill="#7b5a43"/><path d="M0,-6 C-30,-40 -20,-70 0,-90 C20,-70 30,-40 0,-6" fill="#2f7d4f"/><path d="M0,-6 C-40,-20 -46,-50 -30,-60 M0,-6 C40,-20 46,-50 30,-60" stroke="#2f7d4f" stroke-width="10" fill="none" stroke-linecap="round"/></g>
</svg>
<div class="ticker"><span id="tape">MNQ desk is opening...</span></div>
</div>
<div id="signals"></div>
<div class="cols">
 <div class="card"><h2>Market</h2><div class="hero"><div><div class="k">MNQ price</div><div class="big" id="price">-</div></div>
  <div><div class="k">Aggression (15 min)</div><div class="big" id="agg">-</div><div class="k" id="aggsrc"></div></div></div>
  <p id="note" class="muted" style="margin:10px 0 0"></p></div>
 <div class="card"><h2>Position</h2><div id="trade"></div></div>
 <div class="card"><h2>Today</h2><div class="grid" id="today"></div><div id="combine" style="margin-top:12px"></div></div>
 <div class="card"><h2>Setup</h2><div class="grid" id="plan"></div></div>
 <div class="card"><h2>Bias for today</h2><div class="row" id="biasbtns" style="margin-bottom:10px"></div><div id="votes"></div></div>
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
async function setBias(a){await post('/bias',{allow:a});tick()}
function biasPanel(st){const cur=st.bias_override||'auto',lab={auto:'Auto (bot rules)',long:'Longs only',short:'Shorts only',both:'Both'};
 set('biasbtns',['auto','long','short','both'].map(a=>`<button onclick="setBias('${a}')" class="${a===cur?(a==='long'?'yes':a==='short'?'no':'paper'):''}" style="${a===cur&&a!=='long'&&a!=='short'?'background:var(--accent);color:#fff;border-color:var(--accent)':''}">${lab[a]}</button>`).join(''));
 const v=st.votes||[];const n={long:0,short:0};v.forEach(x=>{if(x.side in n)n[x.side]++});
 set('votes',(v.length?`<table>${v.map(x=>`<tr><td>${esc(x.source)}</td><td class="${x.side==='long'?'pos':x.side==='short'?'neg':'muted'}"><b>${x.side?esc(x.side.toUpperCase()):'-'}</b></td><td class="k">${esc(x.why)}</td></tr>`).join('')}</table>`:'')
 +`<p class="k" style="margin:8px 0 0">Sources: ${n.long} long · ${n.short} short. The bot is trading: <b>${esc(st.allow_now||'-')}</b>${cur==='auto'?' (its own rule)':' (your setting)'}.</p>`)}
async function delLevel(id){await post('/levels/remove',{id});tick()}
function stat(k,v,c=''){return `<div><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`}
function set(id,h){document.getElementById(id).innerHTML=h}
function levelRows(ls,price){if(!ls.length)return '<tr><td class="muted">No levels yet.</td></tr>';
return '<tr class="k"><td>Level</td><td>Note</td><td class="num">Distance</td><td>Status</td><td class="num">Held / broke</td><td></td></tr>'+ls.map(l=>`<tr>
<td><b>${fmt(l.price)}</b></td><td>${esc(l.note)}</td><td class="num">${l.dist==null?'-':(l.dist>0?'+':'')+fmt(l.dist)}</td>
<td>${esc(l.state)}${l.last?` <span class="${l.last==='HELD'?'pos':'neg'}">(last ${esc(l.last)})</span>`:''}</td><td class="num">${l.held} / ${l.broke}</td>
<td class="num"><button style="padding:4px 10px" onclick="delLevel(${l.id})">Remove</button></td></tr>`).join('')}

let hist=[],lastTape='',confettiOn=false;
function sceneSky(h){const s1=document.getElementById('sky1'),s2=document.getElementById('sky2'),sun=document.getElementById('sun'),moon=document.getElementById('moon'),lt=document.getElementById('lights');
 let a,b,day=false,dusk=false;if(h>=7&&h<17){a='#7fb6ec';b='#cfe6fb';day=true}else if((h>=17&&h<20)||(h>=5&&h<7)){a='#3d3a78';b='#f29a6b';dusk=true}else{a='#0b1530';b='#24365e'}
 s1.setAttribute('stop-color',a);s2.setAttribute('stop-color',b);sun.style.display=day||dusk?'':'none';sun.setAttribute('cy',dusk?150:78);moon.style.display=day?'none':(dusk?'none':'');lt.style.display=day?'none':''}
function sceneClock(c){const m=/(\d+):(\d+)/.exec(c||'');if(!m)return;const h=+m[1],mi=+m[2];
 document.getElementById('hHand').setAttribute('transform',`rotate(${(h%12)*30+mi/2})`);document.getElementById('mHand').setAttribute('transform',`rotate(${mi*6})`);sceneSky(h)}
function confetti(on){const g=document.getElementById('confetti');if(on===confettiOn)return;confettiOn=on;if(!on){g.innerHTML='';return}
 const cols=['#3ccf8e','#ffd36b','#6c9bff','#ff6b5e','#ffffff'];let h='';for(let i=0;i<34;i++){const x=260+Math.random()*620,d=(Math.random()*2.4).toFixed(2),w=4+Math.random()*5;
 h+=`<rect x="${x.toFixed(0)}" y="0" width="${w.toFixed(1)}" height="${(w*1.8).toFixed(1)}" fill="${cols[i%5]}" style="animation-delay:${d}s"/>`}g.innerHTML=h}
function scene(s,offline){const mg=document.getElementById('mgr'),b=document.getElementById('bubble'),st=(s&&s.status)||{};
 let mood='work',say=st.note||'Watching the tape.';const t=st.trade,lr=st.last_result,now=Date.now()/1000;
 if(offline){mood='sleep';say='Zzz... the bot is offline. Start it again and I am back at the desk.'}
 else if(s.pending&&s.pending.length){const g=s.pending[0].signal;mood='alert';say=`Setup! ${g.side.toUpperCase()} ${g.size} MNQ @ ${fmt(g.entry)}. Stop ${fmt(g.stop)}, target ${fmt(g.target)}.`+(s.pending[0].mode==='confirm'?' Your call: Accept or Reject below.':' Taking it.')}
 else if(t){mood='focus';say=`Managing the ${t.side}: ${t.open_pnl==null?'':money(t.open_pnl)+' open'}${t.open_r==null?'':' ('+(t.open_r>0?'+':'')+fmt(t.open_r)+'R)'}. Stop ${fmt(t.stop)}.`}
 else if(lr&&now-lr.ts<900){if(lr.pnl>0){mood='win';say=`Booked ${money(lr.pnl)} on the ${lr.side}. That is how it is done.`}else{mood='loss';say=`Stopped on the ${lr.side}: ${money(lr.pnl)}. One loss, we are done for the day. Discipline.`}}
 else if(!st.can_enter&&st.session&&(st.session==='Closed'||/outside the entry window/.test(st.why_not||''))){mood='coffee';say=(st.note?st.note+'. ':'')+'Sipping slow until the session.'}
 else if(st.why_not&&/daily stop/.test(st.why_not)){mood='coffee';say='Done for the day: '+st.why_not+'.'}
 mg.setAttribute('class',mood);b.textContent=say;confetti(mood==='win');
 document.getElementById('office').classList.toggle('dim',!!offline);
 sceneClock(st.clock);
 if(offline)return;
 if(st.price!=null){hist.push(st.price);if(hist.length>150)hist.shift()}
 document.getElementById('mPrice').textContent=fmt(st.price);
 if(hist.length>1){const lo=Math.min(...hist),hi=Math.max(...hist),r=(hi-lo)||1;document.getElementById('spark').setAttribute('points',hist.map((p,i)=>`${(314+i*(162/(hist.length-1))).toFixed(1)},${(218-(p-lo)/r*56).toFixed(1)}`).join(' '));
  document.getElementById('spark').setAttribute('stroke',hist[hist.length-1]>=hist[0]?'#3ccf8e':'#ff6b5e')}
 document.getElementById('mAgg').textContent=st.agg==null?'aggression -':`aggression ${(st.agg>0?'+':'')+fmt(st.agg,3)} ${st.agg>0.1?'buyers':st.agg<-0.1?'sellers':'balanced'}`;
 const mL=document.getElementById('monL'),mR=document.getElementById('monR');
 mL.setAttribute('class',t?(t.side==='long'?'glow-long':'glow-short'):'');mR.setAttribute('class',t?(t.side==='long'?'glow-long':'glow-short'):mood==='alert'?'flash':'');
 const rm=document.getElementById('rMain'),rs=document.getElementById('rSub');
 if(t){rm.textContent=`${t.side.toUpperCase()} ${t.size}`;rm.setAttribute('fill',t.side==='long'?'#3ccf8e':'#ff6b5e');rs.textContent=t.open_pnl==null?'':money(t.open_pnl)+(t.open_r==null?'':'  '+(t.open_r>0?'+':'')+fmt(t.open_r)+'R')}
 else if(mood==='alert'){const g=s.pending[0].signal;rm.textContent=`${g.side.toUpperCase()}?`;rm.setAttribute('fill','#ffd36b');rs.textContent='@ '+fmt(g.entry)}
 else{rm.textContent='FLAT';rm.setAttribute('fill','#e8eef8');rs.textContent=st.can_enter?'ready':'waiting'}
 document.getElementById('rDay').textContent='day '+money(st.day_pnl)+'  ·  '+(st.strategy||'');
 const b2=st.bias||{},g=st.gamma||{};const tape=`MNQ ${fmt(st.price)}   ·   DAY P&L ${money(st.day_pnl)}   ·   ${b2.why?esc(b2.why).toUpperCase()+' → '+(b2.allow==='both'?'LONGS & SHORTS':b2.allow==='none'?'NO TRADE':(b2.allow||'').toUpperCase()+'S ONLY'):''}   ·   ${g.flip?'GAMMA FLIP '+fmt(g.flip)+'   ·   CALL WALL '+fmt(g.call_wall)+'   ·   PUT WALL '+fmt(g.put_wall):''}   ·   ${st.combine?'COMBINE '+money(st.combine.profit)+' / '+money(st.combine.target):''}   ·   ${esc(st.session||'')} SESSION`;
 if(tape!==lastTape){lastTape=tape;document.getElementById('tape').textContent=tape}}
async function tick(){let s;try{s=await (await fetch('/state')).json()}catch(e){set('mode','bot offline');scene(null,true);return}
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
const b=st.bias||{};set('plan',stat('Bias',b.why?esc(b.why):'-')+stat('Bot allows',b.allow==null?'-':b.allow==='both'?'longs and shorts':b.allow==='none'?'no trade':esc(b.allow)+'s only',b.allow==='long'?'long':b.allow==='short'?'short':'')
+stat('Range high',fmt(st.range_high))+stat('Range low',fmt(st.range_low))+stat('Range width',st.range_high&&st.range_low?fmt(st.range_high-st.range_low)+' pts':'-'));
const g=st.gamma||{},d=x=>st.price&&x?` <span class="k">(${x-st.price>0?'+':''}${fmt(x-st.price)})</span>`:'';
set('gamma',g.flip?`<div class="grid">${stat('Regime',esc((g.regime||'').replace('_',' ')),g.regime==='positive_gamma'?'pos':'neg')}${stat('Gamma flip',fmt(g.flip)+d(g.flip))}${stat('Call wall',fmt(g.call_wall)+d(g.call_wall))}${stat('Put wall',fmt(g.put_wall)+d(g.put_wall))}</div>
<p class="k" style="margin:8px 0 0">${st.price?(st.price>g.flip?'Price above the flip: a short fades toward it.':'Price below the flip: a long fades toward it.'):''} Snapshot ${esc((g.computed||'').slice(0,16).replace('T',' '))} UTC.</p>`:'<p class="muted" style="margin:0">No gamma snapshot yet (updates around midday ET).</p>');
const w=st.walls||{above:[],below:[]};const wr=(arr,cls)=>arr.map(x=>`<tr><td class="${cls}">${fmt(x[0])}</td><td class="num">${fmt(x[1],0)} contracts</td><td class="num k">${st.price?((x[0]-st.price>0?'+':'')+fmt(x[0]-st.price)+' pts'):''}</td></tr>`).join('');
set('walls',(w.above.length||w.below.length)?`<tr class="k"><td colspan="3">Above (sell walls)</td></tr>${wr([...w.above].reverse(),'short')||'<tr><td class="muted" colspan="3">none</td></tr>'}<tr class="k"><td colspan="3">Below (buy walls)</td></tr>${wr(w.below,'long')||'<tr><td class="muted" colspan="3">none</td></tr>'}`:'<tr><td class="muted">No big resting orders right now (or the order book stream is down).</td></tr>');
set('levels',levelRows(s.levels||[],st.price));biasPanel(st);
set('signals',s.pending.map(p=>{const g=p.signal,q=p.mode==='confirm';if(!seen.has(p.id)){seen.add(p.id);beep()}
return `<div class="card sig"><h3 class="${g.side}">${g.side.toUpperCase()} ${g.size} MNQ @ ${fmt(g.entry)}</h3>
<div class="grid">${stat('Stop',fmt(g.stop))}${stat('Target',fmt(g.target))}${stat('Risk',money(g.risk_usd))}${stat('Reward',money(g.reward_usd))}${stat('Gamma',g.gamma?(g.gamma.toward_flip?'toward flip':'away from flip'):'-',g.gamma?(g.gamma.toward_flip?'pos':'neg'):'')}${stat('Time left',p.left+'s')}</div>
<p class="muted">${q?'The bot will place this trade only if you accept.':'The bot is taking this one itself. Would you take it? Your answer is logged.'}</p>
<div class="row"><button class="yes" onclick="decide(${p.id},true)">${q?'Accept':'Yes, I would'}</button><button class="no" onclick="decide(${p.id},false)">${q?'Reject':'No'}</button></div></div>`}).join(''));
set('events',s.events.map(e=>`<li><span>${esc(e.t)}</span>${esc(e.text)}</li>`).join(''));scene(s,false)}
tick();setInterval(tick,1000);
</script></body></html>
"""
