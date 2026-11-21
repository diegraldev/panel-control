#!/usr/bin/env python3
"""PC agent: reports status to the phone and runs commands.

Commands: shutdown, wake, cancelar, t3-open, t3-close.
"""
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request

PHONE = os.environ.get("PANEL_URL", "http://100.113.245.28:8080")
INTERVAL = int(os.environ.get("PANEL_INTERVAL", "5"))
MAC = os.environ.get("PC_MAC", "d8:bb:c1:22:1a:d1")
WOL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wol.py")
T3_APPIMAGE = os.environ.get(
    "T3_APPIMAGE",
    "/home/diegral/Downloads/T3-Code-Nightly-x86_64_4208950c0787b1202aed9c2bb25c78db.AppImage",
)
_shutdown_timer = None


# ---------- metrics ----------
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


def get_cpu():
    try:
        with open("/proc/stat") as f:
            vals = list(map(int, f.readline().split()[1:]))
        return round(100 * (1 - (vals[3] + vals[4]) / sum(vals)))
    except Exception:
        return None


def get_cpu_temp():
    try:
        out = subprocess.check_output(["sensors", "-u"], stderr=subprocess.DEVNULL, text=True, timeout=3)
        temps, cur = {}, None
        for line in out.splitlines():
            if line and not line.startswith(" "):
                cur = line.strip()
            elif "temp" in line and "+" in line:
                temps[cur] = float(line.split("+")[1].split()[0])
        if temps:
            return {"max": max(temps.values()), "source": "sensors"}
    except Exception:
        pass
    try:
        import glob
        vals = [int(open(f).read().strip()) / 1000 for f in glob.glob("/sys/class/thermal/thermal_zone*/temp")]
        if vals:
            return {"max": max(vals), "source": "thermal_zone"}
    except Exception:
        pass
    return {"max": None, "source": "unavailable"}


def get_gpus():
    """GPU usage and temperature. NVIDIA via nvidia-smi, Intel via /sys/drm."""
    gpus = []
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({
                    "name": parts[0],
                    "usage": float(parts[1]),
                    "temp": float(parts[2]),
                    "type": "nvidia",
                })
    except Exception:
        pass
    # Intel integrated GPU: usage via intel_gpu_top (RCS column), no temperature
    try:
        out = subprocess.check_output(["lspci"], stderr=subprocess.DEVNULL, text=True, timeout=3)
        intel = [l for l in out.splitlines() if "VGA compatible controller" in l and "Intel" in l]
        usage = None
        try:
            top = subprocess.check_output(
                ["timeout", "2", "intel_gpu_top", "-l", "1", "-s", "1"],
                stderr=subprocess.DEVNULL, text=True, timeout=4,
            )
            for line in top.splitlines():
                parts = line.split()
                # Data row starts with numbers; RCS % is column 9 (index 9)
                if parts and parts[0].replace(".", "", 1).isdigit() and len(parts) >= 13:
                    try:
                        usage = float(parts[9])
                        break
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass
        for l in intel:
            name = l.split(": ", 1)[1].split(" (rev")[0] if ": " in l else l.strip()
            gpus.append({"name": name, "usage": usage, "temp": None, "type": "intel"})
    except Exception:
        pass
    return gpus


def get_ip():
    try:
        return subprocess.check_output(["hostname", "-I"], text=True, timeout=2).split()[0]
    except Exception:
        return "?"


def t3_running():
    try:
        out = subprocess.check_output(["pgrep", "-f", "t3code"], stderr=subprocess.DEVNULL, text=True, timeout=2)
        return bool(out.strip())
    except Exception:
        return False


# ---------- network ----------
def post_json(path, data):
    req = urllib.request.Request(
        PHONE + path, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode()


def get_json(path):
    with urllib.request.urlopen(PHONE + path, timeout=5) as r:
        return json.loads(r.read().decode())


# ---------- commands ----------
def cancel_shutdown():
    global _shutdown_timer
    if _shutdown_timer and _shutdown_timer.is_alive():
        _shutdown_timer.cancel()
        _shutdown_timer = None
        return True
    return False


def run_command(cmd):
    global _shutdown_timer
    print(f"[CMD] {cmd}", flush=True)
    if cmd == "cancelar":
        return "shutdown canceled" if cancel_shutdown() else "nothing pending"
    if cmd == "shutdown":
        delay = int(os.environ.get("PANEL_SHUTDOWN_DELAY", "8"))
        post_json("/estado_cmd", {"shutdown_in": delay, "cancelable": True})
        def fire():
            post_json("/estado_cmd", {"shutdown_in": 0, "cancelable": False})
            subprocess.run(["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "poweroff"], check=False)
        _shutdown_timer = threading.Timer(delay, fire)
        _shutdown_timer.daemon = True
        _shutdown_timer.start()
        return f"shutting down in {delay}s"
    if cmd == "wake":
        subprocess.run([sys.executable, WOL_SCRIPT, MAC], check=False)
        return "magic packet sent"
    if cmd == "t3-open":
        if t3_running():
            return "T3 Code already open"
        # systemd service has no graphical env; take it from the active user session
        env = os.environ.copy()
        env.update({
            "DISPLAY": env.get("DISPLAY") or ":0",
            "WAYLAND_DISPLAY": env.get("WAYLAND_DISPLAY") or "wayland-1",
            "XDG_RUNTIME_DIR": env.get("XDG_RUNTIME_DIR") or "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": env.get("DBUS_SESSION_BUS_ADDRESS") or "unix:path=/run/user/1000/bus",
        })
        subprocess.Popen([T3_APPIMAGE], env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return "opening T3 Code"
    if cmd == "t3-close":
        subprocess.run(["pkill", "-f", "t3code"], check=False)
        return "closing T3 Code"
    return "unknown"


def main():
    global PHONE
    PHONE = PHONE.rstrip("/")
    print(f"Agent -> {PHONE} every {INTERVAL}s", flush=True)
    while True:
        status = {
            "hostname": platform.node(),
            "ip": get_ip(),
            "ram": get_ram(),
            "cpu_percent": get_cpu(),
            "cpu_temp": get_cpu_temp(),
            "gpus": get_gpus(),
            "t3_open": t3_running(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            post_json("/status", status)
        except Exception as e:
            print(f"[WARN] status: {e}", flush=True)
        try:
            cmd = get_json("/cmd").get("comando")
            if cmd:
                result = run_command(cmd)
                post_json("/cmd/ack", {"comando": cmd, "resultado": result})
        except Exception as e:
            print(f"[WARN] cmd: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
