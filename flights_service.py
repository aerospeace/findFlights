import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fast_flights import FlightQuery as FastFlightQuery, Passengers, create_query, get_flights

from models import FlightQuery as CachedFlightQuery, db

CACHE_DAYS_DEFAULT = 1


def _format_hhmm(value: tuple[int, int] | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    h, m = value
    return f"{h:02d}:{m:02d}"


def _format_duration(value: int | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    hours, mins = divmod(value, 60)
    return f"{hours}h {mins:02d}m"


def _make_query_hash(
    from_airport: str,
    to_airport: str,
    date: str,
    trip: str,
    seat: str,
    adults: int,
    children: int,
    max_stops: Optional[int],
) -> str:
    """Return a deterministic hex hash for the given search parameters."""
    payload = json.dumps(
        {
            "from": from_airport.upper(),
            "to": to_airport.upper(),
            "date": date,
            "trip": trip,
            "seat": seat,
            "adults": adults,
            "children": children,
            "max_stops": max_stops,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _normalize_new_flight(flight) -> dict:
    segments = list(getattr(flight, "flights", []) or [])
    first_segment = segments[0] if segments else None
    last_segment = segments[-1] if segments else None

    departure = _format_hhmm(getattr(getattr(first_segment, "departure", None), "time", None))
    arrival = _format_hhmm(getattr(getattr(last_segment, "arrival", None), "time", None))

    arrival_time_ahead = ""
    dep_date = getattr(getattr(first_segment, "departure", None), "date", None)
    arr_date = getattr(getattr(last_segment, "arrival", None), "date", None)
    if dep_date and arr_date and dep_date != arr_date:
        try:
            dep_dt = datetime(*dep_date)
            arr_dt = datetime(*arr_date)
            day_diff = (arr_dt.date() - dep_dt.date()).days
            if day_diff > 0:
                arrival_time_ahead = str(day_diff)
        except (TypeError, ValueError):
            arrival_time_ahead = ""

    airlines = list(getattr(flight, "airlines", []) or [])
    raw_price = getattr(flight, "price", None)
    if isinstance(raw_price, int):
        price = f"${raw_price:,}"
    else:
        price = str(raw_price) if raw_price is not None else ""

    return {
        "is_best": getattr(flight, "type", "") == "best",
        "name": ", ".join(airlines) if airlines else "Unknown Airline",
        "departure": departure,
        "arrival": arrival,
        "arrival_time_ahead": arrival_time_ahead,
        "duration": _format_duration(getattr(flight, "duration", None)),
        "stops": max(len(segments) - 1, 0),
        "delay": None,
        "price": price,
    }


def _flight_to_dict(flight) -> dict:
    # Backward-compatible mapping for old fast_flights models used in tests.
    if hasattr(flight, "is_best") and hasattr(flight, "departure"):
        return {
            "is_best": flight.is_best,
            "name": flight.name,
            "departure": flight.departure,
            "arrival": flight.arrival,
            "arrival_time_ahead": flight.arrival_time_ahead,
            "duration": flight.duration,
            "stops": flight.stops,
            "delay": flight.delay,
            "price": flight.price,
        }

    # New fast_flights models.
    return _normalize_new_flight(flight)


def _result_to_payload(result) -> dict:
    # Old shape: object with .flights and .current_price
    if hasattr(result, "flights"):
        flights = list(result.flights)
        current_price = getattr(result, "current_price", None)
    else:
        # New shape: list-like MetaList[Flights]
        flights = list(result or [])
        current_price = None

    return {
        "flights": [_flight_to_dict(f) for f in flights],
        "current_price": current_price,
    }


def _fetch_flights_compat(
    from_airport: str,
    to_airport: str,
    date: str,
    trip: str,
    seat: str,
    adults: int,
    children: int,
    max_stops: Optional[int],
):
    query = create_query(
        flights=[
            FastFlightQuery(
                date=date,
                from_airport=from_airport,
                to_airport=to_airport,
                max_stops=max_stops,
            )
        ],
        trip=trip,
        passengers=Passengers(adults=adults, children=children),
        seat=seat,
        max_stops=max_stops,
    )
    return get_flights(query)


def search_flights(
    from_airport: str,
    to_airport: str,
    date: str,
    trip: str = "one-way",
    seat: str = "economy",
    adults: int = 1,
    children: int = 0,
    max_stops: Optional[int] = None,
    cache_days: int = CACHE_DAYS_DEFAULT,
) -> dict:
    """Search for flights, using a cached result when available.

    Each unique search is stored in the database together with a
    timestamp and the serialised results.  Subsequent identical
    searches within *cache_days* days are served from the database
    instead of hitting the upstream API.

    Returns a dict with keys:
      - ``flights``       – list of flight dicts
      - ``current_price`` – Google's price-level indicator
      - ``from_cache``    – True if the result came from the cache
      - ``cached_at``     – ISO timestamp of when the result was cached
    """
    from_airport = from_airport.upper().strip()
    to_airport = to_airport.upper().strip()

    query_hash = _make_query_hash(
        from_airport, to_airport, date, trip, seat, adults, children, max_stops
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=cache_days)

    cached: Optional[CachedFlightQuery] = (
        CachedFlightQuery.query.filter_by(query_hash=query_hash)
        .filter(CachedFlightQuery.timestamp >= cutoff)
        .order_by(CachedFlightQuery.timestamp.desc())
        .first()
    )

    if cached:
        data = json.loads(cached.results_json)
        data["from_cache"] = True
        data["cached_at"] = cached.timestamp.isoformat()
        return data

    result = _fetch_flights_compat(
        from_airport=from_airport,
        to_airport=to_airport,
        date=date,
        trip=trip,
        seat=seat,
        adults=adults,
        children=children,
        max_stops=max_stops,
    )

    payload = _result_to_payload(result)

    record = CachedFlightQuery(
        query_hash=query_hash,
        from_airport=from_airport,
        to_airport=to_airport,
        date=date,
        trip=trip,
        seat=seat,
        adults=adults,
        children=children,
        max_stops=max_stops,
        timestamp=datetime.now(timezone.utc),
        results_json=json.dumps(payload),
    )
    db.session.add(record)
    db.session.commit()

    payload["from_cache"] = False
    payload["cached_at"] = record.timestamp.isoformat()
    return payload
