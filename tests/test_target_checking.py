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

    def test_load_targets_with_at_date_parameter(self):
        content = (
            "https://in.bookmyshow.com/movies/coimbatore/dune-part-three/ET00491771 | "
            "https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/BWCB | @20261127\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            (tmp_root / "targets.txt").write_text(content, encoding="utf-8")
            with patch("config.PROJECT_ROOT", tmp_root):
                targets = load_targets_from_file_or_env()

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].target_date, "20261127")
        self.assertEqual(
            targets[0].target_theatres[0].url,
            "https://in.bookmyshow.com/cinemas/coimbatore/broadway-cinemas-coimbatore/buytickets/BWCB/20261127",
        )


class TestTheatreFirstChecking(unittest.TestCase):
    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()
