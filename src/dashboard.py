#!/usr/bin/env python3
"""
FRIDAY - BookMyShow Booking Alert System
Local Web Dashboard Server & API.
"""

import argparse
import atexit
import base64
import datetime
import hmac
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
        ENV_FILE,
        MONITOR_LOG_FILE,
        PROJECT_ROOT,
        TARGETS_FILE,
        TargetItem,
        TheatreTarget,
        config,
        parse_theatres,
        update_check_interval,
    )
except ImportError:
    from config import (
        BOOKINGS_LOG_FILE,
        ENV_FILE,
        MONITOR_LOG_FILE,
        PROJECT_ROOT,
        TARGETS_FILE,
        TargetItem,
        TheatreTarget,
        config,
        parse_theatres,
        update_check_interval,
    )

ASSETS_DIR = PROJECT_ROOT / "assets"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_HTML = STATIC_DIR / "dashboard.html"

_HEALTH_BODY = b'{"ok":true}'
_HEALTH_CONTENT_LENGTH = str(len(_HEALTH_BODY))


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

    def set_check_interval(self, seconds: Any) -> Dict[str, Any]:
        try:
            new_val = update_check_interval(seconds, env_file=ENV_FILE)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        with self._lock:
            if self._in_process and self._in_process_running and self._wake_event is not None:
                self._wake_event.set()

        return {
            "ok": True,
            "check_interval": new_val,
            "message": f"Check interval updated to {new_val}s.",
        }


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
    parsed_url = urllib.parse.urlparse(url_clean)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
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


def _extract_theatre_inputs(raw_theatres: Any) -> List[str]:
    if isinstance(raw_theatres, list):
        items: List[str] = []
        for entry in raw_theatres:
            if isinstance(entry, dict):
                val = str(entry.get("raw_input") or entry.get("url") or "").strip()
            else:
                val = str(entry).strip()
            if val:
                items.append(val)
        return items
    if isinstance(raw_theatres, str):
        return [t.strip() for t in raw_theatres.split(",") if t.strip()]
    return []


def build_target_dict(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url_clean = str(entry.get("url") or "").strip()
    parsed_url = urllib.parse.urlparse(url_clean)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return None

    target_date = re.sub(r"[^0-9]", "", str(entry.get("target_date") or "").strip())
    if target_date and not re.fullmatch(r"\d{8}", target_date):
        target_date = ""

    raw_theatres = entry.get("theatres") if "theatres" in entry else entry.get("theatres_raw")
    theatre_inputs = _extract_theatre_inputs(raw_theatres)
    theatres = parse_theatres(",".join(theatre_inputs)) if theatre_inputs else []

    item = TargetItem(url=url_clean, target_theatres=theatres, target_date=target_date)
    effective_date = item.target_date
    if not effective_date:
        for th in item.target_theatres:
            if th.date:
                effective_date = th.date
                break

    return {
        "enabled": bool(entry.get("enabled", True)),
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


def serialize_target_entry(target: Dict[str, Any]) -> Dict[str, Any]:
    url = str(target.get("url") or "").strip()
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("Target URL must be a valid URL starting with http:// or https://")

    raw_theatres = target.get("theatres_raw")
    if raw_theatres is None:
        raw_theatres = target.get("theatres")
    theatre_items = _extract_theatre_inputs(raw_theatres)

    target_date = re.sub(r"[^0-9]", "", str(target.get("target_date") or "").strip())
    if target_date and not re.fullmatch(r"\d{8}", target_date):
        raise ValueError("Target date must be in YYYYMMDD format (e.g. 20261217).")

    return {
        "enabled": bool(target.get("enabled", True)),
        "url": url,
        "theatres": theatre_items,
        "target_date": target_date,
    }


def read_targets_file() -> Dict[str, Any]:
    targets: List[Dict[str, Any]] = []
    raw_content = json.dumps({"targets": []}, indent=2) + "\n"

    if TARGETS_FILE.exists():
        raw_content = TARGETS_FILE.read_text(encoding="utf-8")
        if raw_content.strip():
            data = json.loads(raw_content)
            entries = data.get("targets", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for entry in entries:
                if isinstance(entry, dict):
                    built = build_target_dict(entry)
                    if built:
                        targets.append(built)

    return {"targets": targets, "raw": raw_content}


def write_targets_file(targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    serialized = [serialize_target_entry(t) for t in targets]
    content = json.dumps({"targets": serialized}, indent=2) + "\n"
    TARGETS_FILE.write_text(content, encoding="utf-8")
    return read_targets_file()


def write_raw_targets_file(raw_content: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if isinstance(data, dict):
        entries = data.get("targets")
        if not isinstance(entries, list):
            raise ValueError('JSON object must contain a "targets" array.')
    elif isinstance(data, list):
        entries = data
    else:
        raise ValueError('JSON must be an object with a "targets" array or a JSON array of targets.')

    serialized: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each target entry must be a JSON object.")
        serialized.append(serialize_target_entry(entry))

    formatted = json.dumps({"targets": serialized}, indent=2) + "\n"
    TARGETS_FILE.write_text(formatted, encoding="utf-8")
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

    def address_string(self) -> str:
        # Avoid reverse DNS lookups (socket.getfqdn) on every request
        return self.client_address[0]

    def _is_health_path(self) -> bool:
        raw_path = self.path
        return raw_path == "/health" or raw_path.startswith("/health?")

    def _send_health(self, head_only: bool = False) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", _HEALTH_CONTENT_LENGTH)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            self._write_body(_HEALTH_BODY)

    def _write_body(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browser closed/cancelled the connection before reading the full response
            pass

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write_body(data)

    def _send_html(self, html_path: Path) -> None:
        if not html_path.exists():
            self._send_json({"error": "dashboard.html not found"}, status=HTTPStatus.NOT_FOUND)
            return
        data = html_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._write_body(data)

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
        self._write_body(data)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _check_auth(self) -> bool:
        expected_password = os.getenv("DASHBOARD_PASSWORD", "")
        if not expected_password:
            return True
        expected_user = os.getenv("DASHBOARD_USERNAME", "admin") or "admin"
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
                user, _, pwd = decoded.partition(":")
                if hmac.compare_digest(user, expected_user) and hmac.compare_digest(pwd, expected_password):
                    return True
            except Exception:
                pass
        body = b"Unauthorized"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="FRIDAY Dashboard"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)
        return False

    def do_HEAD(self) -> None:
        if self._is_health_path():
            self._send_health(head_only=True)
            return

        if not self._check_auth():
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self._is_health_path():
            self._send_health(head_only=False)
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self._check_auth():
            return

        if path in ("/", "/index.html"):
            self._send_html(DASHBOARD_HTML)
        elif path == "/assets/banner.png":
            self._send_file(ASSETS_DIR / "banner.png", "image/png")
        elif path.startswith("/static/"):
            rel_name = path[len("/static/"):]
            if not rel_name or "/" in rel_name or "\\" in rel_name or ".." in rel_name:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            static_file = STATIC_DIR / rel_name
            ext = static_file.suffix.lower()
            ctype_map = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".html": "text/html; charset=utf-8",
                ".png": "image/png",
                ".svg": "image/svg+xml",
            }
            self._send_file(static_file, ctype_map.get(ext, "application/octet-stream"))
        elif path == "/api/targets":
            self._send_json(read_targets_file())
        elif path == "/api/monitor/status":
            self._send_json(monitor_manager.status())
        elif path == "/api/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                lines = max(10, min(2000, int(qs.get("lines", ["150"])[0])))
            except (TypeError, ValueError):
                lines = 150
            self._send_json(
                {
                    "monitor_log": read_tail(MONITOR_LOG_FILE, max_lines=lines),
                    "bookings_log": read_tail(BOOKINGS_LOG_FILE, max_lines=lines),
                }
            )
        else:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._check_auth():
            return

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

        elif path == "/api/monitor/interval":
            raw_val = body.get("check_interval", body.get("interval"))
            res = monitor_manager.set_check_interval(raw_val)
            status = HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(res, status=status)

        elif path == "/api/logs/clear":
            try:
                target_log = (body.get("target") or "monitor").strip().lower()
                if target_log == "bookings":
                    Path(BOOKINGS_LOG_FILE).write_text("", encoding="utf-8")
                elif target_log == "monitor":
                    Path(MONITOR_LOG_FILE).write_text("", encoding="utf-8")
                else:
                    self._send_json({"ok": False, "error": "Invalid log target."}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json({"ok": True, "target": target_log})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        else:
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that suppresses noisy client-disconnect socket errors."""

    daemon_threads = True
    request_queue_size = 64

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


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
        server = _QuietThreadingHTTPServer((host, port), DashboardHandler)
    except OSError:
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    if open_browser:
        url = f"http://{host}:{port}"
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    return server


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = _QuietThreadingHTTPServer((host, port), DashboardHandler)
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
    default_port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8765")))
    default_host = os.getenv("DASHBOARD_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
    parser = argparse.ArgumentParser(description="FRIDAY Local Web Dashboard")
    parser.add_argument("--host", default=default_host, help=f"Bind host (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port to listen on (default: {default_port})")
    parser.add_argument("--no-open", action="store_true", help="Do not automatically open browser")
    args = parser.parse_args()
    open_browser = not args.no_open and not os.getenv("RENDER") and not os.getenv("PORT")
    serve_dashboard(host=args.host, port=args.port, open_browser=open_browser)


if __name__ == "__main__":
    main()
