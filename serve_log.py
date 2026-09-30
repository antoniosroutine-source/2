"""Local dashboard server: serves dashboard.html, the trade log, and the kill switch.

Endpoints (http://127.0.0.1:8765):
  GET  /                 dashboard
  GET  /log?limit=N      last N log events (JSON array)
  GET  /status           master switch, strategies, running state
  POST /master           {"on": true|false}      master kill switch
  POST /strategy/{id}    {"enabled": true|false}  per-strategy toggle
A strategy's agent process runs only while master AND its own toggle are on.
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config

LOCK = threading.Lock()
PROCS = {}          # strategy id -> Popen
LAST_START = {}     # strategy id -> time of last start (restart backoff)
RESTART_BACKOFF_SEC = 30


def load_state():
    with open(config.STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    tmp = config.STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, config.STATE_FILE)


def read_log(limit):
    if not os.path.exists(config.LOG_FILE):
        return []
    with open(config.LOG_FILE) as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def stop_proc(sid):
    p = PROCS.pop(sid, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def reconcile():
    """Start/stop agent processes so they match the saved switches."""
    with LOCK:
        state = load_state()
        for sid, s in state["strategies"].items():
            want = state["master"] and s.get("enabled")
            p = PROCS.get(sid)
            alive = p is not None and p.poll() is None
            if want and not alive:
                if time.time() - LAST_START.get(sid, 0) < RESTART_BACKOFF_SEC:
                    continue
                script = os.path.join(config.HERE, s["script"])
                PROCS[sid] = subprocess.Popen([sys.executable, script], cwd=config.HERE)
                LAST_START[sid] = time.time()
            elif not want and alive:
                stop_proc(sid)
            elif not want:
                PROCS.pop(sid, None)


def status():
    state = load_state()
    for sid, s in state["strategies"].items():
        p = PROCS.get(sid)
        s["running"] = p is not None and p.poll() is None
    return state


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/dashboard.html"):
            with open(os.path.join(config.HERE, "dashboard.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if url.path == "/log":
            limit = int(parse_qs(url.query).get("limit", ["500"])[0])
            return self._send(200, read_log(max(1, min(limit, 5000))))
        if url.path == "/status":
            return self._send(200, status())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": "invalid JSON"})
        with LOCK:
            state = load_state()
            if url.path == "/master":
                state["master"] = bool(body.get("on"))
            elif url.path.startswith("/strategy/"):
                sid = url.path.split("/", 2)[2]
                if sid not in state["strategies"]:
                    return self._send(404, {"error": f"unknown strategy {sid}"})
                state["strategies"][sid]["enabled"] = bool(body.get("enabled"))
            else:
                return self._send(404, {"error": "not found"})
            save_state(state)
        reconcile()
        self._send(200, status())

    def log_message(self, *args):
        pass


def main():
    stop_flag = threading.Event()

    def loop():
        while not stop_flag.is_set():
            try:
                reconcile()
            except Exception as e:  # keep the supervisor alive
                print("reconcile error:", e, flush=True)
            stop_flag.wait(2)

    threading.Thread(target=loop, daemon=True).start()
    server = ThreadingHTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), Handler)
    print(f"Dashboard: http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_flag.set()
        with LOCK:
            for sid in list(PROCS):
                stop_proc(sid)
        server.server_close()


if __name__ == "__main__":
    main()
