import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()


@dataclass
class TheatreTarget:
    raw_input: str
    url: str = ""
    code: str = ""
    slug: str = ""
    name: str = ""

    def __post_init__(self):
        cleaned = self.raw_input.strip()
        if cleaned.startswith("http"):
            self.url = cleaned
            path = urllib.parse.urlparse(cleaned).path.strip("/")
            parts = [p for p in path.split("/") if p]

            # 1. Strip trailing 8-digit date if present (e.g. 20261121)
            if parts and re.fullmatch(r"\d{8}", parts[-1]):
                parts.pop()

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
    target_theatres: List[TheatreTarget] = field(default_factory=list)
    is_cinema: bool = False

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

    @property
    def theatres_display(self) -> str:
        if not self.target_theatres:
            return "ANY Theatre in City"
        names = []
        for th in self.target_theatres:
            if th.code and th.code != th.name:
                names.append(f"{th.name} ({th.code})")
            else:
                names.append(th.name)
        return ", ".join(names)


def load_targets_from_file_or_env() -> List[TargetItem]:
    targets = []
    targets_file = "targets.txt"
    
    if os.path.exists(targets_file):
        try:
            with open(targets_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    
                    theatres = []
                    url_part = line
                    # Format: <Movie URL> | <Theatre URL 1>, <Theatre URL 2>
                    if "|" in line:
                        url_part, th_part = line.split("|", 1)
                        theatres = parse_theatres(th_part)
                    
                    url_clean = url_part.strip()
                    if url_clean.startswith("http"):
                        targets.append(TargetItem(url=url_clean, target_theatres=theatres))
        except Exception as e:
            print(f"Notice: Failed to read {targets_file}: {e}")

    # Fallback to .env if targets.txt had no active URLs
    if not targets:
        env_url = os.getenv("BMS_TARGET_URL", "https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771")
        targets.append(TargetItem(url=env_url))

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


config = Config()
