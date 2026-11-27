import json
import os
import re
import shutil
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
DATA_DIR = Path(os.getenv("DATA_DIR")).resolve() if os.getenv("DATA_DIR") else PROJECT_ROOT
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR = DATA_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BOOKINGS_LOG_FILE = str(LOGS_DIR / "booking_opened.txt")
MONITOR_LOG_FILE = str(LOGS_DIR / "monitor.log")

TARGETS_FILE = DATA_DIR / "targets.json"
if DATA_DIR != PROJECT_ROOT and not TARGETS_FILE.exists():
    _seed_targets = PROJECT_ROOT / "targets.json"
    if _seed_targets.exists():
        shutil.copy2(_seed_targets, TARGETS_FILE)

# Load environment variables from .env in PROJECT_ROOT
load_dotenv(ENV_FILE)

MIN_CHECK_INTERVAL = 5
MAX_CHECK_INTERVAL = 86400


@dataclass
class TheatreTarget:
    raw_input: str
    url: str = ""
    code: str = ""
    slug: str = ""
    name: str = ""
    date: str = ""
    city_segment: str = ""

    def __post_init__(self):
        cleaned = self.raw_input.strip()
        if cleaned.startswith("http"):
            self.url = cleaned
            path = urllib.parse.urlparse(cleaned).path.strip("/")
            parts = [p for p in path.split("/") if p]

            # 0. Extract city segment if present (/cinemas/<city>/...)
            for marker in ("cinemas", "cinema"):
                if marker in parts:
                    c_idx = parts.index(marker)
                    if len(parts) > c_idx + 1:
                        self.city_segment = parts[c_idx + 1]
                    break

            # 1. Extract & strip trailing 8-digit date if present (e.g. 20261127)
            if parts and re.fullmatch(r"\d{8}", parts[-1]):
                self.date = parts.pop()

            # 2. Check if 'buytickets' is in URL path
            if "buytickets" in parts:
                bt_idx = parts.index("buytickets")
                if len(parts) > bt_idx + 1:
                    self.code = parts[bt_idx + 1].upper()
                if bt_idx > 0 and "-" in parts[bt_idx - 1]:
                    self.slug = parts[bt_idx - 1].lower()

            # 3. Check for cinema-<city>-<code>-MT pattern
            for p in parts:
                m = re.search(r"cinema-[a-zA-Z]{3,4}-([A-Z0-9]{3,5})-", p, re.I)
                if m:
                    self.code = m.group(1).upper()

            # 4. Fallback code from last parts if still empty
            if not self.code and parts:
                candidate = parts[-1]
                if 3 <= len(candidate) <= 6:
                    self.code = candidate.upper()

            # 5. Fallback slug from parts if still empty
            if not self.slug:
                for p in reversed(parts):
                    if "-" in p and not re.match(r"^(cinema|cinemas|movies|buytickets)", p.lower()):
                        self.slug = p.lower()
                        break

            # 6. Display Name
            if self.slug:
                self.name = self.slug.replace("-", " ").title()
            elif self.code:
                self.name = self.code
        else:
            self.name = cleaned
            self.code = cleaned.upper()
            self.slug = cleaned.lower().replace(" ", "-")

    def build_dated_url(self, target_date: str) -> str:
        """Builds a dated buytickets cinema URL if this target has a base URL without a date."""
        if not self.url or not target_date or self.date or not re.fullmatch(r"\d{8}", target_date):
            return self.url

        parsed = urllib.parse.urlparse(self.url)
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        base = f"{parsed.scheme}://{parsed.netloc}"

        if "buytickets" in parts:
            self.url = f"{base}/{'/'.join(parts)}/{target_date}"
            self.date = target_date
        elif self.city_segment and self.slug and self.code:
            self.url = f"{base}/cinemas/{self.city_segment}/{self.slug}/buytickets/{self.code}/{target_date}"
            self.date = target_date

        return self.url


def parse_theatres(theatres_str: str) -> List[TheatreTarget]:
    if not theatres_str:
        return []
    items = []
    for item in theatres_str.split(","):
        item = item.strip()
        if item:
            items.append(TheatreTarget(raw_input=item))
    return items


@dataclass
class TargetItem:
    url: str
    city: str = ""
    slug: str = ""
    event_code: str = ""
    event_group_code: str = ""
    target_theatres: List[TheatreTarget] = field(default_factory=list)
    is_cinema: bool = False
    target_date: str = ""

    def __post_init__(self):
        # Auto-detect properties from BookMyShow URL
        path = urllib.parse.urlparse(self.url).path.strip("/")
        parts = path.split("/")
        
        if "movies" in parts:
            idx = parts.index("movies")
            # /movies/<city>/<slug>/<code>
            if len(parts) > idx + 1:
                self.city = parts[idx + 1].replace("-", " ").title()
            if len(parts) > idx + 2:
                self.slug = parts[idx + 2]
            if len(parts) > idx + 3:
                self.event_code = parts[idx + 3]
            self.is_cinema = False
        elif "cinemas" in parts or "cinema" in parts:
            idx = parts.index("cinemas") if "cinemas" in parts else parts.index("cinema")
            if len(parts) > idx + 1:
                self.city = parts[idx + 1].replace("-", " ").title()
            if len(parts) > idx + 2:
                self.slug = parts[idx + 2]
            if len(parts) > idx + 3:
                self.event_code = parts[idx + 3]
            self.is_cinema = True

        if self.target_date:
            for th in self.target_theatres:
                if th.url and not th.date:
                    th.build_dated_url(self.target_date)

    @property
    def has_theatre_urls(self) -> bool:
        return any(bool(th.url) for th in self.target_theatres)

    @property
    def theatres_display(self) -> str:
        if not self.target_theatres:
            return "ANY Theatre in City"
        names = []
        for th in self.target_theatres:
            if th.code and th.code != th.name:
                date_suffix = f", {th.date}" if th.date else ""
                names.append(f"{th.name} ({th.code}{date_suffix})")
            else:
                names.append(th.name)
        return ", ".join(names)


def load_targets_from_file_or_env() -> List[TargetItem]:
    targets = []
    targets_file = (DATA_DIR / "targets.json") if os.getenv("DATA_DIR") else (PROJECT_ROOT / "targets.json")
    if not targets_file.exists():
        targets_file = PROJECT_ROOT / "targets.json"
    if not targets_file.exists():
        targets_file = Path("targets.json")

    if targets_file.exists():
        try:
            raw_text = targets_file.read_text(encoding="utf-8").strip()
            if not raw_text:
                return targets
            data = json.loads(raw_text)
            entries = data.get("targets", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("enabled", True):
                    continue

                url_clean = str(entry.get("url") or "").strip()
                if not url_clean.startswith("http"):
                    continue

                target_date = re.sub(r"[^0-9]", "", str(entry.get("target_date") or "").strip())
                if target_date and not re.fullmatch(r"\d{8}", target_date):
                    target_date = ""

                raw_theatres = entry.get("theatres")
                if isinstance(raw_theatres, list):
                    theatre_inputs = []
                    for th in raw_theatres:
                        if isinstance(th, dict):
                            val = str(th.get("raw_input") or th.get("url") or "").strip()
                        else:
                            val = str(th).strip()
                        if val:
                            theatre_inputs.append(val)
                    theatres = parse_theatres(",".join(theatre_inputs)) if theatre_inputs else []
                elif isinstance(raw_theatres, str) and raw_theatres.strip():
                    theatres = parse_theatres(raw_theatres)
                else:
                    theatres = []

                targets.append(
                    TargetItem(
                        url=url_clean,
                        target_theatres=theatres,
                        target_date=target_date,
                    )
                )
        except Exception as e:
            print(f"Notice: Failed to read {targets_file}: {e}")

    return targets


@dataclass
class Config:
    targets: List[TargetItem] = field(default_factory=load_targets_from_file_or_env)
    
    # Check frequency in seconds (default: 60s)
    check_interval: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
    
    # Run browser headless (silent in background)
    headless: bool = os.getenv("HEADLESS_BROWSER", "true").lower() == "true"
    
    # Alert Channels Toggle
    sound_alert: bool = os.getenv("SOUND_ALERT", "true").lower() == "true"
    windows_toast: bool = os.getenv("WINDOWS_TOAST", "true").lower() == "true"
    open_browser_on_open: bool = os.getenv("OPEN_BROWSER_ON_OPEN", "true").lower() == "true"
    
    # Telegram Notifications
    telegram_enabled: bool = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Discord Webhook Notifications
    discord_enabled: bool = os.getenv("DISCORD_ENABLED", "false").lower() == "true"
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    
    # Email Notifications
    email_enabled: bool = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
    smtp_server: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_recipient: str = os.getenv("EMAIL_RECIPIENT", "")
    
    # Generic Webhook (optional)
    webhook_enabled: bool = os.getenv("WEBHOOK_ENABLED", "false").lower() == "true"
    webhook_url: str = os.getenv("WEBHOOK_URL", "")

    # Performance & System Efficiency Settings
    recycle_browser_cycles: int = int(os.getenv("RECYCLE_BROWSER_CYCLES", "50"))
    adaptive_polling: bool = os.getenv("ADAPTIVE_POLLING", "false").lower() == "true"
    eco_priority: bool = os.getenv("ECO_PRIORITY", "true").lower() == "true"

    def reload_targets(self):
        self.targets = load_targets_from_file_or_env()

    def reload_check_interval(self, env_file: Path = ENV_FILE) -> int:
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("CHECK_INTERVAL_SECONDS="):
                        val_str = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        val = int(val_str)
                        if MIN_CHECK_INTERVAL <= val <= MAX_CHECK_INTERVAL:
                            self.check_interval = val
                            os.environ["CHECK_INTERVAL_SECONDS"] = str(val)
                        break
            except Exception:
                pass
        return self.check_interval


def update_check_interval(seconds: int, env_file: Path = ENV_FILE) -> int:
    """Validate, apply in-memory, and persist CHECK_INTERVAL_SECONDS to .env."""
    if isinstance(seconds, bool):
        raise ValueError(f"Check interval must be an integer between {MIN_CHECK_INTERVAL} and {MAX_CHECK_INTERVAL} seconds.")
    try:
        val = int(seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Check interval must be an integer between {MIN_CHECK_INTERVAL} and {MAX_CHECK_INTERVAL} seconds.") from exc

    if val < MIN_CHECK_INTERVAL or val > MAX_CHECK_INTERVAL:
        raise ValueError(f"Check interval must be between {MIN_CHECK_INTERVAL} and {MAX_CHECK_INTERVAL} seconds.")

    config.check_interval = val
    os.environ["CHECK_INTERVAL_SECONDS"] = str(val)

    new_line = f"CHECK_INTERVAL_SECONDS={val}"
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if re.match(r"^\s*CHECK_INTERVAL_SECONDS\s*=", line):
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            lines.append(new_line)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        env_file.write_text(new_line + "\n", encoding="utf-8")

    return val


config = Config()
