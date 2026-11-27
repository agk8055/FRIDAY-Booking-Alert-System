import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import dashboard  # noqa: E402
import monitor  # noqa: E402


class _FakeSocket:
    """Minimal socket shim to exercise DashboardHandler without binding TCP ports."""

    def __init__(self, raw_request: bytes):
        self._rfile = io.BytesIO(raw_request)
        self._wfile = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        return self._wfile

    def sendall(self, data: bytes) -> None:
        self._wfile.write(data)

    def close(self) -> None:
        pass


class TestDashboardServerAndAPI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.targets_file = self.tmp_path / "targets.txt"
        self.monitor_log = self.tmp_path / "monitor.log"
        self.bookings_log = self.tmp_path / "booking_opened.txt"

        self.targets_file.write_text(
            "https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706 | "
            "https://in.bookmyshow.com/cinemas/KOCH/vanitha-cineplex/buytickets/VMHE | @20261217\n",
            encoding="utf-8",
        )
        self.monitor_log.write_text("2026-11-27 21:00:00 [INFO] Monitor initialized.\n", encoding="utf-8")
        self.bookings_log.write_text(
            "[2026-11-27 21:05:00] Avengers: Doomsday (Kochi)\n"
            "URL: https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706\n\n",
            encoding="utf-8",
        )

        self.patches = [
            patch.object(dashboard, "TARGETS_FILE", self.targets_file),
            patch.object(dashboard, "MONITOR_LOG_FILE", str(self.monitor_log)),
            patch.object(dashboard, "BOOKINGS_LOG_FILE", str(self.bookings_log)),
            patch.dict(os.environ, {"DASHBOARD_PASSWORD": ""}, clear=False),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp_dir.cleanup()

    def _request(self, method: str, path: str, body=None):
        body_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
        headers = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
        if body is not None:
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(body_bytes)}")
        raw_req = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body_bytes

        sock = _FakeSocket(raw_req)
        dashboard.DashboardHandler(sock, ("127.0.0.1", 0), None)
        raw_resp = sock._wfile.getvalue()
        head, _, resp_body = raw_resp.partition(b"\r\n\r\n")
        head_lines = head.decode("utf-8", errors="replace").split("\r\n")
        status = int(head_lines[0].split(" ", 2)[1])
        resp_headers = {}
        for line in head_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                resp_headers[k.strip().lower()] = v.strip()
        return status, resp_headers.get("content-type", ""), resp_body

    def test_health_get_and_head_fast_and_unauthenticated(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "secret"}, clear=False):
            status, ctype, raw = self._request("GET", "/health")
            self.assertEqual(status, 200)
            self.assertIn("application/json", ctype)
            self.assertEqual(json.loads(raw), {"ok": True})

            status_qs, _, raw_qs = self._request("GET", "/health?t=12345")
            self.assertEqual(status_qs, 200)
            self.assertEqual(json.loads(raw_qs), {"ok": True})

            status_head, ctype_head, raw_head = self._request("HEAD", "/health")
            self.assertEqual(status_head, 200)
            self.assertIn("application/json", ctype_head)
            self.assertEqual(raw_head, b"")

    def test_html_and_assets_served(self):
        status, ctype, raw = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        html = raw.decode("utf-8")
        self.assertIn("FRIDAY", html)
        for marker in [
            "startMonitor()",
            "stopMonitor()",
            "checkOnce()",
            "testAlert()",
            "openAddModal()",
            "openRawModal()",
            "saveTargetForm()",
            "saveRawTargets()",
            "confirmClearActiveLog()",
            "copyTargetUrl(",
            "copyBookingUrl(",
            "startInlineTheatreEdit(",
            "confirmDeleteTheatre(",
            "startAddCardTheatre(",
            "addModalTheatreRow(",
            "removeModalTheatreRow(",
        ]:
            self.assertIn(marker, html)

        status_img, ctype_img, raw_img = self._request("GET", "/assets/banner.png")
        self.assertEqual(status_img, 200)
        self.assertEqual(ctype_img, "image/png")
        self.assertGreater(len(raw_img), 100)

    def test_targets_crud_and_parse(self):
        status, _, raw = self._request("GET", "/api/targets")
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertEqual(len(data["targets"]), 1)
        self.assertTrue(data["targets"][0]["enabled"])
        self.assertEqual(data["targets"][0]["target_date"], "20261217")

        # Parse valid URL
        status, _, raw = self._request(
            "POST",
            "/api/targets/parse",
            {"url": "https://in.bookmyshow.com/movies/kochi/dune-part-three/ET00491771", "target_date": "20261220"},
        )
        self.assertEqual(status, 200)
        parsed = json.loads(raw)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["target"]["event_code"], "ET00491771")
        self.assertEqual(parsed["target"]["target_date"], "20261220")

        # Parse incomplete URL rejected
        status, _, raw = self._request("POST", "/api/targets/parse", {"url": "https://"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["ok"])

        # Toggle disabled & add second target
        updated_targets = [
            {**data["targets"][0], "enabled": False},
            {
                "enabled": True,
                "url": "https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771",
                "theatres_raw": "",
                "target_date": "20261225",
            },
        ]
        status, _, raw = self._request("POST", "/api/targets", {"targets": updated_targets})
        self.assertEqual(status, 200)
        res = json.loads(raw)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["targets"]), 2)
        self.assertFalse(res["targets"][0]["enabled"])
        self.assertTrue(res["targets"][1]["enabled"])

        # Multi-theatre target update & single-theatre removal
        multi_theatres = (
            "https://in.bookmyshow.com/cinemas/KOCH/vanitha-cineplex/buytickets/VMHE, "
            "https://in.bookmyshow.com/cinemas/ernk/padma-cinema-kochi/buytickets/PMCE"
        )
        status, _, raw = self._request(
            "POST",
            "/api/targets",
            {
                "targets": [
                    {
                        "enabled": True,
                        "url": "https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
                        "theatres_raw": multi_theatres,
                        "target_date": "20261217",
                    }
                ]
            },
        )
        self.assertEqual(status, 200)
        res = json.loads(raw)
        self.assertEqual(len(res["targets"][0]["theatres"]), 2)
        self.assertEqual(res["targets"][0]["theatres"][0]["code"], "VMHE")
        self.assertEqual(res["targets"][0]["theatres"][1]["code"], "PMCE")

        # Save raw targets.txt
        status, _, raw = self._request(
            "POST",
            "/api/targets",
            {"raw": "# comment\nhttps://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706\n"},
        )
        self.assertEqual(status, 200)
        res = json.loads(raw)
        self.assertEqual(len(res["targets"]), 1)

    def test_logs_and_clear_targets(self):
        status, _, raw = self._request("GET", "/api/logs?lines=bad")
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertIn("Monitor initialized", data["monitor_log"])
        self.assertIn("Avengers: Doomsday", data["bookings_log"])

        # Clear monitor log only
        status, _, _ = self._request("POST", "/api/logs/clear", {"target": "monitor"})
        self.assertEqual(status, 200)
        self.assertEqual(self.monitor_log.read_text(encoding="utf-8"), "")
        self.assertIn("Avengers: Doomsday", self.bookings_log.read_text(encoding="utf-8"))

        # Clear bookings log
        status, _, _ = self._request("POST", "/api/logs/clear", {"target": "bookings"})
        self.assertEqual(status, 200)
        self.assertEqual(self.bookings_log.read_text(encoding="utf-8"), "")

        # Invalid log target rejected
        status, _, _ = self._request("POST", "/api/logs/clear", {"target": "other"})
        self.assertEqual(status, 400)

    def test_monitor_status_and_in_process_controls(self):
        pause_ev = threading.Event()
        wake_ev = threading.Event()
        mgr = dashboard.MonitorManager()
        mgr.attach_in_process(pause_ev, wake_ev)

        with patch.object(dashboard, "monitor_manager", mgr):
            status, _, raw = self._request("GET", "/api/monitor/status")
            self.assertEqual(status, 200)
            s = json.loads(raw)
            self.assertTrue(s["running"])
            self.assertIsNotNone(s["started_at"])

            status, _, raw = self._request("POST", "/api/monitor/stop", {})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["ok"])
            self.assertTrue(pause_ev.is_set())

            status, _, raw = self._request("POST", "/api/monitor/start", {})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["ok"])
            self.assertFalse(pause_ev.is_set())
            self.assertTrue(wake_ev.is_set())

    def test_monitor_test_flag_works_without_relative_import_error(self):
        with patch.object(sys, "argv", ["run.py", "--test", "--no-dashboard"]), \
             patch.object(monitor, "dispatch_alerts") as mock_dispatch:
            monitor.main()
        mock_dispatch.assert_called_once()

    def test_every_inline_handler_in_dashboard_html_exists(self):
        html = dashboard.DASHBOARD_HTML.read_text(encoding="utf-8")
        script_part = html.split("<script>", 1)[1].split("</script>", 1)[0]
        handlers = set(re.findall(r'\bon(?:click|input|change|submit)="([^"]+)"', html))
        for expr in handlers:
            for fn in re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", expr):
                if fn in ("event", "preventDefault"):
                    continue
                self.assertRegex(
                    script_part,
                    rf"(?:async\s+)?function\s+{re.escape(fn)}\s*\(",
                    f"Missing JS function {fn} referenced in handler: {expr}",
                )


if __name__ == "__main__":
    unittest.main()
