import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fast_flights import FlightData, Passengers, get_flights

from models import FlightQuery, db

CACHE_DAYS_DEFAULT = 1


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


def _flight_to_dict(flight) -> dict:
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

    cached: Optional[FlightQuery] = (
        FlightQuery.query.filter_by(query_hash=query_hash)
        .filter(FlightQuery.timestamp >= cutoff)
        .order_by(FlightQuery.timestamp.desc())
        .first()
    )

    if cached:
        data = json.loads(cached.results_json)
        data["from_cache"] = True
        data["cached_at"] = cached.timestamp.isoformat()
        return data

    result = get_flights(
        flight_data=[
            FlightData(
                date=date,
                from_airport=from_airport,
                to_airport=to_airport,
                max_stops=max_stops,
            )
        ],
        trip=trip,
        passengers=Passengers(adults=adults, children=children),
        seat=seat,
    )

    flights_list = [_flight_to_dict(f) for f in result.flights]
    payload = {
        "flights": flights_list,
        "current_price": result.current_price,
    }

    record = FlightQuery(
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
