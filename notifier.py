"""
Notification Dispatcher Module (forwarder to src/notifier.py).
"""

import sys
from pathlib import Path

# Add src/ to sys.path for runtime execution
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from src.notifier import (
    dispatch_alerts,
    open_in_browser,
    play_sound_alert,
    send_discord,
    send_email,
    send_generic_webhook,
    send_telegram,
    send_windows_toast,
)

__all__ = [
    "dispatch_alerts",
    "play_sound_alert",
    "send_windows_toast",
    "open_in_browser",
    "send_telegram",
    "send_discord",
    "send_email",
    "send_generic_webhook",
]
