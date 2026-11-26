#!/usr/bin/env python3
"""
FRIDAY - BookMyShow Booking Alert System
Local Web Dashboard Server & API.
"""

import argparse
import atexit
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .config import (
        BOOKINGS_LOG_FILE,
        MONITOR_LOG_FILE,
        PROJECT_ROOT,
        TargetItem,
        TheatreTarget,
        config,
        parse_theatres,
    )
except ImportError:
    from config import (
        BOOKINGS_LOG_FILE,
        MONITOR_LOG_FILE,
        PROJECT_ROOT,
        TargetItem,
        TheatreTarget,
        config,
        parse_theatres,
    )

TARGETS_FILE = PROJECT_ROOT / "targets.txt"
ASSETS_DIR = PROJECT_ROOT / "assets"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_HTML = STATIC_DIR / "dashboard.html"

DEFAULT_HEADER = """# ==============================================================================
# BookMyShow Monitoring Targets
#
# Paste any BookMyShow Movie URL (one per line).
# To focus on specific theatre(s), add a pipe '|' followed by comma-separated Cinema URLs!
#
# Format:
#   <Movie URL>
#   <Movie URL> | <Cinema Buytickets URL with Date>
#   <Movie URL> | <Cinema URL 1>, <Cinema URL 2> | @YYYYMMDD
#
# Examples:
#   # 1. Any theatre in the city:
#   https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706
#
#   # 2. Specific theatre on a specific date (recommended - checks theatre directly first):
#   https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706 | https://in.bookmyshow.com/cinemas/KOCH/vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally/buytickets/VMHE/20261127
#
#   # 3. Multiple base cinema URLs with shared @YYYYMMDD date parameter:
#   https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771 | https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/BWCB, https://in.bookmyshow.com/cinemas/coimbatore/the-cinema-brookefields-mall-coimbatore/SPIB | @20261127
# ==============================================================================
"""

DISABLED_PREFIX = "# [DISABLED] "


class MonitorManager:
    """Manages monitor execution (either in-process with monitor.py or as a subprocess)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._started_at: Optional[str] = None
        self._once_process: Optional[subprocess.Popen] = None
        # In-process hooks when dashboard is started directly by monitor.py
        self._in_process = False
        self._in_process_running = False
        self._pause_event: Optional[threading.Event] = None
        self._wake_event: Optional[threading.Event] = None

    def attach_in_process(self, pause_event: threading.Event, wake_event: threading.Event) -> None:
        with self._lock:
            self._in_process = True
            self._in_process_running = True
            self._pause_event = pause_event
            self._wake_event = wake_event
            self._started_at = datetime.datetime.now().isoformat(timespec="seconds")

    def _creation_flags(self) -> int:
        if sys.platform == "win32":
            return getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return 0

    def is_running(self) -> bool:
        with self._lock:
            if self._in_process:
                return self._in_process_running
            if self._process is None:
                return False
            if self._process.poll() is not None:
                self._process = None
                self._started_at = None
                return False
            return True

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if self._in_process:
                running = self._in_process_running
                pid = os.getpid() if running else None
            else:
                running = self._process is not None and self._process.poll() is None
                if not running:
                    self._process = None
                    self._started_at = None
                pid = self._process.pid if running and self._process else None

            once_running = self._once_process is not None and self._once_process.poll() is None
            if not once_running:
                self._once_process = None

            return {
                "running": running,
                "pid": pid,
                "started_at": self._started_at if running else None,
                "once_running": once_running,
                "check_interval": config.check_interval,
                "adaptive_polling": config.adaptive_polling,
                "eco_priority": config.eco_priority,
                "channels": {
                    "sound": config.sound_alert,
                    "toast": config.windows_toast,
                    "browser": config.open_browser_on_open,
                    "telegram": config.telegram_enabled,
                    "discord": config.discord_enabled,
                    "email": config.email_enabled,
                    "webhook": config.webhook_enabled,
                },
            }

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._in_process:
                if self._in_process_running:
                    return {"ok": True, "message": "Monitor is already running.", "pid": os.getpid()}
                self._in_process_running = True
                if self._pause_event is not None:
                    self._pause_event.clear()
                if self._wake_event is not None:
                    self._wake_event.set()
                self._started_at = datetime.datetime.now().isoformat(timespec="seconds")
                return {"ok": True, "message": "Monitor resumed.", "pid": os.getpid()}

            if self._process is not None and self._process.poll() is None:
                return {"ok": True, "message": "Monitor is already running.", "pid": self._process.pid}

            cmd = [sys.executable, str(PROJECT_ROOT / "run.py"), "--no-dashboard"]
            self._process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=self._creation_flags(),
            )
            self._started_at = datetime.datetime.now().isoformat(timespec="seconds")
            return {"ok": True, "message": "Monitor started.", "pid": self._process.pid}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._in_process:
                if not self._in_process_running:
                    return {"ok": True, "message": "Monitor is already paused."}
                self._in_process_running = False
                if self._pause_event is not None:
                    self._pause_event.set()
                return {"ok": True, "message": "Monitor paused. Dashboard remains active."}

            if self._process is None or self._process.poll() is not None:
                self._process = None
                self._started_at = None
                return {"ok": True, "message": "Monitor is not running."}

            pid = self._process.pid
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception as exc:
                return {"ok": False, "error": f"Failed to stop monitor (PID {pid}): {exc}"}
            finally:
                self._process = None
                self._started_at = None

            return {"ok": True, "message": f"Monitor (PID {pid}) stopped."}

    def check_once(self) -> Dict[str, Any]:
        with self._lock:
            if self._once_process is not None and self._once_process.poll() is None:
                return {"ok": False, "error": "A single check is already in progress."}

            cmd = [sys.executable, str(PROJECT_ROOT / "run.py"), "--once", "--no-dashboard"]
            self._once_process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=self._creation_flags(),
            )
            return {"ok": True, "message": "Single check started. Watch the live log for results."}

    def test_alert(self) -> Dict[str, Any]:
        cmd = [sys.executable, str(PROJECT_ROOT / "run.py"), "--test", "--no-dashboard"]
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=self._creation_flags(),
        )
        return {"ok": True, "message": "Test alert dispatched across enabled channels."}


monitor_manager = MonitorManager()
atexit.register(monitor_manager.stop)


def format_slug_title(slug: str) -> str:
    if not slug:
        return ""
    return slug.replace("-", " ").title()


def parse_target_line(line: str, enabled: bool = True) -> Optional[Dict[str, Any]]:
    cleaned = line.strip()
    if not cleaned:
        return None

    target_date = ""
    date_match = re.search(r"@(\d{8})\b", cleaned)
    if date_match:
        target_date = date_match.group(1)
        cleaned = re.sub(r"\s*@\d{8}\b", "", cleaned).strip()

    theatres: List[TheatreTarget] = []
    url_part = cleaned
    if "|" in cleaned:
        parts = [p.strip() for p in cleaned.split("|") if p.strip()]
        if parts:
            url_part = parts[0]
            if len(parts) > 1:
                theatres = parse_theatres(",".join(parts[1:]))

    url_clean = url_part.strip()
    if not url_clean.startswith("http"):
        return None

    item = TargetItem(url=url_clean, target_theatres=theatres, target_date=target_date)
    # If date wasn't specified via @YYYYMMDD, check if any theatre URL carried a date
    effective_date = item.target_date
    if not effective_date:
        for th in item.target_theatres:
            if th.date:
                effective_date = th.date
                break

    return {
        "enabled": enabled,
        "url": item.url,
        "city": item.city,
        "slug": item.slug,
        "title": format_slug_title(item.slug) or ("Cinema Schedule" if item.is_cinema else "BookMyShow Target"),
        "event_code": item.event_code,
        "is_cinema": item.is_cinema,
        "target_date": effective_date,
        "theatres_raw": ", ".join(th.raw_input for th in theatres),
        "theatres_display": item.theatres_display,
        "theatres": [
            {
                "raw_input": th.raw_input,
                "url": th.url,
                "name": th.name,
                "code": th.code,
                "date": th.date,
            }
            for th in item.target_theatres
        ],
    }


def serialize_target_dict(target: Dict[str, Any]) -> str:
    url = (target.get("url") or "").strip()
    if not url.startswith("http"):
        raise ValueError("Target URL must start with http:// or https://")

    theatres_raw = (target.get("theatres_raw") or "").strip()
    target_date = re.sub(r"[^0-9]", "", (target.get("target_date") or "").strip())
    if target_date and not re.fullmatch(r"\d{8}", target_date):
        raise ValueError("Target date must be in YYYYMMDD format (e.g. 20261217).")

    parts = [url]
    if theatres_raw:
        # Normalize comma-separated theatre entries
        theatre_items = [t.strip() for t in theatres_raw.split(",") if t.strip()]
        if theatre_items:
            parts.append(", ".join(theatre_items))

    if target_date:
        # Only append @YYYYMMDD if theatre URLs don't already end with that exact date
        already_dated = theatres_raw and all(
            re.search(rf"/{target_date}/?$", t.strip()) for t in theatres_raw.split(",") if t.strip()
        )
        if not already_dated:
            parts.append(f"@{target_date}")

    line = " | ".join(parts)
    enabled = target.get("enabled", True)
    return line if enabled else f"{DISABLED_PREFIX}{line}"


def read_targets_file() -> Dict[str, Any]:
    targets: List[Dict[str, Any]] = []
    raw_content = ""

    if TARGETS_FILE.exists():
        raw_content = TARGETS_FILE.read_text(encoding="utf-8")
        for line in raw_content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(DISABLED_PREFIX):
                parsed = parse_target_line(stripped[len(DISABLED_PREFIX) :], enabled=False)
                if parsed:
                    targets.append(parsed)
            elif not stripped.startswith("#"):
                parsed = parse_target_line(stripped, enabled=True)
                if parsed:
                    targets.append(parsed)

    return {"targets": targets, "raw": raw_content}


def write_targets_file(targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    header_lines: List[str] = []
    if TARGETS_FILE.exists():
        existing = TARGETS_FILE.read_text(encoding="utf-8").splitlines()
        for line in existing:
            stripped = line.strip()
            if stripped.startswith("#") and not stripped.startswith(DISABLED_PREFIX):
                header_lines.append(line)
            elif not stripped and header_lines and header_lines[-1].strip().startswith("#"):
                # Stop after the top comment banner
                break
            else:
                break

    if not header_lines:
        header_text = DEFAULT_HEADER.rstrip()
    else:
        header_text = "\n".join(header_lines).rstrip()

    serialized_lines = [serialize_target_dict(t) for t in targets]
    body = "\n".join(serialized_lines)
    content = f"{header_text}\n\n{body}\n" if body else f"{header_text}\n"
    TARGETS_FILE.write_text(content, encoding="utf-8")
    return read_targets_file()


def write_raw_targets_file(raw_content: str) -> Dict[str, Any]:
    TARGETS_FILE.write_text(raw_content, encoding="utf-8")
    return read_targets_file()


def read_tail(path_str: str, max_lines: int = 150) -> str:
    path = Path(path_str)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"Failed to read log: {exc}"


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the FRIDAY local dashboard."""

    def log_message(self, format: str, *args: Any) -> None:
        # Keep terminal output clean; only log errors
        pass

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html_path: Path) -> None:
        if not html_path.exists():
            self._send_json({"error": "dashboard.html not found"}, status=HTTPStatus.NOT_FOUND)
            return
        data = html_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, file_path: Path, content_type: str) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
            return
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_html(DASHBOARD_HTML)
        elif path == "/assets/banner.png":
            self._send_file(ASSETS_DIR / "banner.png", "image/png")
        elif path == "/api/targets":
            self._send_json(read_targets_file())
        elif path == "/api/monitor/status":
            self._send_json(monitor_manager.status())
        elif path == "/api/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            lines = int(qs.get("lines", ["150"])[0])
            self._send_json(
                {
                    "monitor_log": read_tail(MONITOR_LOG_FILE, max_lines=lines),
                    "bookings_log": read_tail(BOOKINGS_LOG_FILE, max_lines=lines),
                }
            )
        else:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_json({"error": f"Invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/targets":
            try:
                if "raw" in body and isinstance(body["raw"], str):
                    result = write_raw_targets_file(body["raw"])
                else:
                    targets = body.get("targets", [])
                    result = write_targets_file(targets)
                self._send_json({"ok": True, **result})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        elif path == "/api/targets/parse":
            url = (body.get("url") or "").strip()
            theatres_raw = (body.get("theatres_raw") or "").strip()
            target_date = re.sub(r"[^0-9]", "", (body.get("target_date") or "").strip())
            parts = [url]
            if theatres_raw:
                parts.append(theatres_raw)
            if target_date and re.fullmatch(r"\d{8}", target_date):
                parts.append(f"@{target_date}")
            parsed_item = parse_target_line(" | ".join(parts), enabled=True)
            if not parsed_item:
                self._send_json({"ok": False, "error": "Enter a valid BookMyShow URL starting with http/https."})
            else:
                self._send_json({"ok": True, "target": parsed_item})

        elif path == "/api/monitor/start":
            self._send_json(monitor_manager.start())

        elif path == "/api/monitor/stop":
            self._send_json(monitor_manager.stop())

        elif path == "/api/monitor/check-once":
            self._send_json(monitor_manager.check_once())

        elif path == "/api/monitor/test-alert":
            self._send_json(monitor_manager.test_alert())

        elif path == "/api/logs/clear":
            try:
                Path(MONITOR_LOG_FILE).write_text("", encoding="utf-8")
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        else:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)


def start_dashboard_background(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    pause_event: Optional[threading.Event] = None,
    wake_event: Optional[threading.Event] = None,
) -> Optional[ThreadingHTTPServer]:
    """Starts the dashboard server in a background daemon thread (used by monitor.py)."""
    if pause_event is not None and wake_event is not None:
        monitor_manager.attach_in_process(pause_event, wake_event)

    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError:
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if open_browser:
        url = f"http://{host}:{port}"
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    return server


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}"
    print("=" * 64)
    print("🎬  FRIDAY — Target & Monitor Control Dashboard")
    print("=" * 64)
    print(f"🌐 Dashboard URL: {url}")
    print(f"📄 Targets File:  {TARGETS_FILE}")
    print("Press Ctrl+C to stop the dashboard server.")
    print("=" * 64)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down FRIDAY Dashboard...")
    finally:
        monitor_manager.stop()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="FRIDAY Local Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Do not automatically open browser")
    args = parser.parse_args()
    serve_dashboard(host=args.host, port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
