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
            # intel_gpu_top runs until killed; capture output then terminate
            proc = subprocess.Popen(
                ["intel_gpu_top", "-l", "1", "-s", "2"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            import time as _time
            _time.sleep(1.5)
            proc.terminate()
            try:
                top, _ = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                top, _ = proc.communicate()
            for line in (top or "").splitlines():
                parts = line.split()
                # Data row starts with numbers; RCS % is column 9 (index 9)
                if parts and parts[0].replace(".", "", 1).isdigit() and len(parts) >= 7:
                    try:
                        usage = float(parts[6])  # RCS % column
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
    # -x matches the process NAME exactly, not the full command line: pgrep -f
    # would match unrelated shells/scripts that merely contain "t3code".
    try:
        out = subprocess.check_output(["pgrep", "-x", "t3code"], stderr=subprocess.DEVNULL, text=True, timeout=2)
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
        # Apago inmediato via dbus/logind (igual que el boton del escritorio), sin sudo.
        subprocess.run([
            "/usr/bin/gdbus", "call", "--system",
            "--dest", "org.freedesktop.login1",
            "--object-path", "/org/freedesktop/login1",
            "--method", "org.freedesktop.login1.Manager.PowerOff",
            "false",
        ], check=False)
        return "shutting down"
    if cmd == "wake":
        subprocess.run([sys.executable, WOL_SCRIPT, MAC], check=False)
        return "magic packet sent"
    if cmd == "t3-open":
        if t3_running():
            return "T3 Code already open"
        # Clone the FULL environment of a graphical session process (niri/noctalia).
        # This is exactly what the .desktop shortcut gets when you click it.
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
        if env is None:
            env = os.environ.copy()
        # This service restricts capabilities to CAP_PERFMON (for intel_gpu_top),
        # so CAP_SYS_ADMIN is missing and AppImage's FUSE mount fails with
        # "Permission denied". Extract-and-run skips FUSE entirely.
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
        # gtk-launch opens the .desktop shortcut exactly like clicking it.
        subprocess.Popen(
            ["gtk-launch", "com.t3tools.T3Code"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "opening T3 Code (gtk-launch)"
    if cmd == "t3-close":
        # -x targets only real T3 Code processes; -f would kill anything whose
        # command line happens to contain "t3code" (other shells, editors...).
        subprocess.run(["pkill", "-x", "t3code"], check=False)
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
