#!/usr/bin/env python3
"""Panel server (phone / Termux) + auto-deploy from GitHub.

Endpoints (unchanged contract):
    GET  /              -> index.html
    GET  /status        -> last PC status
    POST /status        -> store PC status
    GET  /cmd           -> pending command (add ?wait=N to long-poll)
    POST /cmd           -> queue a command
    POST /cmd/ack       -> command result (clears the queue)
    GET  /ack           -> last command result
    GET  /estado        -> free-form state blob
    POST /estado_cmd    -> store free-form state blob
    GET  /version       -> deployed commit

Design notes (all behaviour-compatible with the previous version):
  * HTTP/1.1 with keep-alive: one TCP connection is reused by the agent and
    the browser instead of one handshake per request.
  * JSON state is written atomically (tmp + rename) and served from memory,
    so a concurrent read can never see a half-written file.
  * GET /cmd?wait=N blocks until a command arrives (long-poll), which removes
    the polling latency for the PC agent. Plain GET /cmd still answers at once.
  * Requests coming from another origin (a random web page in the browser) are
    rejected, and foreign Host headers are refused.
  * Auto-deploy polls the public Atom feed with If-None-Match: no GitHub API
    rate limit, zero bytes when nothing changed, and the service only restarts
    when server.py itself changed.
"""
import gzip
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
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
POLL_INTERVAL = int(os.environ.get("DEPLOY_POLL", "10"))
DEPLOY_DIR = os.path.join(BASE, ".deploy")

# Security
PANEL_TOKEN = os.environ.get("PANEL_TOKEN", "")
WAKE_TOKEN = os.environ.get("WAKE_TOKEN", "")
WAKE_URL = os.environ.get("WAKE_URL", "http://100.69.149.63:8091/wake")
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}

CMD_VALUES = ("shutdown", "wake", "cancelar", "t3-open", "t3-close")

# ---------------------------------------------------------------- state cache
_state_lock = threading.Lock()
_status_cache = {"data": None, "written": 0.0}
_cmd_cond = threading.Condition()
_cmd_cache = {}
_ack_cache = {}
_estado_cache = {}
_last_status_write = 0.0


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(path, data):
    """Atomic write: a reader never sees a partial document."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


_battery_cache = {"level": None, "charging": False}


def get_battery():
    """Devuelve la batería cacheada (se actualiza en background)."""
    return dict(_battery_cache)


def _battery_loop():
    """Actualiza la batería fuera del handler HTTP para no crashear la conexión."""
    import time as _time
    while True:
        try:
            out = subprocess.check_output(
                ["termux-battery-status"], stderr=subprocess.DEVNULL, text=True, timeout=10)
            d = json.loads(out)
            _battery_cache["level"] = d.get("percentage")
            _battery_cache["charging"] = d.get("status") == "CHARGING"
        except Exception:
            pass
        _time.sleep(30)


def get_version():
    return read_json(VERSION_FILE, {}).get("sha", "local")


def load_state():
    global _status_cache, _cmd_cache, _ack_cache, _estado_cache
    _status_cache["data"] = read_json(STATUS_FILE, None)
    _cmd_cache = read_json(CMD_FILE, {})
    _ack_cache = read_json(ACK_FILE, {})
    _estado_cache = read_json(ESTADO_FILE, {})


def store_status(data):
    """Keep the status in memory; touch the flash at most every 5 s."""
    global _last_status_write
    data = dict(data)
    data["recibido_en_celular"] = True
    data["recibido_ts"] = time.time()
    with _state_lock:
        _status_cache["data"] = data
        now = time.time()
        if now - _last_status_write >= 5.0:
            _last_status_write = now
            write_json(STATUS_FILE, data)


def current_status():
    with _state_lock:
        d = _status_cache["data"]
    if d is not None:
        return d
    return {"status": "no data"}


def queue_command(cmd, ts):
    global _cmd_cache
    with _cmd_cond:
        _cmd_cache = {"comando": cmd, "ts": ts}
        write_json(CMD_FILE, _cmd_cache)
        _cmd_cond.notify_all()
    return _cmd_cache


def clear_command():
    global _cmd_cache
    with _cmd_cond:
        _cmd_cache = {}
        write_json(CMD_FILE, {})
        _cmd_cond.notify_all()


def wait_command(timeout):
    """Long-poll: return as soon as a command exists (or {} on timeout)."""
    deadline = time.monotonic() + timeout
    with _cmd_cond:
        while not _cmd_cache:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            _cmd_cond.wait(left)
        return dict(_cmd_cache)


# ------------------------------------------------------------- wake helpers
def _send_magic_packet(mac, broadcast="255.255.255.255", port=9):
    hw = bytes.fromhex(mac.replace(":", "").replace("-", "").strip())
    packet = b"\xff" * 6 + hw * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(packet, (broadcast, port))


def wake_pc_async():
    """Fire the magic packet and the listener hint in the background.

    The HTTP response must not wait on the PC: when it is off, connecting to
    the listener used to stall the request for the full socket timeout.
    """

    def _run():
        try:
            _send_magic_packet(PC_MAC)
        except Exception as e:
            print(f"[WAKE] magic packet: {e}", flush=True)
        try:
            headers = {"X-Wake-Token": WAKE_TOKEN} if WAKE_TOKEN else {}
            req = urllib.request.Request(WAKE_URL, data=b"", method="POST", headers=headers)
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass  # PC off / listener down: the shutdown flag is the fallback

    threading.Thread(target=_run, daemon=True).start()


# ------------------------------------------------------------- auto deploy
_ATOM_RE = re.compile(rb"<id>tag:github\.com,2008:Grit::Commit/([0-9a-f]{40})</id>")


def _http_get(url, headers=None, timeout=15):
    h = {"User-Agent": "panel-autodeploy"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def remote_sha():
    """Latest main commit and its feed ETag.

    Atom feed first: no GitHub API rate limit, and a 304 costs zero bytes.
    """
    meta = read_json(VERSION_FILE, {})
    etag = meta.get("etag")
    url = f"https://github.com/{REPO}/commits/main.atom"
    try:
        with _http_get(url, {"If-None-Match": etag} if etag else None, timeout=15) as r:
            body = r.read()
            new_etag = r.headers.get("ETag")
        m = _ATOM_RE.search(body)
        if not m:
            raise ValueError("sha not found in feed")
        return m.group(1).decode(), new_etag
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return get_version(), etag
        raise
    except Exception:
        pass
    # Fallback: REST API (rate limited, but correct)
    headers = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    with _http_get(f"https://api.github.com/repos/{REPO}/commits?per_page=1&sha=main",
                   headers, timeout=10) as r:
        return json.loads(r.read().decode())[0]["sha"], etag


def _persist_etag(etag):
    """Remember the feed ETag so the next poll is a 304 (zero bytes)."""
    if not etag:
        return
    meta = read_json(VERSION_FILE, {})
    if meta.get("sha") and meta.get("etag") != etag:
        meta["etag"] = etag
        write_json(VERSION_FILE, meta)


def check_and_deploy():
    if not REPO:
        return
    try:
        sha, etag = remote_sha()
        if sha == get_version():
            _persist_etag(etag)
            return
        print(f"[DEPLOY] nuevo commit {sha[:8]}", flush=True)
        url = f"https://github.com/{REPO}/archive/refs/heads/main.tar.gz"
        tar = os.path.join(BASE, "update.tar.gz")
        with _http_get(url, {"Authorization": f"Bearer {TOKEN}"} if TOKEN else None, timeout=60) as r:
            data = r.read()
        with open(tar, "wb") as f:
            f.write(data)
        os.makedirs(DEPLOY_DIR, exist_ok=True)
        subprocess.run(["tar", "xzf", tar, "-C", DEPLOY_DIR, "--strip-components=1"],
                       check=True, timeout=60)
        try:
            os.unlink(tar)
        except OSError:
            pass

        src = os.path.join(DEPLOY_DIR, "panel_celular")
        changed = []
        for name in ("index.html", "server.py", "wol.py"):
            s = os.path.join(src, name)
            if not os.path.exists(s):
                continue
            dst = os.path.join(BASE, name)
            try:
                with open(s, "rb") as f:
                    new = f.read()
                with open(dst, "rb") as f:
                    old = f.read()
                if new == old:
                    continue
            except OSError:
                with open(s, "rb") as f:
                    new = f.read()
            with open(dst + ".new", "wb") as f:
                f.write(new)
            changed.append(name)

        for name in ("index.html", "wol.py"):
            if name in changed:
                os.replace(os.path.join(BASE, name + ".new"), os.path.join(BASE, name))
        write_json(VERSION_FILE, {"sha": sha, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "etag": etag})
        if "server.py" in changed:
            os.replace(os.path.join(BASE, "server.py.new"), os.path.join(BASE, "server.py"))
            print("[DEPLOY] reiniciando...", flush=True)
            _flush_state()
            os.execv(sys.executable, [sys.executable, os.path.join(BASE, "server.py")])
        elif changed:
            print(f"[DEPLOY] actualizado sin reinicio: {', '.join(changed)}", flush=True)
    except Exception as e:
        print(f"[DEPLOY] error: {e}", flush=True)


def _flush_state():
    try:
        with _state_lock:
            if _status_cache["data"] is not None:
                write_json(STATUS_FILE, _status_cache["data"])
    except Exception:
        pass


def deploy_loop():
    check_and_deploy()  # al arrancar
    while True:
        time.sleep(POLL_INTERVAL)
        check_and_deploy()


# ------------------------------------------------------------------- assets
class Assets:
    """index.html in memory, optionally gzipped, with an ETag."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.stamp = None
        self.raw = b""
        self.gz = b""
        self.etag = ""
        self.reload()

    def reload(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size)
            if stamp == self.stamp:
                return
            with open(self.path, "rb") as f:
                raw = f.read()
            with self.lock:
                self.raw = raw
                self.gz = gzip.compress(raw, 6)
                self.etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
                self.stamp = stamp
        except OSError:
            pass


ASSETS = None


# -------------------------------------------------------------------- HTTP
def _is_tailnet(ip):
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        a, b = int(parts[0]), int(parts[1])
        return (a == 100 and 64 <= b <= 127) or a == 127
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "panel/2"
    timeout = 20  # drop stalled sockets instead of leaking a thread each

    # ---------- helpers
    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, bytes):
            b = body
        elif isinstance(body, str):
            b = body.encode()
        else:
            b = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def _host_ok(self):
        """Refuse DNS-rebinding: the Host must be a name/IP we answer to."""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True  # HTTP/1.0 clients without Host
        name = host
        if name.startswith("["):
            name = name[1:].split("]", 1)[0]
        elif ":" in name:
            name = name.rsplit(":", 1)[0]
        if name in ALLOWED_HOSTS or name in ("localhost", "127.0.0.1"):
            return True
        if _is_tailnet(name):
            return True
        try:
            ipaddress.IPv4Address(name)
            return True  # any IPv4 literal is fine (rebinding needs a name)
        except ValueError:
            pass
        # a bare hostname: accept only if it resolves to a tailnet/loopback addr
        try:
            return any(_is_tailnet(ip) for ip in
                       {ai[4][0] for ai in socket.getaddrinfo(name, None)})
        except Exception:
            return False

    def _origin_ok(self):
        """Block cross-site requests from any web page the user has open."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            o = origin.split("://", 1)[1]
        except IndexError:
            return False
        o = o.split("/", 1)[0].lower()
        host = (self.headers.get("Host") or "").strip().lower()
        return o == host or o in ALLOWED_HOSTS

    def _token_ok(self):
        if not PANEL_TOKEN:
            return True
        if self.headers.get("X-Panel-Token") == PANEL_TOKEN:
            return True
        if f"token={PANEL_TOKEN}" in self.path:
            return True
        return False

    def _guard(self, mutating=False):
        if not self._host_ok():
            self._send(403, {"error": "host no permitido"})
            return False
        if not self._origin_ok():
            self._send(403, {"error": "origen no permitido"})
            return False
        if mutating and not self._token_ok():
            self._send(401, {"error": "token requerido"})
            return False
        return True

    # ---------- GET
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if not self._guard():
            return
        if path in ("/", "/index.html"):
            self._serve_index()
        elif path == "/status":
            self._send(200, current_status())
        elif path == "/cmd":
            wait = 0.0
            for part in query.split("&"):
                if part.startswith("wait="):
                    try:
                        wait = min(max(float(part[5:]), 0.0), 30.0)
                    except ValueError:
                        wait = 0.0
            self._send(200, wait_command(wait) if wait > 0 else dict(_cmd_cache))
        elif path == "/ack":
            self._send(200, _ack_cache)
        elif path == "/estado":
            self._send(200, _estado_cache)
        elif path == "/version":
            self._send(200, {"sha": get_version(), "repo": REPO or "local"})
        elif path == "/battery":
            self._send(200, get_battery())
        else:
            self._send(404, {"error": "no encontrado"})

    def _serve_index(self):
        ASSETS.reload()
        etag = ASSETS.etag
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = ASSETS.raw
        extra = {"ETag": etag} if etag else {}
        if "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = ASSETS.gz
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self._send(200, body, "text/html; charset=utf-8", extra)

    do_HEAD = do_GET

    # ---------- POST
    def do_POST(self):
        global _estado_cache, _ack_cache
        raw = self._body()
        path = self.path.partition("?")[0]
        if not self._guard(mutating=True):
            return
        try:
            data = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": str(e)})
            return

        if path == "/status":
            store_status(data)
            self._send(200, {"ok": True})
        elif path == "/cmd":
            cmd = data.get("comando")
            if cmd not in CMD_VALUES:
                self._send(400, {"error": "comando invalido"})
                return
            if cmd == "cancelar":
                clear_command()
            else:
                if cmd == "wake":
                    wake_pc_async()
                queue_command(cmd, data.get("ts"))
            self._send(200, {"ok": True, "comando": cmd})
        elif path == "/estado_cmd":
            with _state_lock:
                _estado_cache = data
                write_json(ESTADO_FILE, data)
            self._send(200, {"ok": True})
        elif path == "/cmd/ack":
            with _cmd_cond:
                _ack_cache = data
                write_json(ACK_FILE, data)
                _cmd_cache = {}
                write_json(CMD_FILE, {})
                _cmd_cond.notify_all()
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "no encontrado"})

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = 64

    def get_request(self):
        sock, addr = super().get_request()
        # TCP_NODELAY removes Nagle's ~40 ms delayed-ACK stall that otherwise
        # hits every second request on a keep-alive connection.
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return sock, addr


def main():
    global ASSETS
    try:
        subprocess.run(["termux-wake-lock"], check=False, timeout=5)
    except Exception:
        pass
    load_state()
    ASSETS = Assets(os.path.join(BASE, "index.html"))
    threading.Thread(target=_battery_loop, daemon=True).start()
    if REPO:
        threading.Thread(target=deploy_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    print(f"[PANEL] escuchando en 0.0.0.0:{port} (repo={REPO or 'local'})", flush=True)
    Server(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
