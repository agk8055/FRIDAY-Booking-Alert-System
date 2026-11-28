import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import TargetItem, TheatreTarget, load_targets_from_file_or_env  # noqa: E402
from checker import BMSChecker  # noqa: E402


class TestTheatreTargetParsing(unittest.TestCase):
    def test_dated_buytickets_url_parsing(self):
        url = (
            "https://in.bookmyshow.com/cinemas/KOCH/"
            "vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally/buytickets/VMHE/20261127"
        )
        th = TheatreTarget(raw_input=url)
        self.assertEqual(th.url, url)
        self.assertEqual(th.code, "VMHE")
        self.assertEqual(th.date, "20261127")
        self.assertEqual(th.city_segment, "KOCH")
        self.assertEqual(th.slug, "vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally")

    def test_base_cinema_url_with_target_date_builds_dated_url(self):
        th = TheatreTarget(
            raw_input="https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/BWCB"
        )
        item = TargetItem(
            url="https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771",
            target_theatres=[th],
            target_date="20261127",
        )
        self.assertTrue(item.has_theatre_urls)
        self.assertEqual(
            item.target_theatres[0].url,
            "https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/buytickets/BWCB/20261127",
        )
        self.assertEqual(item.target_theatres[0].date, "20261127")

    def test_load_targets_with_target_date_parameter(self):
        payload = {
            "targets": [
                {
                    "enabled": True,
                    "url": "https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771",
                    "theatres": [
                        "https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/BWCB"
                    ],
                    "target_date": "20261127",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "targets.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch("config.PROJECT_ROOT", tmp_root):
                targets = load_targets_from_file_or_env()

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].target_date, "20261127")
        self.assertEqual(
            targets[0].target_theatres[0].url,
            "https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/buytickets/BWCB/20261127",
        )

    def test_load_targets_returns_empty_when_all_disabled_or_empty(self):
        payload = {
            "targets": [
                {
                    "enabled": False,
                    "url": "https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
                    "theatres": [],
                    "target_date": "",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "targets.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch("config.PROJECT_ROOT", tmp_root), patch.dict("os.environ", {}, clear=False):
                targets = load_targets_from_file_or_env()

        self.assertEqual(targets, [])


class TestTheatreFirstChecking(unittest.TestCase):
    def setUp(self):
        with patch.dict("os.environ", {"USE_CURL_CFFI": "true"}):
            self.checker = BMSChecker(headless=True)
        self.theatre_url = (
            "https://in.bookmyshow.com/cinemas/KOCH/"
            "vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally/buytickets/VMHE/20261127"
        )
        self.target = TargetItem(
            url="https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
            target_theatres=[TheatreTarget(raw_input=self.theatre_url)],
        )

    def test_match_movie_in_initial_state_json(self):
        state = {
            "showtimesByEvent": {
                "ET00439706": {
                    "eventName": "Avengers: Doomsday",
                    "eventCode": "ET00439706",
                }
            }
        }
        html = f"<html><script>window.__INITIAL_STATE__ = {json.dumps(state)};</script></html>"
        mock_session = MagicMock()
        mock_resp = MagicMock(status_code=200, text=html)
        mock_session.get.return_value = mock_resp

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(self.target)

        # Must only fetch the theatre URL directly, NOT the movie page
        mock_session.get.assert_called_once()
        self.assertEqual(mock_session.get.call_args[0][0], self.theatre_url)
        self.assertTrue(result.is_open)
        self.assertEqual(result.movie_name, "Avengers: Doomsday")
        self.assertEqual(result.booking_url, self.theatre_url)
        self.assertIn("VMHE", result.matched_theatres[0])

    def test_match_movie_in_html_link(self):
        html = (
            '<html><body>'
            '<a href="/movies/kochi/avengers-doomsday/ET00439706">Avengers Doomsday</a>'
            '</body></html>'
        )
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=200, text=html)

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(self.target)

        self.assertTrue(result.is_open)
        self.assertEqual(result.booking_url, self.theatre_url)

    def test_not_listed_at_target_theatre_returns_closed(self):
        html = '<html><body><a href="/movies/kochi/other-movie/ET00999999">Other Movie</a></body></html>'
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=200, text=html, url=self.theatre_url)

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(self.target)

        mock_session.get.assert_called_once()
        self.assertFalse(result.is_open)
        self.assertIn("Waiting for", result.status_text)

    def test_seo_footer_and_footer_links_do_not_false_positive(self):
        state = {
            "seo": {
                "queries": {
                    "/cinemas/KOCH/vanitha/buytickets/VMHE/20261127": {
                        "data": {
                            "footer": {
                                "links": [
                                    {
                                        "items": [
                                            {
                                                "label": "Avengers: Doomsday",
                                                "link": "/movies/avengers-doomsday-msinfinity-vsn-3d/ET00526306",
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            },
            "venueShowtimesFunctionalApi": {
                "queries": {
                    "getShowtimesByVenue-VMHE-20261127": {
                        "originalArgs": {"dateCode": "20261127"},
                        "data": {
                            "showDetailsTransformed": {
                                "events": [{"EventCode": "ET00999999", "EventTitle": "Other Movie"}]
                            }
                        },
                    }
                }
            },
        }
        html = (
            f"<html><body>"
            f'<footer><a href="/movies/avengers-doomsday/ET00439706">Avengers: Doomsday</a></footer>'
            f"<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script>"
            f"</body></html>"
        )
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=200, text=html, url=self.theatre_url)

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(self.target)

        self.assertFalse(result.is_open)

    def test_redirect_to_different_date_returns_closed(self):
        target_dec17 = TargetItem(
            url="https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
            target_theatres=[
                TheatreTarget(
                    raw_input="https://in.bookmyshow.com/cinemas/KOCH/vanitha/buytickets/VMHE/20261217"
                )
            ],
        )
        html = '<html><body><a href="/movies/kochi/avengers-doomsday/ET00439706">Avengers: Doomsday</a></body></html>'
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(
            status_code=200,
            text=html,
            url="https://in.bookmyshow.com/cinemas/KOCH/vanitha/buytickets/VMHE/20261127",
        )

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(target_dec17)

        self.assertFalse(result.is_open)

    def test_different_format_event_code_does_not_match_when_event_code_specified(self):
        target_2d = TargetItem(
            url="https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
            target_theatres=[
                TheatreTarget(
                    raw_input="https://in.bookmyshow.com/cinemas/kochi/evm-dolby-cinema-fort-kochi/buytickets/EVMK"
                )
            ],
            target_date="20261217",
        )
        state = {
            "venueShowtimesFunctionalApi": {
                "queries": {
                    "getShowtimesByVenue-EVMK-20261217": {
                        "originalArgs": {"dateCode": "20261217"},
                        "data": {
                            "showDetailsTransformed": {
                                "Event": [
                                    {
                                        "EventTitle": "Avengers: Doomsday",
                                        "ChildEvents": [
                                            {
                                                "EventName": "Avengers: Doomsday (DOLBY CINEMA 2D) - English",
                                                "EventCode": "ET00526329",
                                                "EventImageCode": "avengers-doomsday-et00526329-1794903208",
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    }
                }
            }
        }
        html = f"<html><script>window.__INITIAL_STATE__ = {json.dumps(state)};</script></html>"
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(
            status_code=200,
            text=html,
            url="https://in.bookmyshow.com/cinemas/kochi/evm-dolby-cinema-fort-kochi/buytickets/EVMK/20261217",
        )

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(target_2d)

        self.assertFalse(result.is_open)

    def test_matches_across_format_event_codes_via_event_group_id(self):
        target_movie_url = "https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706"
        theatre_url = "https://in.bookmyshow.com/cinemas/kochi/evm-dolby-cinema-fort-kochi/buytickets/EVMK/20261217"
        target_dolby = TargetItem(
            url=target_movie_url,
            target_theatres=[TheatreTarget(raw_input=theatre_url)],
        )
        theatre_state = {
            "venueShowtimesFunctionalApi": {
                "queries": {
                    "getShowtimesByVenue-EVMK-20261217": {
                        "originalArgs": {"dateCode": "20261217"},
                        "data": {
                            "showDetailsTransformed": {
                                "Event": [
                                    {
                                        "EventTitle": "Avengers: Doomsday",
                                        "ChildEvents": [
                                            {
                                                "EventName": "Avengers: Doomsday (DOLBY CINEMA 2D) - English",
                                                "EventCode": "ET00526329",
                                                "EventGroup": "EG00437704",
                                            }
                                        ],
                                    }
                                ]
                            }
                        },
                    }
                }
            }
        }
        movie_state = {
            "synopsisStore": {
                "synopsis": {
                    "eventCode": "ET00439706",
                    "eventGroupCode": "EG00437704",
                    "eventName": "Avengers: Doomsday",
                }
            }
        }
        theatre_html = f"<html><script>window.__INITIAL_STATE__ = {json.dumps(theatre_state)};</script></html>"
        movie_html = f"<html><script>window.__INITIAL_STATE__ = {json.dumps(movie_state)};</script></html>"

        mock_session = MagicMock()
        mock_session.get.side_effect = [
            MagicMock(status_code=200, text=theatre_html, url=theatre_url),
            MagicMock(status_code=200, text=movie_html, url=target_movie_url),
        ]

        with patch.object(self.checker, "_get_curl_session", return_value=mock_session):
            result = self.checker.check_target(target_dolby)

        self.assertTrue(result.is_open)
        self.assertEqual(result.movie_name, "Avengers: Doomsday (DOLBY CINEMA 2D) - English")
        self.assertIn("EVMK", result.matched_theatres[0])
        self.assertEqual(self.checker._event_group_cache.get("ET00439706"), "EG00437704")


class TestCurlCffiFallbackAndCookies(unittest.TestCase):
    def test_sync_cookies_from_driver_and_cooldown(self):
        with patch.dict("os.environ", {"USE_CURL_CFFI": "true"}):
            checker = BMSChecker(headless=True)
        target = TargetItem(url="https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771")

        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=403, text="Forbidden")

        mock_driver = MagicMock()
        mock_driver.get_cookies.return_value = [
            {"name": "cf_clearance", "value": "abc123", "domain": ".bookmyshow.com", "path": "/"}
        ]
        fake_result = MagicMock(is_open=False, movie_name="Dune Part Three")

        with patch.object(checker, "_get_curl_session", return_value=mock_session), \
             patch.object(checker, "_check_movie_page", return_value=fake_result):
            for _ in range(3):
                checker.check_target(target, driver=mock_driver)

            # Cookie from Chrome fallback should be synced to curl_cffi session
            mock_session.cookies.set.assert_called_with(
                "cf_clearance", "abc123", domain=".bookmyshow.com", path="/"
            )
            self.assertEqual(mock_session.get.call_count, 3)
            self.assertEqual(checker._curl_cooldown_remaining, 10)

            # 4th call should skip curl_cffi during cooldown
            checker.check_target(target, driver=mock_driver)
            self.assertEqual(mock_session.get.call_count, 3)
            self.assertEqual(checker._curl_cooldown_remaining, 9)

    def test_use_curl_cffi_false_skips_curl(self):
        with patch.dict("os.environ", {"USE_CURL_CFFI": "false"}):
            checker = BMSChecker(headless=True)
        target = TargetItem(url="https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771")
        mock_driver = MagicMock()
        fake_result = MagicMock(is_open=False, movie_name="Dune Part Three")

        with patch.object(checker, "_get_curl_session") as mock_get_sess, \
             patch.object(checker, "_check_movie_page", return_value=fake_result):
            checker.check_target(target, driver=mock_driver)

        mock_get_sess.assert_not_called()

    def test_impersonate_and_user_agent_alignment(self):
        with patch.dict("os.environ", {"CURL_IMPERSONATE": "chrome124"}):
            self.assertEqual(BMSChecker._resolve_impersonate(), "chrome124")
            self.assertIn(
                "Chrome/124.0.0.0",
                BMSChecker._derive_chrome_user_agent(BMSChecker._resolve_impersonate()),
            )

        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CURL_IMPERSONATE", None)
            self.assertEqual(BMSChecker._resolve_impersonate(), "chrome152")
            self.assertIn(
                "Chrome/152.0.0.0",
                BMSChecker._derive_chrome_user_agent(BMSChecker._resolve_impersonate()),
            )

    def test_curl_profile_rotation_and_proxy_forwarding(self):
        with patch.dict("os.environ", {"USE_CURL_CFFI": "true", "PROXY_URL": "http://user:pass@1.2.3.4:8080"}):
            checker = BMSChecker(headless=True)
        with patch("checker.curl_requests.Session") as mock_session_cls:
            checker._get_curl_session()
            mock_session_cls.assert_called_with(
                impersonate="chrome152",
                proxies={"http": "http://user:pass@1.2.3.4:8080", "https": "http://user:pass@1.2.3.4:8080"},
            )
            checker._synced_user_agent = "StaleUA"
            checker._rotate_curl_session()
            self.assertEqual(checker._synced_user_agent, "")
            checker._get_curl_session()
            mock_session_cls.assert_called_with(
                impersonate="safari180",
                proxies={"http": "http://user:pass@1.2.3.4:8080", "https": "http://user:pass@1.2.3.4:8080"},
            )

    def test_sync_cookies_also_syncs_user_agent_to_curl_headers(self):
        with patch.dict("os.environ", {"USE_CURL_CFFI": "true"}):
            checker = BMSChecker(headless=True)
        mock_session = MagicMock()
        mock_driver = MagicMock()
        mock_driver.execute_script.return_value = "Mozilla/5.0 CustomChrome/136.0.0.0"
        mock_driver.get_cookies.return_value = [
            {"name": "cf_clearance", "value": "tok", "domain": ".bookmyshow.com", "path": "/"}
        ]

        with patch.object(checker, "_get_curl_session", return_value=mock_session):
            checker._sync_cookies_from_driver(mock_driver)

        headers = checker._get_curl_headers()
        self.assertEqual(headers.get("User-Agent"), "Mozilla/5.0 CustomChrome/136.0.0.0")

    def test_selenium_falls_back_to_dom_links_when_venue_api_queries_empty(self):
        checker = BMSChecker(headless=True)
        theatre_url = (
            "https://in.bookmyshow.com/cinemas/KOCH/"
            "vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally/buytickets/VMHE/20261127"
        )
        target = TargetItem(
            url="https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
            target_theatres=[TheatreTarget(raw_input=theatre_url)],
        )
        mock_driver = MagicMock()
        mock_driver.current_url = theatre_url
        mock_driver.title = "Vanitha Cineplex | BookMyShow"
        mock_driver.page_source = (
            '<html><body><main>'
            '<a href="/movies/kochi/avengers-doomsday/ET00439706">Avengers: Doomsday</a>'
            '</main></body></html>'
        )
        mock_driver.execute_script.side_effect = [
            True,
            {"venueShowtimesFunctionalApi": {"queries": {}}},
        ]

        result = checker._check_target_theatres_selenium(target, mock_driver)
        self.assertTrue(result.is_open)
        self.assertIn("VMHE", result.matched_theatres[0])

    def test_selenium_reports_waf_block_instead_of_false_waiting(self):
        checker = BMSChecker(headless=True)
        theatre_url = (
            "https://in.bookmyshow.com/cinemas/KOCH/"
            "vanitha-cineplex-rgb-laser-4k-3d-atmos-edappally/buytickets/VMHE/20261127"
        )
        target = TargetItem(
            url="https://in.bookmyshow.com/movies/kochi/avengers-doomsday/ET00439706",
            target_theatres=[TheatreTarget(raw_input=theatre_url)],
        )
        mock_driver = MagicMock()
        mock_driver.current_url = theatre_url
        mock_driver.title = "Just a moment..."
        mock_driver.page_source = "<html><body><div id='challenge-platform'>Verify</div></body></html>"
        mock_driver.execute_script.side_effect = [True, {}]

        result = checker._check_target_theatres_selenium(target, mock_driver)
        self.assertFalse(result.is_open)
        self.assertIsNotNone(result.raw_error)
        self.assertIn("Cloudflare/WAF", result.status_text)

    def test_create_driver_uses_undetected_chromedriver_and_falls_back(self):
        checker = BMSChecker(headless=True)
        mock_uc_driver = MagicMock()
        with patch("checker.UC_AVAILABLE", True), \
             patch("checker.uc") as mock_uc:
            mock_uc.ChromeOptions.return_value = MagicMock()
            mock_uc.Chrome.return_value = mock_uc_driver
            drv = checker.create_driver()
            self.assertIs(drv, mock_uc_driver)
            mock_uc.Chrome.assert_called_once()
            self.assertTrue(mock_uc.Chrome.call_args.kwargs.get("headless"))

        mock_std_driver = MagicMock()
        with patch("checker.UC_AVAILABLE", True), \
             patch("checker.uc") as mock_uc, \
             patch.object(checker, "_find_cached_selenium_chromedriver", return_value=""), \
             patch("checker.webdriver.Chrome", return_value=mock_std_driver) as mock_chrome_cls:
            mock_uc.ChromeOptions.return_value = MagicMock()
            mock_uc.Chrome.side_effect = RuntimeError("uc failed")
            drv = checker.create_driver()
            self.assertIs(drv, mock_std_driver)
            mock_chrome_cls.assert_called_once()

    def test_render_with_xvfb_display_uses_non_headless_mode(self):
        checker = BMSChecker(headless=False)
        mock_uc_driver = MagicMock()
        with patch.dict("os.environ", {"RENDER": "true", "DISPLAY": ":99"}), \
             patch("checker.UC_AVAILABLE", True), \
             patch("checker.uc") as mock_uc:
            mock_uc.ChromeOptions.return_value = MagicMock()
            mock_uc.Chrome.return_value = mock_uc_driver
            checker.create_driver()
            self.assertFalse(mock_uc.Chrome.call_args.kwargs.get("headless"))


if __name__ == "__main__":
    unittest.main()
