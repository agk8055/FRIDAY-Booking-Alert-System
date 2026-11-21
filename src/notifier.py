import base64
import logging
import subprocess
import threading
import time
import webbrowser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from typing import List, Optional
import requests

try:
    from .config import config
except ImportError:
    from config import config

logger = logging.getLogger("BMSMonitor")


def play_sound_alert(repeat_count: int = 5):
    """Plays an audible alarm through system speakers on Windows."""
    def _beep():
        try:
            import winsound
            for _ in range(repeat_count):
                for freq in [1200, 1500, 1800, 2200]:
                    winsound.Beep(freq, 150)
                time.sleep(0.2)
        except Exception as e:
            logger.error(f"Error playing sound: {e}")

    thread = threading.Thread(target=_beep, daemon=True)
    thread.start()


def send_windows_toast(title: str, message: str):
    """Displays a native Windows Desktop Toast Notification."""
    try:
        safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
        safe_msg = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")

        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = '<toast><visual><binding template="ToastGeneric"><text>{safe_title}</text><text>{safe_msg}</text></binding></visual></toast>'
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('BookMyShow Alert').Show($toast)
"""
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            logger.info("Windows desktop toast notification sent.")
        else:
            logger.warning(f"Windows toast warning: {res.stderr.strip()}")
    except Exception as e:
        logger.error(f"Failed to display Windows toast: {e}")


def open_in_browser(url: str):
    """Automatically opens the booking URL in user's default browser."""
    try:
        webbrowser.open(url)
        logger.info(f"Opened URL in default browser: {url}")
    except Exception as e:
        logger.error(f"Failed to open browser: {e}")


def send_telegram(message: str, bot_token: str, chat_id: str):
    """Sends a Telegram notification to the given chat ID with retry."""
    if not bot_token or not chat_id:
        logger.warning("Telegram enabled but bot_token or chat_id is missing.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    session = requests.Session()
    for attempt in range(1, 4):
        try:
            response = session.post(url, json=payload, timeout=12)
            if response.status_code == 200:
                logger.info("Telegram notification sent successfully.")
                return True
            else:
                logger.error(f"Telegram error {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.warning(f"Telegram send attempt {attempt} failed: {e}")
            time.sleep(1)
            
    logger.error("Failed to send Telegram message after 3 attempts.")
    return False


def send_discord(webhook_url: str, title: str, description: str, url: str):
    """Sends a formatted embed notification to a Discord webhook."""
    if not webhook_url:
        logger.warning("Discord enabled but webhook_url is missing.")
        return False

    payload = {
        "content": "@everyone 🚨 **BOOKING IS OPEN!** 🚨",
        "embeds": [
            {
                "title": title,
                "url": url,
                "description": description,
                "color": 15158332,
                "footer": {"text": "BookMyShow Booking Alert System"}
            }
        ]
    }
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code in [200, 204]:
            logger.info("Discord notification sent successfully.")
            return True
        else:
            logger.error(f"Discord error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Discord webhook: {e}")
        return False


def send_email(subject: str, body: str, config_obj):
    """Sends an email notification via SMTP."""
    if not config_obj.smtp_user or not config_obj.email_recipient:
        logger.warning("Email enabled but sender/recipient not configured.")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = config_obj.smtp_user
        msg["To"] = config_obj.email_recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(config_obj.smtp_server, config_obj.smtp_port)
        server.starttls()
        server.login(config_obj.smtp_user, config_obj.smtp_password)
        server.send_message(msg)
        server.quit()
        logger.info(f"Email sent successfully to {config_obj.email_recipient}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_generic_webhook(webhook_url: str, payload: dict):
    """Posts JSON payload to a generic webhook endpoint."""
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        logger.info(f"Custom webhook called, response status: {response.status_code}")
        return response.status_code in [200, 201, 202, 204]
    except Exception as e:
        logger.error(f"Failed to send custom webhook: {e}")
        return False


def dispatch_alerts(
    movie_name: str,
    city: str,
    booking_url: str,
    extra_info: str = "",
    theatres: Optional[List[str]] = None
):
    """Dispatches notifications across all enabled channels with city and theatre details."""
    theatre_str = f" at {', '.join(theatres)}" if theatres else ""
    city_str = f" ({city})" if city else ""
    title = f"🎟️ BOOKING OPEN: {movie_name}{theatre_str}{city_str}!"

    logger.info("==========================================")
    logger.info(f"🔔 TRIGGERING ALERTS: {movie_name} in {city}{theatre_str}")
    logger.info("==========================================")

    # 1. Desktop Sound Alert
    if config.sound_alert:
        play_sound_alert(repeat_count=6)

    # 2. Windows Toast Notification
    if config.windows_toast:
        send_windows_toast(
            title=f"BookMyShow: {movie_name} ({city})",
            message=f"Bookings OPEN{theatre_str}! Click to book: {booking_url}"
        )

    # 3. Open in Browser
    if config.open_browser_on_open:
        open_in_browser(booking_url)

    # 4. Telegram Alert
    if config.telegram_enabled:
        theatre_html = f"\n🏛️ <b>Theatre:</b> {', '.join(theatres)}" if theatres else ""
        city_html = f"\n📍 <b>City:</b> {city}" if city else ""
        tg_text = (
            f"🚨 <b>BOOKING IS OPEN!</b> 🚨\n\n"
            f"🎬 <b>Movie:</b> {movie_name}{city_html}{theatre_html}\n"
            f"🎟️ <b>Book Now:</b> <a href=\"{booking_url}\">Click here to Book Tickets</a>\n"
        )
        if extra_info:
            tg_text += f"\nℹ️ <i>{extra_info}</i>"
        send_telegram(tg_text, config.telegram_bot_token, config.telegram_chat_id)

    # 5. Discord Alert
    if config.discord_enabled:
        send_discord(
            webhook_url=config.discord_webhook_url,
            title=f"🎟️ Bookings OPEN: {movie_name}{theatre_str}{city_str}",
            description=f"Tickets are now available! [Click here to book on BookMyShow]({booking_url})\n\n{extra_info}",
            url=booking_url
        )

    # 6. Email Alert
    if config.email_enabled:
        email_body = f"""
        <h2>🎟️ Bookings are OPEN for {movie_name}!</h2>
        <p>City: {city}</p>
        {'<p>Theatre: ' + ', '.join(theatres) + '</p>' if theatres else ''}
        <p>Book tickets directly here: <a href="{booking_url}">{booking_url}</a></p>
        <p>{extra_info}</p>
        """
        send_email(
            subject=f"🚨 BookMyShow Alert: {movie_name} in {city} is OPEN!",
            body=email_body,
            config_obj=config
        )

    # 7. Generic Webhook
    if config.webhook_enabled and config.webhook_url:
        send_generic_webhook(config.webhook_url, {
            "event": "booking_open",
            "movie": movie_name,
            "city": city,
            "theatres": theatres or [],
            "url": booking_url,
            "extra_info": extra_info
        })
