#!/usr/bin/env python3
"""Contract and feature tests for panel_celular/server.py.

Standard library only: no pytest dependency, so this runs on the PC and in CI
with ``python3 -m unittest discover -s tests -v``.

The real Handler runs on an ephemeral loopback port; every state file is
redirected to a temporary directory so the repository stays clean.
"""
import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PATH = os.path.join(REPO_ROOT, "panel_celular", "server.py")


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="panel-test-")
        for name in ("REPO_URL", "REPO_TOKEN", "PANEL_TOKEN"):
            os.environ.pop(name, None)

        spec = importlib.util.spec_from_file_location("panel_server_under_test", SERVER_PATH)
        server = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = server
        spec.loader.exec_module(server)
        cls.server = server

        # Isolate all on-disk state (the existing files live in BASE by default).
        for name in ("STATUS_FILE", "CMD_FILE", "ACK_FILE", "ESTADO_FILE",
                     "VERSION_FILE"):
            setattr(server, name, os.path.join(cls.tmpdir,
                                               os.path.basename(getattr(server, name))))
        server.REPO = ""
        server.TOKEN = ""
        server.PANEL_TOKEN = ""
        index = os.path.join(cls.tmpdir, "index.html")
        with open(index, "w") as handle:
            handle.write("<!doctype html><title>test</title>")
        server.ASSETS = server.Assets(index)
        server.load_state()

        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        s = self.server
        with s._state_lock:
            s._status_cache["data"] = None
            s._status_cache["written"] = 0.0
            s._cmd_cache = {}
            s._ack_cache = {}
            s._estado_cache = {}
        s._last_status_write = 0.0
        s._last_ts_ms = 0
        s.PANEL_TOKEN = ""
        for name in ("STATUS_FILE", "CMD_FILE", "ACK_FILE", "ESTADO_FILE"):
            try:
                os.unlink(getattr(s, name))
            except OSError:
                pass

    # ------------------------------------------------------------- helpers
    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        final_headers = dict(headers or {})
        payload = raw
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode()
            final_headers.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=payload, headers=final_headers)
        response = connection.getresponse()
        data = response.read()
        result = {
            "status": response.status,
            "ctype": response.getheader("Content-Type"),
            "headers": dict(response.getheaders()),
            "body": data,
        }
        connection.close()
        return result

    def json_of(self, result):
        return json.loads(result["body"].decode())

    def publish(self, **extra):
        data = {
            "hostname": "pc-host", "ip": "10.0.0.5",
            "ram": {"percent": 60, "used_mb": 600, "total_mb": 1000, "free_mb": 400},
            "cpu_percent": 42, "cpu_temp": {"max": 55, "source": "k10temp"},
            "gpus": [{"name": "GPU", "usage": 10, "temp": 50, "type": "nvidia"}],
            "t3_open": True, "sleep_active": False, "timestamp": time.time(),
        }
        data.update(extra)
        return self.request("POST", "/status", data)

    # ------------------------------------------------ existing contract (1-6)
    def test_existing_get_endpoints(self):
        for path, ctype in (("/", "text/html"), ("/version", "application/json"),
                            ("/status", "application/json"), ("/cmd", "application/json"),
                            ("/ack", "application/json"), ("/estado", "application/json"),
                            ("/battery", "application/json"),
                            ("/cmd?wait=1", "application/json")):
            result = self.request("GET", path)
            self.assertEqual(result["status"], 200, path)
            self.assertIn(ctype, result["ctype"], path)
        self.assertEqual(self.json_of(self.request("GET", "/status")),
                         {"status": "no data"})

    def test_existing_command_roundtrip(self):
        with mock.patch.object(self.server, "wake_pc_async"):
            for cmd in self.server.CMD_VALUES:
                with self.subTest(cmd=cmd):
                    sent = self.request("POST", "/cmd", {"comando": cmd, "ts": 123})
                    self.assertEqual(sent["status"], 200)
                    self.assertEqual(self.json_of(sent), {"ok": True, "comando": cmd})
                    queued = self.json_of(self.request("GET", "/cmd"))
                    if cmd == "cancelar":
                        self.assertEqual(queued, {})
                    else:
                        self.assertEqual(queued["comando"], cmd)
                        self.assertEqual(queued["ts"], 123)
                    result = {"ok": True, "comando": cmd}
                    self.assertEqual(
                        self.json_of(self.request("POST", "/cmd/ack", result)),
                        {"ok": True})
                    self.assertEqual(self.json_of(self.request("GET", "/ack")), result)
                    self.assertEqual(self.json_of(self.request("GET", "/cmd")), {})

    def test_existing_status_publish(self):
        data = {
            "hostname": "pc-host", "ip": "10.0.0.5",
            "ram": {"total_mb": 1000, "used_mb": 600, "free_mb": 400, "percent": 60},
            "cpu_percent": 42, "cpu_temp": {"max": 55, "source": "k10temp"},
            "gpus": [{"name": "GPU", "usage": 10, "temp": 50, "type": "nvidia"}],
            "t3_open": True, "sleep_active": False, "timestamp": time.time(),
        }
        self.assertEqual(self.json_of(self.request("POST", "/status", data)), {"ok": True})
        got = self.json_of(self.request("GET", "/status"))
        for key in ("hostname", "ip", "ram", "cpu_percent", "cpu_temp", "gpus",
                    "t3_open", "sleep_active", "timestamp",
                    "recibido_ts", "recibido_en_celular"):
            self.assertIn(key, got, key)
        self.assertTrue(got["recibido_en_celular"])
        self.assertGreater(got["recibido_ts"], 0)

    def test_status_disk_throttle_unchanged(self):
        with mock.patch.object(self.server, "write_json",
                               wraps=self.server.write_json) as writer:
            self.request("POST", "/status", {"hostname": "first"})
            time.sleep(1.0)
            self.request("POST", "/status", {"hostname": "second"})
            self.assertEqual(writer.call_count, 1, "status.json must be written at most once")
        with open(self.server.STATUS_FILE) as handle:
            self.assertEqual(json.load(handle)["hostname"], "first")
        self.assertEqual(self.json_of(self.request("GET", "/status"))["hostname"],
                         "second")  # memory is fresh even though disk lags

    def test_malformed_json(self):
        result = self.request("POST", "/cmd", raw=b"{not json")
        self.assertEqual(result["status"], 400)
        self.assertIn("error", self.json_of(result))

    def test_version_check_additive(self):
        result = self.request("POST", "/version?check=1", {})
        self.assertEqual(result["status"], 200)
        self.assertEqual(self.json_of(result), {"ok": True, "check": "queued"})
        version = self.json_of(self.request("GET", "/version"))
        self.assertIn("sha", version)
        self.assertEqual(version["repo"], "local")

    # ------------------------------------------------------------ Siri (7-14)
    def test_siri_offline(self):
        result = self.request("GET", "/siri")
        self.assertEqual(result["status"], 200)
        self.assertIn("text/plain", result["ctype"])
        self.assertEqual(result["body"].decode(), "PC is off.")

    def test_siri_online(self):
        self.publish()
        result = self.request("GET", "/siri")
        self.assertEqual(result["status"], 200)
        self.assertIn("text/plain", result["ctype"])
        text = result["body"].decode()
        for fragment in ("pc-host", "10.0.0.5", "42 percent", "60 percent",
                         "T3 Code is open", "Sleep mode is off"):
            self.assertIn(fragment, text)
        self.assertNotIn("\n", text)

    def test_siri_partial_status(self):
        self.request("POST", "/status", {"hostname": "solo\npc"})
        result = self.request("GET", "/siri")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"].decode(), "PC online on solo pc.")

    def test_spoken_cmd_plain_text_and_ts(self):
        with mock.patch.object(self.server, "wake_pc_async"):
            result = self.request("POST", "/cmd?spoken=1", {"comando": "t3-open"})
        self.assertEqual(result["status"], 200)
        self.assertIn("text/plain", result["ctype"])
        self.assertEqual(result["body"].decode(), "Opening T3 Code.")
        queued = self.json_of(self.request("GET", "/cmd"))
        self.assertEqual(queued["comando"], "t3-open")
        self.assertIsInstance(queued["ts"], int)
        self.assertLess(abs(queued["ts"] - int(time.time() * 1000)), 5000)

    def test_spoken_cmd_accept_header(self):
        result = self.request("POST", "/cmd", {"comando": "sleep-on"},
                              headers={"Accept": "text/plain"})
        self.assertEqual(result["status"], 200)
        self.assertIn("text/plain", result["ctype"])
        self.assertEqual(result["body"].decode(), "Sleep mode on.")

    def test_spoken_ts_unique(self):
        seen = set()
        for _ in range(2):
            self.request("POST", "/cmd?spoken=1", {"comando": "t3-close"})
            seen.add(self.json_of(self.request("GET", "/cmd"))["ts"])
        self.assertEqual(len(seen), 2)

    def test_spoken_invalid_command(self):
        result = self.request("POST", "/cmd?spoken=1", {"comando": "bogus"})
        self.assertEqual(result["status"], 400)
        self.assertIn("text/plain", result["ctype"])
        self.assertEqual(result["body"].decode(), "Invalid command.")

    def test_guard_still_active(self):
        self.server.PANEL_TOKEN = "secret"
        try:
            first = self.request("POST", "/cmd", {"comando": "wake"})
            self.assertEqual(first["status"], 401)
            self.assertIn("error", self.json_of(first))
            second = self.request("POST", "/cmd", {"comando": "wake"},
                                  headers={"X-Panel-Token": "secret"})
            self.assertEqual(second["status"], 200)
            third = self.request("POST", "/cmd?token=secret", {"comando": "wake"})
            self.assertEqual(third["status"], 200)
            fourth = self.request("GET", "/siri")
            self.assertEqual(fourth["status"], 200)  # reads are not token-guarded
        finally:
            self.server.PANEL_TOKEN = ""

    def test_is_online_boundary(self):
        now = time.time()
        fresh = {"recibido_en_celular": True, "recibido_ts": now - 14.9}
        stale = {"recibido_en_celular": True, "recibido_ts": now - 15.1}
        no_flag = {"recibido_ts": now}
        self.assertTrue(self.server.is_online(fresh, now))
        self.assertFalse(self.server.is_online(stale, now))
        self.assertFalse(self.server.is_online(no_flag, now))
        self.assertFalse(self.server.is_online({}, now))


if __name__ == "__main__":
    unittest.main()
