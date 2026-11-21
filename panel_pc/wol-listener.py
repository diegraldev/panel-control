#!/usr/bin/env python3
"""Servicio ROOT que escucha en Tailscale y marca arranques por WoL.

El celular llama a POST /wake justo antes de enviar el magic packet.
El flag se guarda en disco; el servicio wol-autologin lo consume al arrancar.
Corre como root (independiente de la sesión gráfica).
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FLAG_DIR = "/var/lib/wol-autologin"
FLAG = os.path.join(FLAG_DIR, "pending")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/wake":
            os.makedirs(FLAG_DIR, exist_ok=True)
            with open(FLAG, "w") as f:
                f.write("wol")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("100.69.149.63", 8091), Handler).serve_forever()
