#!/usr/bin/env python3
"""Servidor del panel en el celular + auto-deploy desde GitHub.

- GET  /            -> index.html
- POST /status      -> guarda estado de la PC
- GET  /status      -> ultimo estado
- POST /cmd, GET /cmd, POST /cmd/ack
- GET  /estado, POST /estado_cmd
- GET  /version     -> informacion del despliegue actual
Cada POLL_INTERVAL segundos consulta GitHub y, si hay un commit nuevo,
descarga el repo, copia los archivos y se reinicia solo.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE, "status.json")
CMD_FILE = os.path.join(BASE, "cmd.json")
ACK_FILE = os.path.join(BASE, "ack.json")
ESTADO_FILE = os.path.join(BASE, "estado_cmd.json")
WOL_SCRIPT = os.path.join(BASE, "wol.py")
PC_MAC = os.environ.get("PC_MAC", "d8:bb:c1:22:1a:d1")

# Auto-deploy
REPO = os.environ.get("REPO_URL", "")
TOKEN = os.environ.get("REPO_TOKEN", "")
VERSION_FILE = os.path.join(BASE, "version.json")
POLL_INTERVAL = int(os.environ.get("DEPLOY_POLL", "30"))
DEPLOY_DIR = os.path.join(BASE, ".deploy")


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def get_version():
    return read_json(VERSION_FILE, {}).get("sha", "local")


# ---------- auto deploy ----------
def github_api(path):
    url = f"https://api.github.com/repos/{REPO}{path}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "panel-autodeploy"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def check_and_deploy():
    if not REPO:
        return
    try:
        data = github_api("/commits?per_page=1&sha=main")
        sha = data[0]["sha"]
        if sha == get_version():
            return
        print(f"[DEPLOY] nuevo commit {sha[:8]}", flush=True)
        url = f"https://github.com/{REPO}/archive/refs/heads/main.tar.gz"
        req = urllib.request.Request(url, headers={"User-Agent": "panel-autodeploy"})
        if TOKEN:
            req.add_header("Authorization", f"Bearer {TOKEN}")
        tar = os.path.join(BASE, "update.tar.gz")
        with urllib.request.urlopen(req, timeout=30) as r, open(tar, "wb") as f:
            f.write(r.read())
        subprocess.run(["mkdir", "-p", DEPLOY_DIR], check=True)
        subprocess.run(["tar", "xzf", tar, "-C", DEPLOY_DIR, "--strip-components=1"], check=True)
        src = os.path.join(DEPLOY_DIR, "panel_celular")
        for name in ("index.html", "server.py", "wol.py"):
            s = os.path.join(src, name)
            if os.path.exists(s):
                # No sobrescribir server.py en caliente; se copia y se reinicia
                with open(s, "rb") as fs, open(os.path.join(BASE, name + ".new"), "wb") as fd:
                    fd.write(fs.read())
        write_json(VERSION_FILE, {"sha": sha, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        for name in ("index.html", "wol.py"):
            s = os.path.join(BASE, name + ".new")
            if os.path.exists(s):
                os.replace(s, os.path.join(BASE, name))
        if os.path.exists(os.path.join(BASE, "server.py.new")):
            os.replace(os.path.join(BASE, "server.py.new"), os.path.join(BASE, "server.py"))
            print("[DEPLOY] reiniciando...", flush=True)
            os.execv(sys.executable, [sys.executable, os.path.join(BASE, "server.py")])
    except Exception as e:
        print(f"[DEPLOY] error: {e}", flush=True)


def deploy_loop():
    check_and_deploy()  # al arrancar
    while True:
        time.sleep(POLL_INTERVAL)
        check_and_deploy()


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, bytes):
            b = body
        elif isinstance(body, str):
            b = body.encode()
        else:
            b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, str(e), "text/plain")
        elif self.path == "/status":
            self._send(200, read_json(STATUS_FILE, {"estado": "sin datos"}))
        elif self.path == "/cmd":
            self._send(200, read_json(CMD_FILE, {}))
        elif self.path == "/ack":
            self._send(200, read_json(ACK_FILE, {}))
        elif self.path == "/estado":
            self._send(200, read_json(ESTADO_FILE, {}))
        elif self.path == "/version":
            self._send(200, {"sha": get_version(), "repo": REPO or "local"})
        else:
            self._send(404, {"error": "no encontrado"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.path == "/status":
            try:
                data = json.loads(raw)
                data["recibido_en_celular"] = True
                data["recibido_ts"] = time.time()
                write_json(STATUS_FILE, data)
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/cmd":
            try:
                data = json.loads(raw)
                cmd = data.get("comando")
                if cmd not in ("shutdown", "wake", "cancelar", "t3-open", "t3-close"):
                    self._send(400, {"error": "comando invalido"})
                    return
                if cmd == "cancelar":
                    write_json(CMD_FILE, {})
                else:
                    if cmd == "wake":
                        subprocess.run([sys.executable, WOL_SCRIPT, PC_MAC], check=False)
                    write_json(CMD_FILE, {"comando": cmd, "ts": data.get("ts")})
                self._send(200, {"ok": True, "comando": cmd})
            except Exception as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/estado_cmd":
            try:
                write_json(ESTADO_FILE, json.loads(raw))
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/cmd/ack":
            try:
                write_json(ACK_FILE, json.loads(raw))
                write_json(CMD_FILE, {})
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(400, {"error": str(e)})
        else:
            self._send(404, {"error": "no encontrado"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    subprocess.run(["termux-wake-lock"], check=False)
    if REPO:
        threading.Thread(target=deploy_loop, daemon=True).start()
    p = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", p), Handler).serve_forever()
