#!/usr/bin/env python3
"""PC agent: reports status to the phone panel and runs its commands.

Commands: shutdown, wake, cancelar, t3-open, t3-close.

Same contract as before (same JSON fields, same commands, same files), but the
hot path no longer forks a process per sample:

  * CPU %   -> delta between two /proc/stat reads (real instantaneous value;
               the old single read returned the average since boot).
  * CPU temp-> /sys/class/hwmon/coretemp, then thermal zones. No `sensors`.
  * IP      -> ioctl on the interfaces, cached. No `hostname -I`.
  * T3 Code -> /proc/*/comm scan. No `pgrep`.
  * GPUs    -> long-lived sampler threads; the publish loop never blocks.
  * HTTP    -> one keep-alive connection + long-poll for commands, so a tap
               on the phone is executed in milliseconds instead of up to one
               full polling interval.
"""
import array
import fcntl
import json
import os
import platform
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

PHONE = os.environ.get("PANEL_URL", "http://100.113.245.28:8080")
INTERVAL = float(os.environ.get("PANEL_INTERVAL", "1"))
MAC = os.environ.get("PC_MAC", "d8:bb:c1:22:1a:d1")
WOL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wol.py")
T3_APPIMAGE = os.environ.get(
    "T3_APPIMAGE",
    "/home/diegral/Downloads/T3-Code-Nightly-x86_64_4208950c0787b1202aed9c2bb25c78db.AppImage",
)
PANEL_TOKEN = os.environ.get("PANEL_TOKEN", "")
GPU_PERIOD = float(os.environ.get("GPU_SAMPLE_PERIOD", "2"))
DEBUG = os.environ.get("PANEL_DEBUG", "") not in ("", "0")

_shutdown_timer = None
_stop = threading.Event()


def log(msg):
    if DEBUG:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------- metrics
class CpuSampler:
    """Instantaneous CPU utilisation from the delta of two /proc/stat reads."""

    def __init__(self):
        self.prev = None

    @staticmethod
    def _read():
        with open("/proc/stat") as f:
            return [int(x) for x in f.readline().split()[1:11]]

    def start(self):
        try:
            self.prev = self._read()
        except OSError:
            self.prev = None

    def sample(self):
        try:
            cur = self._read()
        except OSError:
            return None
        prev, self.prev = self.prev, cur
        if prev is None:
            # first call: same estimate the old code produced, then we get exact
            total = sum(cur)
            return round(100 * (1 - (cur[3] + cur[4]) / total)) if total else 0
        d = [c - p for c, p in zip(cur, prev)]
        total = sum(d)
        if total <= 0:
            return None
        return round(100 * (1 - (d[3] + d[4]) / total))


def get_ram():
    try:
        d = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                d[k] = int(v.strip().split()[0])
        total = d["MemTotal"]
        avail = d.get("MemAvailable", d["MemFree"])
        return {
            "total_mb": round(total / 1024),
            "used_mb": round((total - avail) / 1024),
            "free_mb": round(avail / 1024),
            "percent": round(100 * (total - avail) / total),
        }
    except Exception as e:
        return {"error": str(e)}


class TempSampler:
    """CPU temperature straight from sysfs (no `sensors` subprocess)."""

    def __init__(self):
        self.source = None
        self.paths = []
        self._discover()

    def _discover(self):
        # 1) coretemp: package + per-core temperatures
        try:
            for h in os.listdir("/sys/class/hwmon"):
                base = f"/sys/class/hwmon/{h}"
                try:
                    with open(f"{base}/name") as f:
                        if f.read().strip() != "coretemp":
                            continue
                except OSError:
                    continue
                self.paths = sorted(
                    os.path.join(base, n) for n in os.listdir(base)
                    if n.startswith("temp") and n.endswith("_input")
                )
                if self.paths:
                    self.source = "coretemp"
                    return
        except OSError:
            pass
        # 2) x86_pkg_temp thermal zone, then anything else available
        for zone_type, label in (("x86_pkg_temp", "x86_pkg_temp"),
                                 ("TCPU", "TCPU"), ("acpitz", "acpitz")):
            try:
                for z in os.listdir("/sys/class/thermal"):
                    if not z.startswith("thermal_zone"):
                        continue
                    base = f"/sys/class/thermal/{z}"
                    try:
                        with open(f"{base}/type") as f:
                            if f.read().strip() != zone_type:
                                continue
                    except OSError:
                        continue
                    self.paths = [f"{base}/temp"]
                    self.source = label
                    return
            except OSError:
                continue
        self.source = "unavailable"

    def sample(self):
        best = None
        for p in self.paths:
            try:
                with open(p) as f:
                    v = int(f.read().strip()) / 1000.0
            except (OSError, ValueError):
                continue
            if v <= 0 or v > 200:  # ignore obviously bogus readings
                continue
            best = v if best is None else max(best, v)
        return {"max": best, "source": self.source}


class GpuSampler:
    """GPU usage/temperature from long-lived samplers.

    A background thread keeps the newest sample in memory, so building the
    status payload is a dict lookup instead of a 1.5 s blocking call.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.nvidia = []       # [{name, usage, temp}]
        self.intel_usage = None
        self.intel_name = None
        self._threads = []
        self._stop = threading.Event()

    # ---- names
    def _find_intel_name(self):
        lspci = shutil.which("lspci")
        if lspci:
            try:
                out = subprocess.check_output([lspci], stderr=subprocess.DEVNULL,
                                              text=True, timeout=5)
                for line in out.splitlines():
                    if "VGA compatible controller" in line and "Intel" in line:
                        return (line.split(": ", 1)[1].split(" (rev")[0]
                                if ": " in line else line.strip())
            except Exception:
                pass
        return None

    def start(self):
        self.intel_name = self._find_intel_name()
        if shutil.which("nvidia-smi"):
            t = threading.Thread(target=self._nvidia_loop, daemon=True)
            t.start()
            self._threads.append(t)
        if shutil.which("intel_gpu_top"):
            t = threading.Thread(target=self._intel_loop, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    # ---- nvidia: one persistent nvidia-smi that streams a line per second
    def _nvidia_loop(self):
        while not self._stop.is_set():
            try:
                p = subprocess.Popen(
                    ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu",
                     "--format=csv,noheader,nounits", "-lms", str(int(GPU_PERIOD * 1000))],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                    bufsize=1,
                )
                for line in p.stdout:
                    parts = [x.strip() for x in line.split(",")]
                    if len(parts) < 3:
                        continue
                    try:
                        entry = {"name": parts[0], "usage": float(parts[1]),
                                 "temp": float(parts[2])}
                    except ValueError:
                        continue
                    with self.lock:
                        if self.nvidia and self.nvidia[0]["name"] == entry["name"]:
                            self.nvidia[0] = entry
                        else:
                            self.nvidia = [entry] + self.nvidia[1:]
                p.wait(timeout=5)
            except Exception as e:
                log(f"nvidia-smi sampler: {e}")
            if self._stop.wait(2.0):
                return

    # ---- intel: short samples in the background (never blocks the publisher)
    def _intel_loop(self):
        window = max(1.0, GPU_PERIOD)
        while not self._stop.is_set():
            proc = None
            try:
                proc = subprocess.Popen(
                    ["intel_gpu_top", "-l", "1", "-s", str(int(window * 1000))],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                # intel_gpu_top streams until killed: use a fixed sampling
                # window and then terminate, exactly like the old code did.
                time.sleep(min(0.6, window))
                proc.terminate()
                try:
                    out, _ = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
                usage = None
                for line in (out or "").splitlines():
                    parts = line.split()
                    if parts and parts[0].replace(".", "", 1).isdigit() and len(parts) >= 7:
                        try:
                            usage = float(parts[6])  # RCS %
                        except (ValueError, IndexError):
                            continue
                        break
                if usage is not None:
                    with self.lock:
                        self.intel_usage = usage
            except Exception as e:
                log(f"intel_gpu_top: {e}")
            finally:
                if proc and proc.poll() is None:
                    proc.kill()
            if self._stop.wait(max(0.1, GPU_PERIOD - 0.6)):
                return

    def gpus(self):
        with self.lock:
            nvidia = [dict(g, type="nvidia") for g in self.nvidia]
            usage = self.intel_usage
            name = self.intel_name
        gpus = nvidia
        if name or usage is not None:
            gpus = gpus + [{"name": name or "Intel GPU", "usage": usage,
                            "temp": None, "type": "intel"}]
        return gpus


class NetSampler:
    """Local IP without forking `hostname -I`, cached for 30 s."""

    def __init__(self):
        self.value = "?"
        self.stamp = 0.0

    @staticmethod
    def _ifaddrs():
        """Enumerate IPv4 addresses with ioctl(SIOCGIFADDR) - no subprocess."""
        out = []
        try:
            names = [n for _, n in socket.if_nameindex()]
        except OSError:
            return out
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for name in names:
                try:
                    buf = array.array("B", name[:15].encode() + b"\0" * (32 - min(len(name), 15)))
                    fcntl.ioctl(s.fileno(), 0x8915, buf, True)  # SIOCGIFADDR
                    ip = socket.inet_ntoa(buf[20:24])
                    if ip != "0.0.0.0":
                        out.append(ip)
                except OSError:
                    continue
        finally:
            s.close()
        return out

    def sample(self):
        now = time.time()
        if now - self.stamp < 30 and self.value != "?":
            return self.value
        addrs = [a for a in self._ifaddrs() if not a.startswith("127.")]
        tailnet = [a for a in addrs if a.startswith("100.")]
        self.value = (tailnet or addrs or ["?"])[0]
        self.stamp = now
        return self.value


def t3_running():
    """T3 Code is running? Scan /proc/*/comm (no `pgrep` fork)."""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as f:
                    if f.read().strip() == "t3code":
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


# ------------------------------------------------------------------- network
class Client:
    """One keep-alive connection to the panel, reconnected on failure."""

    def __init__(self, base):
        u = urllib.parse.urlsplit(base.rstrip("/"))
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 80
        self.prefix = u.path or ""
        self.conn = None
        self.lock = threading.Lock()

    def _connect(self):
        import http.client
        conn = http.client.HTTPConnection(self.host, self.port, timeout=4)
        conn.connect()
        return conn

    def request(self, method, path, data=None, timeout=4):
        import http.client
        body = None
        headers = {}
        if data is not None:
            body = json.dumps(data, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if PANEL_TOKEN:
            headers["X-Panel-Token"] = PANEL_TOKEN
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.conn is None:
                        self.conn = self._connect()
                    self.conn.timeout = timeout
                    self.conn.sock.settimeout(timeout)
                    self.conn.request(method, self.prefix + path, body=body, headers=headers)
                    r = self.conn.getresponse()
                    payload = r.read()
                    if r.status >= 400:
                        raise http.client.HTTPException(f"HTTP {r.status}")
                    return json.loads(payload) if payload else {}
                except Exception:
                    try:
                        if self.conn:
                            self.conn.close()
                    except Exception:
                        pass
                    self.conn = None
                    if attempt:
                        raise
        return {}

    def close(self):
        with self.lock:
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass
            self.conn = None


# ------------------------------------------------------------------ commands
def cancel_shutdown():
    global _shutdown_timer
    if _shutdown_timer and _shutdown_timer.is_alive():
        _shutdown_timer.cancel()
        _shutdown_timer = None
        return True
    return False


def _magic_packet(mac):
    hw = bytes.fromhex(mac.replace(":", "").replace("-", "").strip())
    return b"\xff" * 6 + hw * 16


def send_magic_packet(mac=MAC):
    packet = _magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(packet, ("255.255.255.255", 9))


def _session_env():
    """Clone the full environment of a graphical session process."""
    env = None
    try:
        pids = subprocess.check_output(
            ["pgrep", "-u", str(os.getuid()), "-f", "noctalia|niri|gnome-shell"],
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).split()
        for pid in pids:
            try:
                with open(f"/proc/{pid}/environ", "rb") as f:
                    raw = f.read()
                env = dict(
                    p.decode("utf-8", "replace").split("=", 1)
                    for p in raw.split(b"\0") if b"=" in p
                )
                if "WAYLAND_DISPLAY" in env or "DISPLAY" in env:
                    break
                env = None
            except (OSError, ValueError):
                continue
    except Exception:
        pass
    return env if env is not None else os.environ.copy()


def open_t3():
    if t3_running():
        return "T3 Code already open"
    env = _session_env()
    # This service restricts capabilities to CAP_PERFMON (for intel_gpu_top),
    # so CAP_SYS_ADMIN is missing and AppImage's FUSE mount fails with
    # "Permission denied". Extract-and-run skips FUSE entirely.
    env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    # Launch in a transient USER scope. T3 Code then runs with the desktop
    # session's SELinux context (unconfined_u) and full user privileges, so
    # sudo/pkexec work inside the app. A oneshot --user service cannot be
    # used: systemd kills its cgroup as soon as gtk-launch returns.
    launch = [
        "/usr/bin/systemd-run", "--user", "--scope", "--collect",
        "--unit=t3code-panel",
    ]
    for key in (
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "XDG_DATA_DIRS",
        "APPIMAGE_EXTRACT_AND_RUN",
    ):
        if env.get(key):
            launch.append(f"--setenv={key}={env[key]}")
    launch += ["/usr/bin/gtk-launch", "com.t3tools.T3Code"]
    subprocess.Popen(
        launch, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return "opening T3 Code (user scope)"


def close_t3():
    """SIGTERM every process whose comm is exactly `t3code` (pkill -x)."""
    killed = 0
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as f:
                    if f.read().strip() != "t3code":
                        continue
                os.kill(int(pid), signal.SIGTERM)
                killed += 1
            except OSError:
                continue
    except OSError:
        pass
    return f"closing T3 Code ({killed})"


def sleep_mode(mode):
    """Baja/restaura brillo, volumen y RGB via systemd-run --user."""
    env = _session_env()
    launch = [
        "/usr/bin/systemd-run", "--user", "--scope", "--collect",
        "--unit=panel-sleep",
    ]
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE",
                "XDG_CURRENT_DESKTOP", "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_DATA_DIRS"):
        if env.get(key):
            launch.append(f"--setenv={key}={env[key]}")
    launch += ["/usr/local/lib/panel-pc/sleep-mode.sh", mode]
    subprocess.Popen(launch, env=env, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return f"sleep {mode}"


def run_command(cmd):
    print(f"[CMD] {cmd}", flush=True)
    if cmd == "cancelar":
        return "shutdown canceled" if cancel_shutdown() else "nothing pending"
    if cmd == "shutdown":
        # Escribir el flag ANTES de apagar: así existe cuando arranque
        # wol-autologin.service, sin depender de que el celular avise tarde.
        subprocess.run(["/usr/bin/sudo", "-n", "/usr/local/lib/panel-pc/wol-flag-set.sh"],
                       check=False)
        # Apago inmediato via dbus/logind (igual que el boton del escritorio).
        r = subprocess.run([
            "/usr/bin/gdbus", "call", "--system",
            "--dest", "org.freedesktop.login1",
            "--object-path", "/org/freedesktop/login1",
            "--method", "org.freedesktop.login1.Manager.PowerOff",
            "false",
        ], check=False)
        if r.returncode != 0:
            print(f"[WARN] poweroff fallo (rc={r.returncode})", flush=True)
        return "shutting down"
    if cmd == "wake":
        send_magic_packet()
        return "magic packet sent"
    if cmd == "t3-open":
        return open_t3()
    if cmd == "t3-close":
        return close_t3()
    if cmd in ("sleep-on", "sleep-off"):
        return sleep_mode("on" if cmd == "sleep-on" else "off")
    return "unknown"


# --------------------------------------------------------------------- loops
class Agent:
    def __init__(self):
        # Two connections: a long-poll can wait 10 s, and it must never block
        # the status publisher.
        self.client = Client(PHONE)
        self.cmd_client = Client(PHONE)
        self.cpu = CpuSampler()
        self.temp = TempSampler()
        self.gpus = GpuSampler()
        self.net = NetSampler()
        self.long_poll = True
        self.last_key = None

    def status(self):
        return {
            "hostname": platform.node(),
            "ip": self.net.sample(),
            "ram": get_ram(),
            "cpu_percent": self.cpu.sample(),
            "cpu_temp": self.temp.sample(),
            "gpus": self.gpus.gpus(),
            "t3_open": t3_running(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def publish_loop(self):
        while not _stop.is_set():
            t0 = time.monotonic()
            try:
                self.client.request("POST", "/status", self.status(), timeout=3)
            except Exception as e:
                log(f"status: {e}")
            _stop.wait(max(0.0, INTERVAL - (time.monotonic() - t0)))

    def command_loop(self):
        while not _stop.is_set():
            try:
                if self.long_poll:
                    cmd = self.cmd_client.request("GET", "/cmd?wait=10", timeout=15)
                else:
                    cmd = self.cmd_client.request("GET", "/cmd", timeout=5)
            except Exception as e:
                log(f"cmd: {e}")
                self.long_poll = False
                _stop.wait(0.2)
                continue
            name = (cmd or {}).get("comando")
            if not name:
                if not self.long_poll:
                    _stop.wait(max(0.5, INTERVAL))
                continue
            key = (name, cmd.get("ts"))
            if key == self.last_key:
                # already executed and only the ack was lost: never run twice
                log(f"skip duplicate {key}")
                result = "already executed"
            else:
                self.last_key = key
                try:
                    result = run_command(name)
                except Exception as e:
                    result = f"error: {e}"
                log(f"cmd {name}: {result}")
            try:
                self.cmd_client.request("POST", "/cmd/ack",
                                        {"comando": name, "resultado": result}, timeout=3)
            except Exception as e:
                log(f"ack: {e}")

    def run(self):
        print(f"Agent -> {PHONE} every {INTERVAL:g}s", flush=True)
        self.cpu.start()
        self.gpus.start()
        self.status()  # prime caches before the first publish
        pub = threading.Thread(target=self.publish_loop, daemon=True)
        cmd = threading.Thread(target=self.command_loop, daemon=True)
        pub.start()
        cmd.start()
        try:
            while pub.is_alive() and cmd.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        _stop.set()
        self.gpus.stop()
        self.client.close()
        self.cmd_client.close()


def main():
    agent = Agent()
    try:
        agent.run()
    finally:
        _stop.set()


if __name__ == "__main__":
    main()
