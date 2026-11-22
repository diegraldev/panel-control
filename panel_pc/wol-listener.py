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
            # NO escribir el flag aquí: la señal llega cuando la PC ya arrancó,
            # después de que wol-autologin consumió el flag. Reescribirlo aquí
            # contaminaría el SIGUIENTE arranque (autologin en arranques manuales).
            # El flag lo escribe únicamente el agente al apagar.
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
    # Escuchar en todas las interfaces: al arrancar, Tailscale quizá aún no
    # tiene asignada su IP, pero 0.0.0.0 siempre está disponible.
    ThreadingHTTPServer(("0.0.0.0", 8091), Handler).serve_forever()
