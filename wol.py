#!/usr/bin/env python3
"""Wake-on-LAN: envia un magic packet por UDP broadcast.

Funciona igual en Linux, Termux (Android) y iPhone. No requiere root.
Uso:
    python3 wol.py d8:bb:c1:22:1a:d1
    python3 wol.py d8:bb:c1:22:1a:d1 --ip 192.168.100.255 --port 9
"""
import argparse
import socket


def magic_packet(mac: str) -> bytes:
    mac = mac.replace(":", "").replace("-", "").strip()
    if len(mac) != 12:
        raise ValueError(f"MAC invalida: {mac}")
    hw = bytes.fromhex(mac)
    return b"\xff" * 6 + hw * 16


def wake(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(packet, (broadcast, port))
    print(f"Magic packet enviado a {mac} -> {broadcast}:{port} ({len(packet)} bytes)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Wake-on-LAN por UDP broadcast")
    p.add_argument("mac", help="MAC de la tarjeta de red (ej. d8:bb:c1:22:1a:d1)")
    p.add_argument("--ip", default="255.255.255.255", help="IP de broadcast (default 255.255.255.255)")
    p.add_argument("--port", type=int, default=9, help="Puerto UDP (default 9)")
    a = p.parse_args()
    wake(a.mac, a.ip, a.port)
