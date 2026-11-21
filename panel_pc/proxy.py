#!/usr/bin/env python3
"""Reverse proxy: http://diegral (PC, puerto 80) -> celular por Tailscale"""
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# El celular por Tailscale (nombre MagicDNS del dispositivo)
DESTINO = "http://100.113.245.28:8080"

class Proxy(BaseHTTPRequestHandler):
    def _proxy(self):
        url = DESTINO + self.path
        data = None
        if self.command in ("POST", "PUT"):
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
        req = urllib.request.Request(url, data=data, method=self.command)
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                body = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())

    do_GET = _proxy
    do_POST = _proxy

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Proxy).serve_forever()
