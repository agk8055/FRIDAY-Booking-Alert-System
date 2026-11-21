"""
Test Script for BookMyShow Alert System
Run this script to verify that sound, desktop notifications, browser opening,
and any configured remote alerts (Telegram, Discord, Email) work properly.
"""

import os
import sys
from pathlib import Path

# Add src/ to sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from config import config
from notifier import dispatch_alerts


def run_test():
    target = config.targets[0] if config.targets else None
    city = target.city if target else "Coimbatore"
    theatres = [th.name for th in target.target_theatres] if target and target.target_theatres else ["Broadway Cinemas"]

    print("=" * 60)
    print("🔔 BookMyShow Alert System - Notification Tester")
    print("=" * 60)
    print(f"Target City: {city}")
    print(f"Focus Theatre(s): {', '.join(theatres) if theatres else 'ANY'}")
    print(f"Sound Alert: {'ENABLED' if config.sound_alert else 'DISABLED'}")
    print(f"Windows Toast: {'ENABLED' if config.windows_toast else 'DISABLED'}")
    print(f"Open in Browser: {'ENABLED' if config.open_browser_on_open else 'DISABLED'}")
    print(f"Telegram: {'ENABLED' if config.telegram_enabled else 'DISABLED'}")
    print(f"Discord: {'ENABLED' if config.discord_enabled else 'DISABLED'}")
    print(f"Email: {'ENABLED' if config.email_enabled else 'DISABLED'}")
    print("=" * 60)
    print("\nTriggering a simulated alert now...\n")

    test_movie = "Dune: Part Three (TEST SIMULATION)"
    test_url = target.url if target else "https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771"
    test_details = "This is a test notification to verify your alert configuration."

    dispatch_alerts(
        movie_name=test_movie,
        city=city,
        booking_url=test_url,
        extra_info=test_details,
        theatres=theatres
    )

    print("\n✅ Simulated alert sent! Check your screen, speakers, or Telegram.")


if __name__ == "__main__":
    run_test()
