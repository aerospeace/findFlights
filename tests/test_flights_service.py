"""Tests for the flight search service and caching logic."""
import json
import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Allow importing app modules from parent directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import _date_range, _parse_airports, create_app
from models import db, FlightQuery
from flights_service import _make_query_hash, search_flights


# ------------------------------------------------------------------ #
#  Fixtures                                                            #
# ------------------------------------------------------------------ #

@pytest.fixture
def app():
    """Create a Flask app with an in-memory SQLite database."""
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "CACHE_DAYS": 1,
        }
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_ctx(app):
    with app.app_context():
        yield app


# ------------------------------------------------------------------ #
#  _make_query_hash                                                    #
# ------------------------------------------------------------------ #

class TestMakeQueryHash:
    def test_same_params_same_hash(self):
        h1 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        h2 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        assert h1 == h2

    def test_different_airports_different_hash(self):
        h1 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        h2 = _make_query_hash("JFK", "SFO", "2025-06-01", "one-way", "economy", 1, 0, None)
        assert h1 != h2

    def test_case_insensitive_airports(self):
        h1 = _make_query_hash("jfk", "lax", "2025-06-01", "one-way", "economy", 1, 0, None)
        h2 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        assert h1 == h2

    def test_different_dates_different_hash(self):
        h1 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        h2 = _make_query_hash("JFK", "LAX", "2025-06-02", "one-way", "economy", 1, 0, None)
        assert h1 != h2

    def test_max_stops_affects_hash(self):
        h1 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        h2 = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, 0)
        assert h1 != h2

    def test_returns_64_char_hex(self):
        h = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ------------------------------------------------------------------ #
#  search_flights (service layer)                                      #
# ------------------------------------------------------------------ #

def _make_mock_result(price="$199"):
    """Build a mock fast-flights Result object."""
    flight = MagicMock()
    flight.is_best = True
    flight.name = "Test Airline"
    flight.departure = "08:00"
    flight.arrival = "11:00"
    flight.arrival_time_ahead = ""
    flight.duration = "3h 00m"
    flight.stops = 0
    flight.delay = None
    flight.price = price

    result = MagicMock()
    result.current_price = "low"
    result.flights = [flight]
    return result


class TestSearchFlights:
    def test_calls_api_and_caches_result(self, app_ctx):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result) as mock_api:
            result = search_flights(
                "JFK", "LAX", "2025-06-01",
                trip="one-way", seat="economy", adults=1,
            )

        assert mock_api.call_count == 1
        assert result["from_cache"] is False
        assert len(result["flights"]) == 1
        assert result["flights"][0]["price"] == "$199"
        assert result["current_price"] == "low"

        # Verify it was persisted
        record = FlightQuery.query.first()
        assert record is not None
        assert record.from_airport == "JFK"
        assert record.to_airport == "LAX"

    def test_second_call_uses_cache(self, app_ctx):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result) as mock_api:
            search_flights("JFK", "LAX", "2025-06-01")
            result = search_flights("JFK", "LAX", "2025-06-01")

        # API should only have been called once
        assert mock_api.call_count == 1
        assert result["from_cache"] is True

    def test_cache_bypassed_when_too_old(self, app_ctx):
        """An existing entry older than cache_days should trigger a fresh API call."""
        old_payload = json.dumps({"flights": [], "current_price": "typical"})
        old_ts = datetime.now(timezone.utc) - timedelta(days=3)
        query_hash = _make_query_hash("JFK", "LAX", "2025-06-01", "one-way", "economy", 1, 0, None)
        old_record = FlightQuery(
            query_hash=query_hash,
            from_airport="JFK",
            to_airport="LAX",
            date="2025-06-01",
            trip="one-way",
            seat="economy",
            adults=1,
            children=0,
            max_stops=None,
            timestamp=old_ts,
            results_json=old_payload,
        )
        db.session.add(old_record)
        db.session.commit()

        mock_result = _make_mock_result("$299")
        with patch("flights_service.get_flights", return_value=mock_result) as mock_api:
            result = search_flights(
                "JFK", "LAX", "2025-06-01",
                cache_days=1,
            )

        assert mock_api.call_count == 1
        assert result["from_cache"] is False
        assert result["flights"][0]["price"] == "$299"

    def test_cache_zero_days_always_fetches(self, app_ctx):
        """cache_days=0 should always call the API."""
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result) as mock_api:
            search_flights("JFK", "LAX", "2025-06-01", cache_days=0)
            search_flights("JFK", "LAX", "2025-06-01", cache_days=0)

        assert mock_api.call_count == 2

    def test_airport_codes_uppercased(self, app_ctx):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            search_flights("jfk", "lax", "2025-06-01")

        record = FlightQuery.query.first()
        assert record.from_airport == "JFK"
        assert record.to_airport == "LAX"

    def test_result_contains_cached_at(self, app_ctx):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            result = search_flights("JFK", "LAX", "2025-06-01")

        assert "cached_at" in result

    def test_multiple_flights_stored(self, app_ctx):
        flight2 = MagicMock()
        flight2.is_best = False
        flight2.name = "Other Airline"
        flight2.departure = "14:00"
        flight2.arrival = "17:30"
        flight2.arrival_time_ahead = ""
        flight2.duration = "3h 30m"
        flight2.stops = 1
        flight2.delay = "15 min delay"
        flight2.price = "$149"

        mock_result = MagicMock()
        mock_result.current_price = "low"
        mock_result.flights = [_make_mock_result().flights[0], flight2]

        with patch("flights_service.get_flights", return_value=mock_result):
            result = search_flights("JFK", "LAX", "2025-06-01")

        assert len(result["flights"]) == 2
        assert result["flights"][1]["name"] == "Other Airline"
        assert result["flights"][1]["stops"] == 1


# ------------------------------------------------------------------ #
#  Flask routes (integration)                                          #
# ------------------------------------------------------------------ #

class TestRoutes:
    def test_index_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"Search" in response.data

    def test_search_missing_params_redirects(self, client):
        response = client.get("/search")
        assert response.status_code == 302

    def test_search_post_redirects_to_get(self, client):
        response = client.post(
            "/search",
            data={
                "from_airports": "JFK",
                "to_airports": "LAX",
                "date_from": "2025-06-01",
                "date_to": "2025-06-01",
                "trip": "one-way",
                "seat": "economy",
                "adults": "1",
                "children": "0",
            },
        )
        assert response.status_code == 302
        location = response.headers["Location"]
        assert "from_airports=JFK" in location
        assert "to_airports=LAX" in location

    def test_search_get_returns_results(self, client, app):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            with app.app_context():
                response = client.get(
                    "/search",
                    query_string={
                        "from_airports": "JFK",
                        "to_airports": "LAX",
                        "date_from": "2025-06-01",
                        "date_to": "2025-06-01",
                    },
                )
        assert response.status_code == 200
        assert b"Test Airline" in response.data

    def test_api_search_missing_params(self, client):
        response = client.get("/api/search")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_api_search_returns_json(self, client, app):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            with app.app_context():
                response = client.get(
                    "/api/search",
                    query_string={
                        "from_airports": "JFK",
                        "to_airports": "LAX",
                        "date_from": "2025-06-01",
                        "date_to": "2025-06-01",
                    },
                )
        assert response.status_code == 200
        data = response.get_json()
        assert "results" in data
        assert len(data["results"]) == 1

    def test_search_shows_cache_notice(self, client, app):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            with app.app_context():
                # First call populates the cache
                client.get(
                    "/search",
                    query_string={
                        "from_airports": "JFK",
                        "to_airports": "LAX",
                        "date_from": "2025-06-01",
                        "date_to": "2025-06-01",
                    },
                )
                # Second call hits the cache
                response = client.get(
                    "/search",
                    query_string={
                        "from_airports": "JFK",
                        "to_airports": "LAX",
                        "date_from": "2025-06-01",
                        "date_to": "2025-06-01",
                    },
                )
        assert b"cache" in response.data.lower()

    def test_api_search_returns_multiple_results_for_range(self, client, app):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            with app.app_context():
                response = client.get(
                    "/api/search",
                    query_string={
                        "from_airports": "JFK,EWR",
                        "to_airports": "LAX",
                        "date_from": "2025-06-01",
                        "date_to": "2025-06-02",
                    },
                )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["results"]) == 4

    def test_search_csv_download(self, client, app):
        mock_result = _make_mock_result()
        with patch("flights_service.get_flights", return_value=mock_result):
            with app.app_context():
                response = client.get(
                    "/search.csv",
                    query_string={
                        "from_airports": "JFK",
                        "to_airports": "LAX",
                        "date_from": "2025-06-01",
                        "date_to": "2025-06-01",
                    },
                )
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert b"search_date,from_airport,to_airport" in response.data


class TestSearchHelpers:
    def test_parse_airports(self):
        assert _parse_airports("jfk, ewr, ba, JFK") == ["JFK", "EWR"]

    def test_date_range(self):
        assert _date_range("2025-06-01", "2025-06-03") == ["2025-06-01", "2025-06-02", "2025-06-03"]
