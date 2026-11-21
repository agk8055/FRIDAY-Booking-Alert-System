#!/usr/bin/env python3
"""
BookMyShow Booking Alert & Monitoring System
Multi-Movie & Multi-City Monitor using direct BookMyShow URLs.
Optimized for high-speed, continuous, resource-efficient background execution on personal PCs.
"""

import argparse
import atexit
import datetime
import logging
import os
import signal
import sys
import time
from typing import Dict, List, Optional

from config import TargetItem, config
from bms_checker import BMSChecker, CheckResult
from notifier import dispatch_alerts

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("monitor.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("BMSMonitor")

# Global reference for clean shutdown
_ACTIVE_DRIVER = None


def set_process_efficiency_priority():
    """Sets the process priority to Below Normal on Windows to avoid impacting user PC performance."""
    if sys.platform == "win32" and config.eco_priority:
        try:
            import ctypes
            # BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
            logger.info("⚡ Background Eco Mode active: Process running in Below-Normal Priority.")
        except Exception:
            pass


def cleanup_driver():
    """Guarantees browser process termination on exit to prevent orphaned background instances."""
    global _ACTIVE_DRIVER
    if _ACTIVE_DRIVER:
        try:
            _ACTIVE_DRIVER.quit()
        except Exception:
            pass
        finally:
            _ACTIVE_DRIVER = None


atexit.register(cleanup_driver)


def signal_handler(sig, frame):
    cleanup_driver()
    print("\n\n👋 FRIDAY Monitor stopped safely. Have a great day!")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def calculate_sleep_interval(base_interval: int) -> int:
    """Calculates sleep time, applying adaptive polling during off-peak night hours if configured."""
    if not config.adaptive_polling:
        return base_interval
    now_hour = datetime.datetime.now().hour
    # Off-peak hours: Midnight to 7 AM (tickets almost never open during these hours)
    if 0 <= now_hour < 7:
        off_peak_interval = max(base_interval * 4, 300)
        logger.info(f"🌙 Off-peak night hours (00:00 - 07:00): Adaptive interval set to {off_peak_interval}s.")
        return off_peak_interval
    return base_interval


def print_banner(targets: List[TargetItem]):
    print("=" * 68)
    print("🎬  BookMyShow Multi-Movie & Multi-City Booking Alert System (FRIDAY)")
    print("=" * 68)
    print(f"📋 Active Targets ({len(targets)} total):")
    for idx, t in enumerate(targets, 1):
        th_info = f" | 🏛️ {t.theatres_display}" if t.target_theatres else " | 🏛️ ANY Theatre"
        city_info = f" | 📍 {t.city}" if t.city else ""
        print(f"  {idx}. [{t.slug or 'Direct URL'}]{city_info}{th_info}")
        print(f"     🔗 {t.url}")
    print("-" * 68)
    print(f"⏱️ Check Interval: {config.check_interval}s | 🔄 Browser Recycling: every {config.recycle_browser_cycles} cycles")
    print(f"🌱 Eco Priority: {'ON' if config.eco_priority else 'OFF'} | 🌙 Adaptive Polling: {'ON' if config.adaptive_polling else 'OFF'}")
    print(f"🔊 Audio: {'ON' if config.sound_alert else 'OFF'} | 💻 Toast: {'ON' if config.windows_toast else 'OFF'} | 🌐 Auto-Browser: {'ON' if config.open_browser_on_open else 'OFF'}")
    print(f"📱 Telegram: {'ON' if config.telegram_enabled else 'OFF'} | 📧 Email: {'ON' if config.email_enabled else 'OFF'} | 💬 Discord: {'ON' if config.discord_enabled else 'OFF'}")
    print("=" * 68)
    print("Press Ctrl+C at any time to stop monitoring.\n")


def monitor_loop(targets: List[TargetItem], interval: int, run_once: bool = False):
    global _ACTIVE_DRIVER
    checker = BMSChecker()
    check_cycle = 0

    # Track which targets have already alerted to prevent repeated alert loops
    # url -> last_alert_time
    alerted_targets: Dict[str, float] = {}

    # Initialize persistent driver session across cycles for maximum speed & efficiency
    try:
        _ACTIVE_DRIVER = checker.create_driver()
    except Exception as e:
        logger.error(f"Failed to start initial Chrome driver: {e}")
        return

    while True:
        check_cycle += 1
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now_str}] --- Cycle #{check_cycle}: Checking {len(targets)} Target(s) ---")

        # Periodically recycle browser session to release accumulated browser cache & memory
        if check_cycle > 1 and (check_cycle % config.recycle_browser_cycles == 0):
            logger.info(f"♻️ Recycling browser session (Cycle #{check_cycle}) to maintain clean memory...")
            cleanup_driver()
            try:
                _ACTIVE_DRIVER = checker.create_driver()
            except Exception as e:
                logger.error(f"Error during browser recycling: {e}")

        # Ensure active driver is ready
        if _ACTIVE_DRIVER is None:
            try:
                _ACTIVE_DRIVER = checker.create_driver()
            except Exception as e:
                logger.error(f"Unable to recover Chrome driver: {e}")
                time.sleep(10)
                continue

        cycle_has_error = False

        try:
            for idx, target in enumerate(targets, 1):
                print(f"Checking [{idx}/{len(targets)}]: {target.city or 'Target'} -> {target.slug or target.url}")
                res: CheckResult = checker.check_target(target, driver=_ACTIVE_DRIVER)

                if res.raw_error:
                    logger.warning(f"⚠️ {res.movie_name} ({res.city}): {res.status_text}")
                    # If renderer crashed or disconnected, mark for restart
                    if "timed out" in str(res.raw_error).lower() or "disconnected" in str(res.raw_error).lower():
                        cycle_has_error = True
                else:
                    logger.info(f"[{res.city}] {res.movie_name} -> {res.status_text}")
                    if res.matched_theatres:
                        logger.info(f"🏛️ Matched: {', '.join(res.matched_theatres)}")

                # Check if open
                if res.is_open:
                    now_ts = time.time()
                    last_alert = alerted_targets.get(target.url, 0)

                    # Alert if never alerted or if 10 minutes have passed
                    if (now_ts - last_alert) > 600:
                        alerted_targets[target.url] = now_ts
                        theatre_log = f" at {', '.join(res.matched_theatres)}" if res.matched_theatres else ""

                        logger.info("🚨" * 20)
                        logger.info(f"🎉 BOOKING IS OPEN: {res.movie_name} in {res.city}{theatre_log}! 🎉")
                        logger.info("🚨" * 20)

                        # Write to file
                        with open("booking_opened.txt", "a", encoding="utf-8") as f:
                            f.write(
                                f"[{now_str}] {res.movie_name} ({res.city}){theatre_log}\n"
                                f"URL: {res.booking_url}\n\n"
                            )

                        extra_info = f"Detected at {now_str}"
                        if res.details.get("release_date"):
                            extra_info += f" | Release: {res.details['release_date']}"

                        dispatch_alerts(
                            movie_name=res.movie_name,
                            city=res.city,
                            booking_url=res.booking_url,
                            extra_info=extra_info,
                            theatres=res.matched_theatres
                        )

            # Unload heavy page from Chrome memory while idle
            try:
                _ACTIVE_DRIVER.get("about:blank")
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error in monitor cycle: {e}")
            cycle_has_error = True

        # If a connection or renderer error occurred, recycle driver for safety
        if cycle_has_error:
            logger.info("Recovering Chrome driver after cycle error...")
            cleanup_driver()

        if run_once:
            print("\nCompleted single check cycle. Exiting.")
            cleanup_driver()
            break

        sleep_duration = calculate_sleep_interval(interval)
        logger.info(f"Cycle #{check_cycle} done. Sleeping for {sleep_duration} seconds...")
        try:
            time.sleep(sleep_duration)
        except KeyboardInterrupt:
            cleanup_driver()
            print("\nMonitoring stopped by user.")
            break


def main():
    set_process_efficiency_priority()

    parser = argparse.ArgumentParser(description="BookMyShow Multi-Movie/City Booking Monitor")
    parser.add_argument("--url", nargs="+", help="One or more BookMyShow URLs to monitor")
    parser.add_argument("--interval", type=int, default=config.check_interval, help="Check interval in seconds")
    parser.add_argument("--once", action="store_true", help="Perform a single check and exit")
    parser.add_argument("--test", action="store_true", help="Test alert notifications without monitoring")
    args = parser.parse_args()

    if args.test:
        from test_alerts import run_test
        run_test()
        return

    # Check CLI URLs if provided
    targets = config.targets
    if args.url:
        targets = [TargetItem(url=u) for u in args.url]

    print_banner(targets)
    monitor_loop(targets=targets, interval=args.interval, run_once=args.once)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cleanup_driver()
        print("\nProcess interrupted. Goodbye!")
