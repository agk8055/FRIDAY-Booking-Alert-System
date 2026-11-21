import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from config import TargetItem, TheatreTarget, config

logger = logging.getLogger("BMSMonitor")


@dataclass
class CheckResult:
    is_open: bool
    movie_name: str
    city: str
    status_text: str
    booking_url: str
    theatres_found: List[str] = field(default_factory=list)
    matched_theatres: List[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    raw_error: Optional[str] = None


class BMSChecker:
    def __init__(self, headless: bool = None):
        self.headless = headless if headless is not None else config.headless

    def create_driver(self) -> webdriver.Chrome:
        """Initializes an ultra-lean, high-efficiency headless Chrome webdriver session."""
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")

        # Core optimization flags
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,720")
        options.page_load_strategy = "eager"

        # Aggressive asset & background blocking (saves 85%+ CPU, RAM & network bandwidth)
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-sync")
        options.add_argument("--mute-audio")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-breakpad")
        options.add_argument("--disable-component-update")
        options.add_argument("--disable-domain-reliability")
        options.add_argument("--disable-hang-monitor")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-prompt-on-repost")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-translate")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--safebrowsing-disable-auto-update")

        # Block media, images, and geolocation at the profile level
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.media_stream": 2,
            "profile.default_content_setting_values.geolocation": 2,
        }
        options.add_experimental_option("prefs", prefs)

        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)

        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(20)
        return driver

    def check_target(self, target: TargetItem, driver: webdriver.Chrome) -> CheckResult:
        """Inspects a specific BookMyShow Target URL."""
        try:
            if target.is_cinema:
                return self._check_cinema_page(target, driver)
            return self._check_movie_page(target, driver)
        except Exception as e:
            logger.error(f"Error checking {target.url}: {e}")
            return CheckResult(
                is_open=False,
                movie_name=target.slug or "Unknown",
                city=target.city or "Unknown",
                status_text=f"Check failed: {e}",
                booking_url=target.url,
                raw_error=str(e)
            )

    def _check_cinema_page(self, target: TargetItem, driver: webdriver.Chrome) -> CheckResult:
        driver.get(target.url)
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        cinema_title = driver.title.split("|")[0].strip()
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")

        listed_movies = []
        for a in soup.find_all("a", href=True):
            if "/movies/" in a["href"]:
                txt = a.text.strip()
                if txt and txt not in listed_movies:
                    listed_movies.append(txt)

        return CheckResult(
            is_open=len(listed_movies) > 0,
            movie_name=f"Movies at {cinema_title}",
            city=target.city,
            status_text=f"Listed {len(listed_movies)} movies at {cinema_title}",
            booking_url=driver.current_url,
            theatres_found=[cinema_title],
            details={"listed_movies": listed_movies}
        )

    def _check_movie_page(self, target: TargetItem, driver: webdriver.Chrome) -> CheckResult:
        driver.get(target.url)

        # Wait for the specific target's state to be loaded
        try:
            if target.event_code:
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script(f"""
                        var s = window.__INITIAL_STATE__;
                        return s && s.synopsisStore && s.synopsisStore.synopsis && 
                               s.synopsisStore.synopsis.eventCode === '{target.event_code}';
                    """)
                )
            else:
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("""
                        var s = window.__INITIAL_STATE__;
                        return s && s.synopsisStore && s.synopsisStore.synopsis && s.synopsisStore.synopsis.eventName;
                    """)
                )
        except Exception:
            pass

        state = driver.execute_script("return window.__INITIAL_STATE__;") or {}
        syn = state.get("synopsisStore", {}).get("synopsis", {})
        analytics = syn.get("analytics", {})

        movie_name = syn.get("eventName") or target.slug.replace("-", " ").title()
        release_date = syn.get("releaseDate") or analytics.get("eventReleaseDate", "")
        book_button_state = syn.get("bookButton")
        label = analytics.get("label", "").lower()
        event_code = syn.get("eventCode") or target.event_code

        # City from cookies / state / URL
        city_name = target.city
        cookies = state.get("cookies", {})
        if cookies.get("rgn", {}).get("regionName"):
            city_name = cookies["rgn"]["regionName"]

        # Derive default Showtimes URL
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", movie_name.lower()).strip("-")
        city_slug = re.sub(r"[^a-zA-Z0-9]+", "-", city_name.lower()).strip("-")
        booking_url = f"https://in.bookmyshow.com/buytickets/{slug}-{city_slug}/movie-{city_slug[:3]}-{event_code}-MT/"

        # Check DOM for "Book tickets" button
        dom_book_button_found = False
        try:
            buttons = driver.find_elements(
                By.XPATH,
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'book tickets')]"
            )
            if buttons and any(b.is_displayed() for b in buttons):
                dom_book_button_found = True
        except Exception:
            pass

        general_booking_open = False
        if book_button_state is True or dom_book_button_found:
            general_booking_open = True
        elif label and ("now showing" in label or ("book" in label and "coming soon" not in label)):
            general_booking_open = True

        if not general_booking_open:
            return CheckResult(
                is_open=False,
                movie_name=movie_name,
                city=city_name,
                status_text=f"Bookings NOT open yet in {city_name} (Coming Soon / Interested)",
                booking_url=target.url,
                details={
                    "movie_name": movie_name,
                    "city": city_name,
                    "release_date": release_date,
                    "event_code": event_code
                }
            )

        # General booking IS open!
        theatres_found = []
        matched_theatres = []

        # 1. Direct Cinema Check:
        # If the user provided a direct Cinema URL (e.g. with specific future date like Dec 17 at Forum Mall),
        # check that cinema's schedule directly!
        if target.target_theatres:
            for th_target in target.target_theatres:
                if th_target.url:
                    try:
                        driver.get(th_target.url)
                        WebDriverWait(driver, 8).until(
                            lambda d: d.execute_script("return document.readyState;") in ["interactive", "complete"]
                        )
                        soup_cin = BeautifulSoup(driver.page_source, "html.parser")
                        for a in soup_cin.find_all("a", href=True):
                            href_l = a["href"].lower()
                            txt_l = a.text.strip().lower()
                            # Match by event code, slug, or title
                            if (target.event_code and target.event_code.lower() in href_l) or \
                               (target.slug and target.slug in href_l) or \
                               (movie_name and movie_name.lower() in txt_l) or \
                               ("avengers" in href_l and "doomsday" in href_l):
                                label_name = f"{th_target.name} [{th_target.code}]"
                                if label_name not in matched_theatres:
                                    matched_theatres.append(label_name)
                                break
                    except Exception as e:
                        logger.warning(f"Error checking direct cinema URL {th_target.url}: {e}")

            # If matched directly at the cinema URL, trigger immediately!
            if matched_theatres:
                th_booking_url = target.target_theatres[0].url if target.target_theatres[0].url else booking_url
                status_text = f"🎟️ BOOKINGS OPEN in {city_name} at target theatre: {', '.join(matched_theatres)}!"
                return CheckResult(
                    is_open=True,
                    movie_name=movie_name,
                    city=city_name,
                    status_text=status_text,
                    booking_url=th_booking_url,
                    theatres_found=matched_theatres,
                    matched_theatres=matched_theatres,
                    details={
                        "movie_name": movie_name,
                        "city": city_name,
                        "matched_theatres": matched_theatres,
                        "booking_url": th_booking_url
                    }
                )

            # 2. General Showtimes Check (fallback):
            # Click the on-page 'Book tickets' button on the movie page to inspect currently open venues
            try:
                if driver.current_url != target.url:
                    driver.get(target.url)
                btn = WebDriverWait(driver, 6).until(
                    lambda d: d.find_element(
                        By.XPATH,
                        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'book tickets')]"
                    )
                )
                driver.execute_script("arguments[0].click();", btn)
                WebDriverWait(driver, 5).until(
                    lambda d: len(d.find_elements(By.XPATH, "//a[contains(@href, '/cinemas/') or contains(@href, '/cinema/')]")) > 0
                )
            except Exception:
                pass

            soup = BeautifulSoup(driver.page_source, "html.parser")
            found_cinema_entries = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/cinemas/" in href or "/cinema/" in href:
                    txt = a.text.strip()
                    code = href.strip("/").split("/")[-1].upper()
                    found_cinema_entries.append({"url": href, "text": txt, "code": code})
                    if txt and txt not in theatres_found:
                        theatres_found.append(f"{txt} ({code})")

            # Match against user's target theatre URLs / codes
            for th_target in target.target_theatres:
                for entry in found_cinema_entries:
                    if th_target.code and th_target.code == entry["code"]:
                        venue_display = entry["text"] or th_target.name
                        label_name = f"{venue_display} [{entry['code']}]"
                        if label_name not in matched_theatres:
                            matched_theatres.append(label_name)
                        break
                    
                    if th_target.slug and th_target.slug in entry["url"].lower():
                        venue_display = entry["text"] or th_target.name
                        label_name = f"{venue_display} [{entry['code']}]"
                        if label_name not in matched_theatres:
                            matched_theatres.append(label_name)
                        break

                    if th_target.name and th_target.name.lower() in entry["text"].lower():
                        label_name = f"{entry['text']} [{entry['code']}]"
                        if label_name not in matched_theatres:
                            matched_theatres.append(label_name)
                        break

            if matched_theatres:
                status_text = f"🎟️ BOOKINGS OPEN in {city_name} at target theatre: {', '.join(matched_theatres)}!"
                return CheckResult(
                    is_open=True,
                    movie_name=movie_name,
                    city=city_name,
                    status_text=status_text,
                    booking_url=booking_url,
                    theatres_found=theatres_found,
                    matched_theatres=matched_theatres,
                    details={
                        "movie_name": movie_name,
                        "city": city_name,
                        "matched_theatres": matched_theatres,
                        "booking_url": booking_url
                    }
                )
            else:
                status_text = (
                    f"Booking open in {city_name}, but waiting for {target.theatres_display} "
                    f"(currently listed: {', '.join(theatres_found[:3]) if theatres_found else 'none yet'})"
                )
                return CheckResult(
                    is_open=False,
                    movie_name=movie_name,
                    city=city_name,
                    status_text=status_text,
                    booking_url=booking_url,
                    theatres_found=theatres_found,
                    matched_theatres=[],
                    details={"target_theatres": target.theatres_display, "city": city_name}
                )

        # No theatre filter -> Alert immediately
        return CheckResult(
            is_open=True,
            movie_name=movie_name,
            city=city_name,
            status_text=f"🎟️ BOOKINGS ARE OPEN in {city_name}!",
            booking_url=booking_url,
            details={
                "movie_name": movie_name,
                "city": city_name,
                "release_date": release_date,
                "booking_url": booking_url
            }
        )
