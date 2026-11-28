#!/usr/bin/env python3
"""
BookMyShow Booking Alert & Monitoring System (FRIDAY)
Core Monitoring Loop & Background Orchestrator.
"""

import argparse
import atexit
import datetime
import logging
import os
import random
import signal
import sys
import threading
import time
from typing import Dict, List, Optional

try:
    from .config import TargetItem, config, load_targets_from_file_or_env, BOOKINGS_LOG_FILE, MONITOR_LOG_FILE
    from .checker import BMSChecker, CheckResult
    from .notifier import dispatch_alerts
except ImportError:
    from config import TargetItem, config, load_targets_from_file_or_env, BOOKINGS_LOG_FILE, MONITOR_LOG_FILE
    from checker import BMSChecker, CheckResult
    from notifier import dispatch_alerts

# Configure logging to console and to logs/monitor.log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(MONITOR_LOG_FILE, encoding="utf-8")
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
    """Calculates sleep time with adaptive night polling and jitter to avoid robotic request cadences."""
    effective = base_interval
    if config.adaptive_polling:
        now_hour = datetime.datetime.now().hour
        if 0 <= now_hour < 7:
            effective = max(base_interval * 4, 300)
            logger.info(f"🌙 Off-peak night hours (00:00 - 07:00): Adaptive interval set to {effective}s.")
    jitter = random.randint(-min(10, max(1, effective // 6)), min(15, max(2, effective // 4)))
    return max(30, effective + jitter)


def print_banner(targets: List[TargetItem]):
    print("=" * 68)
    print("🎬  BookMyShow Multi-Movie & Multi-City Booking Alert System (FRIDAY)")
    print("=" * 68)
    print(f"📋 Active Targets ({len(targets)} total):")
    for idx, t in enumerate(targets, 1):
        th_info = f" | 🏛️ {t.theatres_display}" if t.target_theatres else " | 🏛️ ANY Theatre"
        city_info = f" | 📍 {t.city}" if t.city else ""
        date_info = f" | 📅 {t.target_date}" if t.target_date else ""
        print(f"  {idx}. [{t.slug or 'Direct URL'}]{city_info}{date_info}{th_info}")
        print(f"     🔗 {t.url}")
    print("-" * 68)
    print(f"⚡ Primary Engine: curl_cffi (Fast TLS) | 🛡️ Fallback: Headless Chrome")
    print(f"⏱️ Check Interval: {config.check_interval}s | 🔄 Browser Recycling: every {config.recycle_browser_cycles} cycles")
    print(f"🌱 Eco Priority: {'ON' if config.eco_priority else 'OFF'} | 🌙 Adaptive Polling: {'ON' if config.adaptive_polling else 'OFF'}")
    print(f"🔊 Audio: {'ON' if config.sound_alert else 'OFF'} | 💻 Toast: {'ON' if config.windows_toast else 'OFF'} | 🌐 Auto-Browser: {'ON' if config.open_browser_on_open else 'OFF'}")
    print(f"📱 Telegram: {'ON' if config.telegram_enabled else 'OFF'} | 📧 Email: {'ON' if config.email_enabled else 'OFF'} | 💬 Discord: {'ON' if config.discord_enabled else 'OFF'}")
    print("=" * 68)
    print("Press Ctrl+C at any time to stop monitoring.\n")


def monitor_loop(
    targets: List[TargetItem],
    interval: int,
    run_once: bool = False,
    pause_event: Optional[threading.Event] = None,
    wake_event: Optional[threading.Event] = None,
    reload_from_file: bool = False,
):
    global _ACTIVE_DRIVER
    checker = BMSChecker()
    check_cycle = 0

    # Track which targets have already alerted to prevent repeated alert loops
    alerted_targets: Dict[str, float] = {}

    def get_fallback_driver():
        global _ACTIVE_DRIVER
        if _ACTIVE_DRIVER is None:
            logger.info("🌐 Launching headless Chrome fallback session...")
            _ACTIVE_DRIVER = checker.create_driver()
        return _ACTIVE_DRIVER

    while True:
        if pause_event is not None and pause_event.is_set():
            logger.info("⏸️ Monitor paused from dashboard. Waiting to resume...")
            while pause_event.is_set():
                if wake_event is not None:
                    wake_event.wait(timeout=1.0)
                    wake_event.clear()
                else:
                    time.sleep(1.0)
            logger.info("▶️ Monitor resumed from dashboard.")

        if reload_from_file:
            targets = load_targets_from_file_or_env()
            interval = config.reload_check_interval()

        check_cycle += 1
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now_str}] --- Cycle #{check_cycle}: Checking {len(targets)} Target(s) ---")

        if not targets:
            logger.info("⏸️ No active targets enabled. Skipping check cycle.")
            if run_once:
                print("\nCompleted single check cycle. Exiting.")
                cleanup_driver()
                break
            sleep_duration = calculate_sleep_interval(interval)
            logger.info(f"Cycle #{check_cycle} done. Sleeping for {sleep_duration} seconds...")
            try:
                if wake_event is not None:
                    if wake_event.wait(timeout=sleep_duration):
                        wake_event.clear()
                else:
                    time.sleep(sleep_duration)
            except KeyboardInterrupt:
                cleanup_driver()
                print("\nMonitoring stopped by user.")
                break
            continue

        # Periodically recycle active browser session to release accumulated memory if Chrome was used
        if _ACTIVE_DRIVER is not None and check_cycle > 1 and (check_cycle % config.recycle_browser_cycles == 0):
            logger.info(f"♻️ Recycling active Chrome session (Cycle #{check_cycle}) to maintain clean memory...")
            cleanup_driver()

        cycle_has_error = False

        try:
            for idx, target in enumerate(targets, 1):
                print(f"Checking [{idx}/{len(targets)}]: {target.city or 'Target'} -> {target.slug or target.url}")
                res: CheckResult = checker.check_target(
                    target,
                    driver=_ACTIVE_DRIVER,
                    driver_factory=get_fallback_driver
                )

                if res.raw_error:
                    logger.warning(f"⚠️ {res.movie_name} ({res.city}): {res.status_text}")
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

                    if (now_ts - last_alert) > 600:
                        alerted_targets[target.url] = now_ts
                        theatre_log = f" at {', '.join(res.matched_theatres)}" if res.matched_theatres else ""

                        logger.info("🚨" * 20)
                        logger.info(f"🎉 BOOKING IS OPEN: {res.movie_name} in {res.city}{theatre_log}! 🎉")
                        logger.info("🚨" * 20)

                        # Write to logs/booking_opened.txt
                        with open(BOOKINGS_LOG_FILE, "a", encoding="utf-8") as f:
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

            # Unload heavy page from Chrome memory while idle if browser was active
            if _ACTIVE_DRIVER is not None:
                try:
                    _ACTIVE_DRIVER.get("about:blank")
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error in monitor cycle: {e}")
            cycle_has_error = True

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
            if wake_event is not None:
                if wake_event.wait(timeout=sleep_duration):
                    wake_event.clear()
            else:
                time.sleep(sleep_duration)
        except KeyboardInterrupt:
            cleanup_driver()
            print("\nMonitoring stopped by user.")
            break


def main():
    set_process_efficiency_priority()

    default_dashboard_port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8765")))
    default_dashboard_host = os.getenv("DASHBOARD_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")

    parser = argparse.ArgumentParser(description="BookMyShow Multi-Movie/City Booking Monitor")
    parser.add_argument("--url", nargs="+", help="One or more BookMyShow URLs to monitor")
    parser.add_argument("--interval", type=int, default=config.check_interval, help="Check interval in seconds")
    parser.add_argument("--once", action="store_true", help="Perform a single check and exit")
    parser.add_argument("--test", action="store_true", help="Test alert notifications without monitoring")
    parser.add_argument("--no-dashboard", action="store_true", help="Do not start the local web dashboard")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the dashboard in browser")
    parser.add_argument("--dashboard-host", default=default_dashboard_host, help=f"Dashboard bind host (default: {default_dashboard_host})")
    parser.add_argument("--dashboard-port", type=int, default=default_dashboard_port, help=f"Dashboard port (default: {default_dashboard_port})")
    args = parser.parse_args()

    if args.test:
        # Run test alert
        target = config.targets[0] if config.targets else None
        city = target.city if target else "Coimbatore"
        theatres = [th.name for th in target.target_theatres] if target and target.target_theatres else ["Broadway Cinemas"]
        dispatch_alerts(
            movie_name="Dune: Part Three (TEST SIMULATION)",
            city=city,
            booking_url=target.url if target else "https://in.bookmyshow.com",
            extra_info="Simulation test of FRIDAY alert dispatchers.",
            theatres=theatres
        )
        return

    targets = config.targets
    reload_from_file = True
    if args.url:
        targets = [TargetItem(url=u) for u in args.url]
        reload_from_file = False

    pause_event: Optional[threading.Event] = None
    wake_event: Optional[threading.Event] = None
    dashboard_server = None

    if not args.once and not args.no_dashboard:
        try:
            try:
                from .dashboard import start_dashboard_background
            except ImportError:
                from dashboard import start_dashboard_background

            pause_event = threading.Event()
            wake_event = threading.Event()
            open_browser = not args.no_browser and not os.getenv("RENDER") and not os.getenv("PORT")
            dashboard_server = start_dashboard_background(
                host=args.dashboard_host,
                port=args.dashboard_port,
                open_browser=open_browser,
                pause_event=pause_event,
                wake_event=wake_event,
            )
            if dashboard_server is not None:
                logger.info(f"🌐 Dashboard live at http://{args.dashboard_host}:{args.dashboard_port}")
            else:
                logger.info(f"Notice: Dashboard port {args.dashboard_port} already in use; continuing monitor only.")
        except Exception as exc:
            logger.warning(f"Notice: Could not start dashboard server: {exc}")

    print_banner(targets)
    try:
        monitor_loop(
            targets=targets,
            interval=args.interval,
            run_once=args.once,
            pause_event=pause_event,
            wake_event=wake_event,
            reload_from_file=reload_from_file,
        )
    finally:
        if dashboard_server is not None:
            try:
                dashboard_server.shutdown()
                dashboard_server.server_close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cleanup_driver()
        print("\nProcess interrupted. Goodbye!")
