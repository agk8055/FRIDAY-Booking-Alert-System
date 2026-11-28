import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    curl_requests = None

try:
    from .config import TargetItem, TheatreTarget, config
except ImportError:
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
    _DEFAULT_IMPERSONATE = "chrome152"
    _IMPERSONATE_POOL = ("chrome152", "safari180", "firefox135", "chrome146", "chrome136")
    _CURL_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://in.bookmyshow.com/",
        "Upgrade-Insecure-Requests": "1",
    }

    @classmethod
    def _resolve_impersonate(cls) -> str:
        return os.getenv("CURL_IMPERSONATE", cls._DEFAULT_IMPERSONATE).strip() or cls._DEFAULT_IMPERSONATE

    @staticmethod
    def _derive_chrome_user_agent(impersonate: str) -> str:
        match = re.search(r"chrome(\d+)", impersonate or "")
        major = match.group(1) if match else "152"
        return (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        )

    def __init__(self, headless: bool = None):
        self.headless = headless if headless is not None else config.headless
        self._curl_session = None
        self._event_group_cache: dict[str, str] = {}
        self._last_curl_reason: str = ""
        self._curl_consecutive_failures: int = 0
        self._curl_cooldown_remaining: int = 0
        self._curl_enabled: bool = os.getenv("USE_CURL_CFFI", "true").strip().lower() in ("1", "true", "yes", "on")
        self._synced_user_agent: str = ""
        self._impersonate_idx: int = 0
        self._proxy_url: str = os.getenv("PROXY_URL", "").strip()

    def _current_impersonate(self) -> str:
        primary = self._resolve_impersonate()
        if self._impersonate_idx == 0:
            return primary
        pool = [primary] + [p for p in self._IMPERSONATE_POOL if p != primary]
        return pool[self._impersonate_idx % len(pool)]

    def _rotate_curl_session(self) -> None:
        """Drops the current curl_cffi session and rotates to the next browser TLS profile."""
        if self._curl_session is not None:
            try:
                self._curl_session.close()
            except Exception:
                pass
        self._curl_session = None
        self._synced_user_agent = ""
        self._impersonate_idx += 1

    def _get_curl_headers(self) -> dict:
        headers = dict(self._CURL_HEADERS)
        if self._synced_user_agent:
            headers["User-Agent"] = self._synced_user_agent
        return headers

    def _get_curl_session(self):
        if not CURL_CFFI_AVAILABLE or not self._curl_enabled:
            return None
        if self._curl_session is None:
            impersonate = self._current_impersonate()
            kwargs = {"impersonate": impersonate}
            if self._proxy_url:
                kwargs["proxies"] = {"http": self._proxy_url, "https": self._proxy_url}
            self._curl_session = curl_requests.Session(**kwargs)
        return self._curl_session

    def _sync_cookies_from_driver(self, driver: webdriver.Chrome) -> None:
        """Copies cookies and User-Agent from Chrome into the curl_cffi session."""
        if not self._curl_enabled or driver is None:
            return
        session = self._get_curl_session()
        if session is None:
            return
        try:
            ua = driver.execute_script("return navigator.userAgent;")
            if isinstance(ua, str) and ua.strip():
                self._synced_user_agent = ua.strip()
        except Exception as e:
            logger.debug(f"Could not sync User-Agent from Chrome to curl_cffi: {e}")
        try:
            for cookie in driver.get_cookies():
                name = cookie.get("name")
                value = cookie.get("value")
                domain = cookie.get("domain") or "in.bookmyshow.com"
                path = cookie.get("path") or "/"
                if name and value is not None:
                    session.cookies.set(name, value, domain=domain, path=path)
        except Exception as e:
            logger.debug(f"Could not sync cookies from Chrome to curl_cffi: {e}")

    def _extract_initial_state_from_html(self, html: str) -> dict:
        """Extracts window.__INITIAL_STATE__ JSON directly from HTML without launching a browser."""
        idx = html.find("window.__INITIAL_STATE__")
        if idx != -1:
            start = html.find("{", idx)
            end = html.find("</script>", start)
            if start != -1 and end != -1:
                raw = html[start:end].rstrip().rstrip(";")
                try:
                    return json.loads(raw)
                except Exception:
                    pass

        # Fallback regex if script tag format differs
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})(?:;\s*|(?=</script>))', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).rstrip(";"))
            except Exception:
                pass
        return {}

    def _check_cinema_page_curl(self, target: TargetItem, session) -> Optional[CheckResult]:
        try:
            resp = session.get(target.url, headers=self._get_curl_headers(), timeout=10)
            if resp.status_code != 200:
                self._last_curl_reason = f"HTTP {resp.status_code}"
                return None
            soup = BeautifulSoup(resp.text, "html.parser")
            cinema_title = soup.title.text.split("|")[0].strip() if soup.title else target.slug or "Cinema"
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
                status_text=f"Listed {len(listed_movies)} movies at {cinema_title} (⚡ curl_cffi)",
                booking_url=target.url,
                theatres_found=[cinema_title],
                details={"listed_movies": listed_movies, "engine": "curl_cffi"}
            )
        except Exception as e:
            self._last_curl_reason = str(e)
            return None

    def _derive_movie_name(self, target: TargetItem) -> str:
        return target.slug.replace("-", " ").title() if target.slug else (target.event_code or "Target Movie")

    def _is_redirected_away_from_date(self, final_url: str, target_date: str) -> bool:
        """Returns True if BookMyShow redirected a dated cinema URL to a different date."""
        if not target_date or not final_url:
            return False
        m = re.search(r"/(\d{8})(?:[/?#]|$)", final_url)
        if m and m.group(1) != target_date:
            return True
        return False

    def _match_movie_in_state(
        self, state: dict, target: TargetItem, fallback_name: str, target_date: str = ""
    ) -> Optional[str]:
        """Searches venue showtime sections of window.__INITIAL_STATE__ for the target movie."""
        if not state or not isinstance(state, (dict, list)):
            return None

        # Prefer authoritative venue showtime stores; never scan seo/footer/header/sidebar
        ignored_top_keys = {"seo", "headerApi", "sidebarApi", "regions", "cookies", "config", "appConfig", "appPromotionReducer", "youtube"}
        search_roots = []
        if isinstance(state, dict) and ("venueShowtimesFunctionalApi" in state or "venueShowtimesNew" in state):
            vfa = state.get("venueShowtimesFunctionalApi", {})
            queries = vfa.get("queries", {}) if isinstance(vfa, dict) else {}
            for qk, qv in queries.items():
                if target_date and isinstance(qv, dict):
                    arg_date = qv.get("originalArgs", {}).get("dateCode", "")
                    if arg_date and arg_date != target_date:
                        continue
                search_roots.append(qv)
            if not search_roots and "venueShowtimesNew" in state:
                search_roots.append(state["venueShowtimesNew"])
        elif isinstance(state, dict):
            search_roots = [v for k, v in state.items() if k not in ignored_top_keys]
        else:
            search_roots = [state]

        ev_code_lower = target.event_code.lower() if target.event_code else ""
        eg_code_lower = target.event_group_code.lower() if target.event_group_code else ""
        slug_lower = target.slug.lower() if target.slug else ""
        stack = list(search_roots)

        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                # Check if a dict key itself is the event code (e.g. showtimesByEvent["ET00491771"])
                if ev_code_lower:
                    for k, v in node.items():
                        if isinstance(k, str) and k.lower() == ev_code_lower:
                            if isinstance(v, dict):
                                return v.get("eventName") or v.get("EventTitle") or v.get("EventName") or fallback_name
                            return fallback_name

                node_code = node.get("eventCode") or node.get("EventCode") or ""
                if ev_code_lower and isinstance(node_code, str) and node_code.lower() == ev_code_lower:
                    return node.get("eventName") or node.get("EventTitle") or node.get("EventName") or fallback_name

                node_group = (
                    node.get("eventGroup")
                    or node.get("EventGroup")
                    or node.get("eventGroupCode")
                    or node.get("EventGroupCode")
                    or ""
                )
                if eg_code_lower and isinstance(node_group, str) and node_group.lower() == eg_code_lower:
                    return node.get("EventName") or node.get("eventName") or node.get("EventTitle") or fallback_name

                # Only fall back to slug matching when no event_code was specified on the target
                if not ev_code_lower and slug_lower:
                    node_slug = node.get("slug") or node.get("eventSlug") or node.get("EventURL") or ""
                    if isinstance(node_slug, str) and slug_lower in node_slug.lower():
                        return node.get("eventName") or node.get("EventTitle") or node.get("EventName") or fallback_name

                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        stack.append(item)
        return None

    @staticmethod
    def _has_authoritative_venue_showtimes(state: dict, target_date: str = "") -> bool:
        """Returns True only when venueShowtimesFunctionalApi contains populated query data."""
        if not isinstance(state, dict) or "venueShowtimesFunctionalApi" not in state:
            return False
        vfa = state.get("venueShowtimesFunctionalApi")
        queries = vfa.get("queries", {}) if isinstance(vfa, dict) else {}
        if not isinstance(queries, dict) or not queries:
            return False
        for qv in queries.values():
            if not isinstance(qv, dict):
                continue
            if target_date:
                arg_date = qv.get("originalArgs", {}).get("dateCode", "")
                if arg_date and arg_date != target_date:
                    continue
            if qv.get("data"):
                return True
        return False

    def _match_movie_in_cinema_page(
        self, html: str, state: dict, target: TargetItem, fallback_name: str, target_date: str = ""
    ) -> Optional[str]:
        """Matches the target movie in a cinema page using venue showtime JSON and non-footer HTML links."""
        matched_name = self._match_movie_in_state(state, target, fallback_name, target_date=target_date)
        if matched_name:
            return matched_name

        # If authoritative venue showtimes API data is already populated, don't fall back to <a> scraping
        if self._has_authoritative_venue_showtimes(state, target_date=target_date):
            return None

        soup_cin = BeautifulSoup(html, "html.parser")
        for chrome_tag in soup_cin.find_all(["header", "footer", "nav"]):
            chrome_tag.decompose()

        ev_code_lower = target.event_code.lower() if target.event_code else ""
        slug_lower = target.slug.lower() if target.slug else ""

        for a in soup_cin.find_all("a", href=True):
            href_l = a["href"].lower()
            txt = a.text.strip()
            if ev_code_lower:
                if ev_code_lower in href_l:
                    return txt or fallback_name
            elif slug_lower and slug_lower in href_l:
                return txt or fallback_name

        return None

    def _resolve_event_group_curl(self, target: TargetItem, session, headers: dict) -> str:
        """Resolves and caches BookMyShow's unique EventGroup ID (EG...) from the movie page."""
        cache_key = target.event_code or target.url
        if target.event_group_code:
            return target.event_group_code
        if cache_key in self._event_group_cache:
            target.event_group_code = self._event_group_cache[cache_key]
            return target.event_group_code
        if not target.url or target.is_cinema:
            return ""
        try:
            resp = session.get(target.url, headers=headers, timeout=10)
            if resp.status_code == 200:
                state = self._extract_initial_state_from_html(resp.text)
                eg = state.get("synopsisStore", {}).get("synopsis", {}).get("eventGroupCode", "")
                if isinstance(eg, str) and eg:
                    self._event_group_cache[cache_key] = eg
                    target.event_group_code = eg
                    logger.debug(f"Resolved EventGroup {eg} for {target.event_code or target.url}")
                    return eg
        except Exception as e:
            logger.debug(f"Could not resolve EventGroup for {target.url}: {e}")
        return ""

    def _check_target_theatres_curl(self, target: TargetItem, session) -> Optional[CheckResult]:
        """Checks target theatre URLs directly via curl_cffi, bypassing the movie page gate."""
        headers = self._get_curl_headers()
        cache_key = target.event_code or target.url
        if not target.event_group_code and cache_key in self._event_group_cache:
            target.event_group_code = self._event_group_cache[cache_key]

        fallback_name = self._derive_movie_name(target)
        matched_theatres = []
        matched_url = ""
        matched_movie_name = fallback_name
        first_theatre_url = next((th.url for th in target.target_theatres if th.url), target.url)

        for th_target in target.target_theatres:
            if not th_target.url:
                continue
            resp = session.get(th_target.url, headers=headers, timeout=10)
            if resp.status_code != 200:
                self._last_curl_reason = f"HTTP {resp.status_code} on {th_target.code or th_target.url}"
                logger.debug(f"curl_cffi returned HTTP {resp.status_code} for theatre URL {th_target.url}")
                return None

            final_url = str(getattr(resp, "url", "") or th_target.url)
            if self._is_redirected_away_from_date(final_url, th_target.date):
                logger.debug(f"Theatre URL {th_target.url} redirected to {final_url} (date {th_target.date} not listed yet)")
                continue

            state = self._extract_initial_state_from_html(resp.text)
            found_name = self._match_movie_in_cinema_page(
                resp.text, state, target, fallback_name, target_date=th_target.date
            )
            if not found_name and not target.event_group_code and isinstance(state, dict) and (
                "venueShowtimesFunctionalApi" in state or "venueShowtimesNew" in state
            ):
                if self._resolve_event_group_curl(target, session, headers):
                    found_name = self._match_movie_in_cinema_page(
                        resp.text, state, target, fallback_name, target_date=th_target.date
                    )
            if found_name:
                matched_movie_name = found_name
                label_name = f"{th_target.name} [{th_target.code}]"
                if label_name not in matched_theatres:
                    matched_theatres.append(label_name)
                if not matched_url:
                    matched_url = th_target.url

        if matched_theatres:
            return CheckResult(
                is_open=True,
                movie_name=matched_movie_name,
                city=target.city,
                status_text=f"🎟️ BOOKINGS OPEN in {target.city} at target theatre: {', '.join(matched_theatres)}! [⚡ curl_cffi]",
                booking_url=matched_url,
                theatres_found=matched_theatres,
                matched_theatres=matched_theatres,
                details={
                    "movie_name": matched_movie_name,
                    "city": target.city,
                    "matched_theatres": matched_theatres,
                    "booking_url": matched_url,
                    "engine": "curl_cffi"
                }
            )

        return CheckResult(
            is_open=False,
            movie_name=fallback_name,
            city=target.city,
            status_text=f"Waiting for {fallback_name} at {target.theatres_display} [⚡ curl_cffi]",
            booking_url=first_theatre_url,
            theatres_found=[],
            matched_theatres=[],
            details={
                "target_theatres": target.theatres_display,
                "city": target.city,
                "engine": "curl_cffi"
            }
        )

    def _check_movie_page_curl(self, target: TargetItem, session) -> Optional[CheckResult]:
        if target.has_theatre_urls:
            return self._check_target_theatres_curl(target, session)

        headers = self._get_curl_headers()
        try:
            resp = session.get(target.url, headers=headers, timeout=10)
            if resp.status_code != 200:
                self._last_curl_reason = f"HTTP {resp.status_code}"
                logger.debug(f"curl_cffi returned HTTP {resp.status_code} for {target.url}")
                return None

            html = resp.text
            state = self._extract_initial_state_from_html(html)
            syn = state.get("synopsisStore", {}).get("synopsis", {})
            analytics = syn.get("analytics", {})

            movie_name = syn.get("eventName")
            if not movie_name:
                self._last_curl_reason = "no __INITIAL_STATE__/eventName (likely JS challenge)"
                return None

            release_date = syn.get("releaseDate") or analytics.get("eventReleaseDate", "")
            book_button_state = syn.get("bookButton")
            label = analytics.get("label", "").lower()
            event_code = syn.get("eventCode") or target.event_code

            city_name = target.city
            cookies = state.get("cookies", {})
            if cookies.get("rgn", {}).get("regionName"):
                city_name = cookies["rgn"]["regionName"]

            slug = re.sub(r"[^a-zA-Z0-9]+", "-", movie_name.lower()).strip("-")
            city_slug = re.sub(r"[^a-zA-Z0-9]+", "-", city_name.lower()).strip("-")
            booking_url = f"https://in.bookmyshow.com/buytickets/{slug}-{city_slug}/movie-{city_slug[:3]}-{event_code}-MT/"

            dom_book_button_found = "book tickets" in html.lower()
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
                    status_text=f"Bookings NOT open yet in {city_name} (Coming Soon / Interested) [⚡ curl_cffi]",
                    booking_url=target.url,
                    details={
                        "movie_name": movie_name,
                        "city": city_name,
                        "release_date": release_date,
                        "event_code": event_code,
                        "engine": "curl_cffi"
                    }
                )

            theatres_found = []
            matched_theatres = []

            if target.target_theatres:
                for th_target in target.target_theatres:
                    if th_target.url:
                        try:
                            th_resp = session.get(th_target.url, headers=headers, timeout=8)
                            if th_resp.status_code == 200:
                                soup_cin = BeautifulSoup(th_resp.text, "html.parser")
                                for a in soup_cin.find_all("a", href=True):
                                    href_l = a["href"].lower()
                                    txt_l = a.text.strip().lower()
                                    if (target.event_code and target.event_code.lower() in href_l) or \
                                       (target.slug and target.slug in href_l) or \
                                       (movie_name and movie_name.lower() in txt_l):
                                        label_name = f"{th_target.name} [{th_target.code}]"
                                        if label_name not in matched_theatres:
                                            matched_theatres.append(label_name)
                                        break
                        except Exception:
                            pass

                if matched_theatres:
                    th_booking_url = target.target_theatres[0].url if target.target_theatres[0].url else booking_url
                    return CheckResult(
                        is_open=True,
                        movie_name=movie_name,
                        city=city_name,
                        status_text=f"🎟️ BOOKINGS OPEN in {city_name} at target theatre: {', '.join(matched_theatres)}! [⚡ curl_cffi]",
                        booking_url=th_booking_url,
                        theatres_found=matched_theatres,
                        matched_theatres=matched_theatres,
                        details={
                            "movie_name": movie_name,
                            "city": city_name,
                            "matched_theatres": matched_theatres,
                            "booking_url": th_booking_url,
                            "engine": "curl_cffi"
                        }
                    )

                try:
                    bt_resp = session.get(booking_url, headers=headers, timeout=8)
                    if bt_resp.status_code == 200:
                        soup_bt = BeautifulSoup(bt_resp.text, "html.parser")
                        found_cinema_entries = []
                        for a in soup_bt.find_all("a", href=True):
                            href = a["href"]
                            if "/cinemas/" in href or "/cinema/" in href:
                                txt = a.text.strip()
                                code = href.strip("/").split("/")[-1].upper()
                                found_cinema_entries.append({"url": href, "text": txt, "code": code})
                                if txt and txt not in theatres_found:
                                    theatres_found.append(f"{txt} ({code})")

                        for th_target in target.target_theatres:
                            for entry in found_cinema_entries:
                                if (th_target.code and th_target.code == entry["code"]) or \
                                   (th_target.slug and th_target.slug in entry["url"].lower()) or \
                                   (th_target.name and th_target.name.lower() in entry["text"].lower()):
                                    label_name = f"{entry['text'] or th_target.name} [{entry['code']}]"
                                    if label_name not in matched_theatres:
                                        matched_theatres.append(label_name)
                                    break
                except Exception:
                    pass

                if matched_theatres:
                    return CheckResult(
                        is_open=True,
                        movie_name=movie_name,
                        city=city_name,
                        status_text=f"🎟️ BOOKINGS OPEN in {city_name} at target theatre: {', '.join(matched_theatres)}! [⚡ curl_cffi]",
                        booking_url=booking_url,
                        theatres_found=theatres_found,
                        matched_theatres=matched_theatres,
                        details={
                            "movie_name": movie_name,
                            "city": city_name,
                            "matched_theatres": matched_theatres,
                            "booking_url": booking_url,
                            "engine": "curl_cffi"
                        }
                    )
                else:
                    return CheckResult(
                        is_open=False,
                        movie_name=movie_name,
                        city=city_name,
                        status_text=f"Booking open in {city_name}, but waiting for {target.theatres_display} (currently listed: {', '.join(theatres_found[:3]) if theatres_found else 'none yet'}) [⚡ curl_cffi]",
                        booking_url=booking_url,
                        theatres_found=theatres_found,
                        matched_theatres=[],
                        details={"target_theatres": target.theatres_display, "city": city_name, "engine": "curl_cffi"}
                    )

            return CheckResult(
                is_open=True,
                movie_name=movie_name,
                city=city_name,
                status_text=f"🎟️ BOOKINGS ARE OPEN in {city_name}! [⚡ curl_cffi]",
                booking_url=booking_url,
                details={
                    "movie_name": movie_name,
                    "city": city_name,
                    "release_date": release_date,
                    "booking_url": booking_url,
                    "engine": "curl_cffi"
                }
            )
        except Exception as e:
            self._last_curl_reason = str(e)
            logger.debug(f"curl_cffi execution error on {target.url}: {e}")
            return None

    def create_driver(self) -> webdriver.Chrome:
        """Initializes an ultra-lean, high-efficiency headless Chrome webdriver session."""
        options = Options()
        if self.headless or os.getenv("RENDER"):
            options.add_argument("--headless=new")

        # Support custom Chromium/Chrome binary paths (e.g. Docker / Render)
        chrome_bin = os.getenv("CHROME_BIN", "").strip()
        if not chrome_bin:
            for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
                if os.path.exists(candidate):
                    chrome_bin = candidate
                    break
        if chrome_bin:
            options.binary_location = chrome_bin

        # Core optimization flags
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,720")
        options.page_load_strategy = "eager"

        # Background optimization (keep images enabled to avoid Cloudflare headless bot triggers)
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

        prefs = {
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.media_stream": 2,
            "profile.default_content_setting_values.geolocation": 2,
        }
        options.add_experimental_option("prefs", prefs)

        if self._proxy_url and "@" not in self._proxy_url:
            options.add_argument(f"--proxy-server={self._proxy_url}")

        user_agent = os.getenv("CHROME_USER_AGENT", "").strip()
        if user_agent:
            options.add_argument(f"user-agent={user_agent}")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)

        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "").strip()
        if not chromedriver_path and os.path.exists("/usr/bin/chromedriver"):
            chromedriver_path = "/usr/bin/chromedriver"

        if chromedriver_path:
            driver = webdriver.Chrome(service=Service(executable_path=chromedriver_path), options=options)
        else:
            driver = webdriver.Chrome(options=options)
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": (
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )
                },
            )
        except Exception as e:
            logger.debug(f"Could not apply CDP stealth script: {e}")
        driver.set_page_load_timeout(20)
        return driver

    def check_target(
        self,
        target: TargetItem,
        driver: Optional[webdriver.Chrome] = None,
        driver_factory: Optional[Callable[[], webdriver.Chrome]] = None
    ) -> CheckResult:
        """Inspects a specific BookMyShow Target URL using curl_cffi first, with seamless Chrome fallback."""
        if CURL_CFFI_AVAILABLE and self._curl_enabled:
            if self._curl_cooldown_remaining > 0:
                self._curl_cooldown_remaining -= 1
            else:
                self._last_curl_reason = ""
                try:
                    session = self._get_curl_session()
                    if session is not None:
                        if target.is_cinema:
                            result = self._check_cinema_page_curl(target, session)
                        else:
                            result = self._check_movie_page_curl(target, session)

                        if result is not None:
                            self._curl_consecutive_failures = 0
                            return result

                        self._curl_consecutive_failures += 1
                        reason = self._last_curl_reason or "challenge/unsupported format"
                        self._rotate_curl_session()
                        logger.info(
                            f"🔄 curl_cffi failed ({reason}) for {target.slug or target.url}; engaging Chrome fallback..."
                        )
                except Exception as e:
                    self._curl_consecutive_failures += 1
                    self._rotate_curl_session()
                    logger.info(
                        f"🔄 curl_cffi error ({e}) for {target.slug or target.url}; engaging Chrome fallback..."
                    )

                if self._curl_consecutive_failures >= 3:
                    self._curl_cooldown_remaining = 10
                    self._curl_consecutive_failures = 0
                    logger.info(
                        "⏭️ curl_cffi failed 3 times consecutively; pausing curl_cffi for 10 checks and using Chrome directly."
                    )

        # Chrome Fallback Execution
        try:
            active_driver = driver
            if active_driver is None and driver_factory is not None:
                active_driver = driver_factory()

            if active_driver is None:
                active_driver = self.create_driver()

            if target.is_cinema:
                result = self._check_cinema_page(target, active_driver)
            else:
                result = self._check_movie_page(target, active_driver)

            self._sync_cookies_from_driver(active_driver)
            return result
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

    @staticmethod
    def _is_waf_or_challenge_page(title: str, html: str) -> bool:
        """Detects Cloudflare or WAF block/challenge pages that prevent BookMyShow content from loading."""
        title_l = (title or "").lower()
        html_l = (html or "").lower()
        if any(marker in title_l for marker in ("just a moment", "attention required", "access denied", "cloudflare")):
            return True
        if any(
            marker in html_l
            for marker in (
                "cf-browser-verification",
                "challenge-platform",
                "sorry, you have been blocked",
                "verify you are human",
            )
        ):
            return True
        return False

    @staticmethod
    def _wait_for_cinema_page_ready(driver: webdriver.Chrome, timeout: int = 10) -> None:
        """Waits for Cloudflare JS challenges to clear and BookMyShow state or movie links to render."""
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: d.execute_script(
                    """
                    var title = (document.title || '').toLowerCase();
                    if (title.indexOf('just a moment') !== -1) return false;
                    if (title.indexOf('attention required') !== -1 || title.indexOf('access denied') !== -1) return true;
                    var s = window.__INITIAL_STATE__;
                    if (s && typeof s === 'object' && Object.keys(s).length > 0) {
                        var vfa = s.venueShowtimesFunctionalApi;
                        if (vfa && vfa.queries && Object.keys(vfa.queries).length > 0) return true;
                        if (document.querySelectorAll('a[href*="/movies/"]').length > 0) return true;
                        if (document.readyState === 'complete') return true;
                    }
                    if (document.readyState === 'complete' && document.querySelectorAll('a[href*="/movies/"]').length > 0) {
                        return true;
                    }
                    return false;
                    """
                )
            )
        except Exception:
            pass

    def _resolve_event_group_selenium(self, target: TargetItem, driver: webdriver.Chrome) -> str:
        """Resolves and caches BookMyShow's unique EventGroup ID (EG...) via Selenium."""
        cache_key = target.event_code or target.url
        if target.event_group_code:
            return target.event_group_code
        if cache_key in self._event_group_cache:
            target.event_group_code = self._event_group_cache[cache_key]
            return target.event_group_code
        if not target.url or target.is_cinema:
            return ""
        try:
            driver.get(target.url)
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script(
                        "var s = window.__INITIAL_STATE__; return Boolean(s && s.synopsisStore && s.synopsisStore.synopsis);"
                    )
                )
            except Exception:
                pass
            state = driver.execute_script("return window.__INITIAL_STATE__;") or {}
            eg = state.get("synopsisStore", {}).get("synopsis", {}).get("eventGroupCode", "")
            if isinstance(eg, str) and eg:
                self._event_group_cache[cache_key] = eg
                target.event_group_code = eg
                return eg
        except Exception as e:
            logger.debug(f"Could not resolve EventGroup via Selenium for {target.url}: {e}")
        return ""

    def _check_target_theatres_selenium(self, target: TargetItem, driver: webdriver.Chrome) -> CheckResult:
        """Checks target theatre URLs directly via headless Chrome, bypassing the movie page gate."""
        cache_key = target.event_code or target.url
        if not target.event_group_code and cache_key in self._event_group_cache:
            target.event_group_code = self._event_group_cache[cache_key]

        fallback_name = self._derive_movie_name(target)
        matched_theatres = []
        matched_url = ""
        matched_movie_name = fallback_name
        first_theatre_url = next((th.url for th in target.target_theatres if th.url), target.url)
        waf_blocked_reasons = []
        checked_urls = 0

        for th_target in target.target_theatres:
            if not th_target.url:
                continue
            checked_urls += 1
            try:
                driver.get(th_target.url)
                self._wait_for_cinema_page_ready(driver, timeout=10)
                if self._is_redirected_away_from_date(driver.current_url, th_target.date):
                    logger.debug(
                        f"Theatre URL {th_target.url} redirected to {driver.current_url} "
                        f"(date {th_target.date} not listed yet)"
                    )
                    continue
                state = driver.execute_script("return window.__INITIAL_STATE__;") or {}
                page_source = driver.page_source or ""
                title = getattr(driver, "title", "") or ""

                if not state and self._is_waf_or_challenge_page(title, page_source):
                    reason = title.strip() or "Cloudflare/WAF challenge"
                    logger.warning(
                        f"🛡️ Headless Chrome blocked on {th_target.code or th_target.url} ({reason})"
                    )
                    waf_blocked_reasons.append(f"{th_target.code or 'venue'}: {reason}")
                    continue

                found_name = self._match_movie_in_cinema_page(
                    page_source, state, target, fallback_name, target_date=th_target.date
                )
                if not found_name and not target.event_group_code and isinstance(state, dict) and (
                    "venueShowtimesFunctionalApi" in state or "venueShowtimesNew" in state
                ):
                    if self._resolve_event_group_selenium(target, driver):
                        found_name = self._match_movie_in_cinema_page(
                            page_source, state, target, fallback_name, target_date=th_target.date
                        )
                if found_name:
                    matched_movie_name = found_name
                    label_name = f"{th_target.name} [{th_target.code}]"
                    if label_name not in matched_theatres:
                        matched_theatres.append(label_name)
                    if not matched_url:
                        matched_url = th_target.url
            except Exception as e:
                logger.warning(f"Error checking direct cinema URL {th_target.url}: {e}")

        if matched_theatres:
            return CheckResult(
                is_open=True,
                movie_name=matched_movie_name,
                city=target.city,
                status_text=f"🎟️ BOOKINGS OPEN in {target.city} at target theatre: {', '.join(matched_theatres)}!",
                booking_url=matched_url,
                theatres_found=matched_theatres,
                matched_theatres=matched_theatres,
                details={
                    "movie_name": matched_movie_name,
                    "city": target.city,
                    "matched_theatres": matched_theatres,
                    "booking_url": matched_url
                }
            )

        if checked_urls > 0 and len(waf_blocked_reasons) == checked_urls:
            block_summary = "; ".join(waf_blocked_reasons)
            return CheckResult(
                is_open=False,
                movie_name=fallback_name,
                city=target.city,
                status_text=f"Blocked by Cloudflare/WAF in headless Chrome ({block_summary})",
                booking_url=first_theatre_url,
                theatres_found=[],
                matched_theatres=[],
                details={
                    "target_theatres": target.theatres_display,
                    "city": target.city,
                    "waf_blocked": waf_blocked_reasons,
                },
                raw_error=f"Cloudflare/WAF block: {block_summary}",
            )

        return CheckResult(
            is_open=False,
            movie_name=fallback_name,
            city=target.city,
            status_text=f"Waiting for {fallback_name} at {target.theatres_display}",
            booking_url=first_theatre_url,
            theatres_found=[],
            matched_theatres=[],
            details={
                "target_theatres": target.theatres_display,
                "city": target.city
            }
        )

    def _check_movie_page(self, target: TargetItem, driver: webdriver.Chrome) -> CheckResult:
        if target.has_theatre_urls:
            return self._check_target_theatres_selenium(target, driver)

        driver.get(target.url)

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
        if not state and self._is_waf_or_challenge_page(getattr(driver, "title", ""), driver.page_source or ""):
            reason = (getattr(driver, "title", "") or "Cloudflare/WAF challenge").strip()
            logger.warning(f"🛡️ Headless Chrome blocked on {target.url} ({reason})")
            return CheckResult(
                is_open=False,
                movie_name=target.slug or "Unknown",
                city=target.city or "Unknown",
                status_text=f"Blocked by Cloudflare/WAF in headless Chrome ({reason})",
                booking_url=target.url,
                raw_error=f"Cloudflare/WAF block: {reason}",
            )
        syn = state.get("synopsisStore", {}).get("synopsis", {})
        analytics = syn.get("analytics", {})

        movie_name = syn.get("eventName") or target.slug.replace("-", " ").title()
        release_date = syn.get("releaseDate") or analytics.get("eventReleaseDate", "")
        book_button_state = syn.get("bookButton")
        label = analytics.get("label", "").lower()
        event_code = syn.get("eventCode") or target.event_code

        city_name = target.city
        cookies = state.get("cookies", {})
        if cookies.get("rgn", {}).get("regionName"):
            city_name = cookies["rgn"]["regionName"]

        slug = re.sub(r"[^a-zA-Z0-9]+", "-", movie_name.lower()).strip("-")
        city_slug = re.sub(r"[^a-zA-Z0-9]+", "-", city_name.lower()).strip("-")
        booking_url = f"https://in.bookmyshow.com/buytickets/{slug}-{city_slug}/movie-{city_slug[:3]}-{event_code}-MT/"

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

        theatres_found = []
        matched_theatres = []

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
