#!/usr/bin/env python3
"""Agente de la PC: reporta estado al celular y ejecuta comandos.

Comandos soportados: shutdown, wake, cancelar, t3-open, t3-close.
"""
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

CELULAR = os.environ.get("PANEL_URL", "http://100.113.245.28:8080")
INTERVALO = int(os.environ.get("PANEL_INTERVAL", "5"))
MAC = os.environ.get("PC_MAC", "d8:bb:c1:22:1a:d1")
WOL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wol.py")
T3_APPIMAGE = os.environ.get(
    "T3_APPIMAGE",
    "/home/diegral/Downloads/T3-Code-Nightly-x86_64_4208950c0787b1202aed9c2bb25c78db.AppImage",
)
_apagado_timer = None


# ---------- helpers ----------
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
            "usado_mb": round((total - avail) / 1024),
            "libre_mb": round(avail / 1024),
            "porcentaje": round(100 * (total - avail) / total),
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


def get_temp():
    try:
        out = subprocess.check_output(["sensors", "-u"], stderr=subprocess.DEVNULL, text=True, timeout=3)
        temps, cur = {}, None
        for line in out.splitlines():
            if line and not line.startswith(" "):
                cur = line.strip()
            elif "temp" in line and "+" in line:
                temps[cur] = float(line.split("+")[1].split()[0])
        if temps:
            return {"max": max(temps.values()), "fuente": "sensors"}
    except Exception:
        pass
    try:
        import glob
        vals = [int(open(f).read().strip()) / 1000 for f in glob.glob("/sys/class/thermal/thermal_zone*/temp")]
        if vals:
            return {"max": max(vals), "fuente": "thermal_zone"}
    except Exception:
        pass
    return {"max": None, "fuente": "no disponible"}


def get_gpus():
    """Temperatura de todas las GPUs NVIDIA + deteccion de GPUs Intel."""
    gpus = []
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,temperature.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({"nombre": parts[1], "temp": float(parts[2]), "tipo": "nvidia"})
    except Exception:
        pass
    try:
        out = subprocess.check_output(["lspci"], stderr=subprocess.DEVNULL, text=True, timeout=3)
        intel = [l for l in out.splitlines() if "VGA compatible controller" in l and "Intel" in l]
        for l in intel:
            nombre = l.split(": ", 1)[1].split(" (rev")[0] if ": " in l else l.strip()
            gpus.append({"nombre": nombre, "temp": None, "tipo": "intel"})
    except Exception:
        pass
    return gpus


def get_ip():
    try:
        return subprocess.check_output(["hostname", "-I"], text=True, timeout=2).split()[0]
    except Exception:
        return "?"


def t3_estado():
    try:
        out = subprocess.check_output(["pgrep", "-f", "t3code"], stderr=subprocess.DEVNULL, text=True, timeout=2)
        return bool(out.strip())
    except Exception:
        return False


# ---------- red ----------
def post_json(path, data):
    req = urllib.request.Request(
        CELULAR + path, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode()


def get_json(path):
    with urllib.request.urlopen(CELULAR + path, timeout=5) as r:
        return json.loads(r.read().decode())


# ---------- comandos ----------
def cancelar_apagado():
    global _apagado_timer
    if _apagado_timer and _apagado_timer.is_alive():
        _apagado_timer.cancel()
        _apagado_timer = None
        return True
    return False


def ejecutar_comando(cmd):
    global _apagado_timer
    print(f"[CMD] {cmd}", flush=True)
    if cmd == "cancelar":
        return "apagado cancelado" if cancelar_apagado() else "nada pendiente"
    if cmd == "shutdown":
        delay = int(os.environ.get("PANEL_SHUTDOWN_DELAY", "8"))
        post_json("/estado_cmd", {"apagado_en": delay, "cancelable": True})
        def fire():
            post_json("/estado_cmd", {"apagado_en": 0, "cancelable": False})
            subprocess.run(["/usr/bin/systemctl", "poweroff"], check=False)
        _apagado_timer = threading.Timer(delay, fire)
        _apagado_timer.daemon = True
        _apagado_timer.start()
        return f"apagando en {delay}s"
    if cmd == "wake":
        subprocess.run([sys.executable, WOL_SCRIPT, MAC], check=False)
        return "magic packet enviado"
    if cmd == "t3-open":
        if t3_estado():
            return "T3 Code ya estaba abierto"
        subprocess.Popen([T3_APPIMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "abriendo T3 Code"
    if cmd == "t3-close":
        subprocess.run(["pkill", "-f", "t3code"], check=False)
        return "cerrando T3 Code"
    return "desconocido"


def main():
    # Eliminar cualquier barra final para concatenar rutas
    global CELULAR
    CELULAR = CELULAR.rstrip("/")
    print(f"Agente -> {CELULAR} cada {INTERVALO}s", flush=True)
    while True:
        status = {
            "hostname": platform.node(),
            "ip": get_ip(),
            "ram": get_ram(),
            "cpu_pct": get_cpu(),
            "temp": get_temp(),
            "gpus": get_gpus(),
            "t3_abierto": t3_estado(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            post_json("/status", status)
        except Exception as e:
            print(f"[WARN] status: {e}", flush=True)
        try:
            cmd = get_json("/cmd").get("comando")
            if cmd:
                resultado = ejecutar_comando(cmd)
                post_json("/cmd/ack", {"comando": cmd, "resultado": resultado})
        except Exception as e:
            print(f"[WARN] cmd: {e}", flush=True)
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
