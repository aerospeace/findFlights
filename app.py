import os
import re
from csv import DictWriter
from datetime import date as dt_date, datetime, timedelta
from io import StringIO

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from flights_service import search_flights
from models import db

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)

    app.config.setdefault(
        "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{os.path.join(BASE_DIR, 'findflights.db')}",
    )
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("CACHE_DAYS", 1)
    app.config.setdefault("SECRET_KEY", os.environ.get("SECRET_KEY", "change-me-in-prod"))

    if config:
        app.config.update(config)

    db.init_app(app)

    @app.template_filter("numeric_price")
    def numeric_price_filter(value: str) -> str:
        """Strip everything except digits and decimal points from a price string."""
        return re.sub(r"[^0-9.]", "", value or "")

    with app.app_context():
        db.create_all()

    # ------------------------------------------------------------------ #
    #  Routes                                                              #
    # ------------------------------------------------------------------ #

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/search", methods=["GET", "POST"])
    def search():
        if request.method == "POST":
            params = {
                "from_airports": request.form.get("from_airports", "").strip().upper(),
                "to_airports": request.form.get("to_airports", "").strip().upper(),
                "date_from": request.form.get("date_from", ""),
                "date_to": request.form.get("date_to", ""),
                "trip": request.form.get("trip", "one-way"),
                "seat": request.form.get("seat", "economy"),
                "adults": int(request.form.get("adults", 1)),
                "children": int(request.form.get("children", 0)),
                "max_stops": _optional_int(request.form.get("max_stops")),
                "cache_days": int(request.form.get("cache_days", app.config["CACHE_DAYS"])),
            }
            return redirect(url_for("search", **params))

        from_airports_text = request.args.get("from_airports", "").strip().upper()
        to_airports_text = request.args.get("to_airports", "").strip().upper()
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", "")

        if not (from_airports_text and to_airports_text and date_from):
            return redirect(url_for("index"))

        trip = request.args.get("trip", "one-way")
        seat = request.args.get("seat", "economy")
        adults = int(request.args.get("adults", 1))
        children = int(request.args.get("children", 0))
        max_stops = _optional_int(request.args.get("max_stops"))
        cache_days = int(request.args.get("cache_days", app.config["CACHE_DAYS"]))

        error = None
        results: list[dict] = []
        csv_url = None

        try:
            from_airports = _parse_airports(from_airports_text)
            to_airports = _parse_airports(to_airports_text)
            dates = _date_range(date_from, date_to or date_from)

            for from_airport in from_airports:
                for to_airport in to_airports:
                    for date in dates:
                        try:
                            result = search_flights(
                                from_airport=from_airport,
                                to_airport=to_airport,
                                date=date,
                                trip=trip,
                                seat=seat,
                                adults=adults,
                                children=children,
                                max_stops=max_stops,
                                cache_days=cache_days,
                            )
                            results.append(
                                {
                                    "from_airport": from_airport,
                                    "to_airport": to_airport,
                                    "date": date,
                                    "result": result,
                                    "error": None,
                                }
                            )
                        except Exception as exc:  # noqa: BLE001
                            results.append(
                                {
                                    "from_airport": from_airport,
                                    "to_airport": to_airport,
                                    "date": date,
                                    "result": None,
                                    "error": str(exc),
                                }
                            )

            if results:
                csv_url = url_for(
                    "download_csv",
                    from_airports=from_airports_text,
                    to_airports=to_airports_text,
                    date_from=date_from,
                    date_to=date_to or date_from,
                    trip=trip,
                    seat=seat,
                    adults=adults,
                    children=children,
                    max_stops=max_stops,
                    cache_days=cache_days,
                )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        return render_template(
            "results.html",
            results=results,
            error=error,
            from_airports_text=from_airports_text,
            to_airports_text=to_airports_text,
            date_from=date_from,
            date_to=date_to or date_from,
            trip=trip,
            seat=seat,
            adults=adults,
            children=children,
            max_stops=max_stops,
            cache_days=cache_days,
            csv_url=csv_url,
        )

    @app.route("/search.csv")
    def download_csv():
        from_airports = _parse_airports(request.args.get("from_airports", ""))
        to_airports = _parse_airports(request.args.get("to_airports", ""))
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", date_from)
        dates = _date_range(date_from, date_to)

        trip = request.args.get("trip", "one-way")
        seat = request.args.get("seat", "economy")
        adults = int(request.args.get("adults", 1))
        children = int(request.args.get("children", 0))
        max_stops = _optional_int(request.args.get("max_stops"))
        cache_days = int(request.args.get("cache_days", app.config["CACHE_DAYS"]))

        buffer = StringIO()
        fieldnames = [
            "search_date",
            "from_airport",
            "to_airport",
            "airline",
            "departure",
            "arrival",
            "duration",
            "stops",
            "delay",
            "price",
            "is_best",
            "current_price",
            "from_cache",
            "cached_at",
            "status",
            "error_message",
            "no_results",
        ]
        writer = DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()

        for from_airport in from_airports:
            for to_airport in to_airports:
                for flight_date in dates:
                    try:
                        result = search_flights(
                            from_airport=from_airport,
                            to_airport=to_airport,
                            date=flight_date,
                            trip=trip,
                            seat=seat,
                            adults=adults,
                            children=children,
                            max_stops=max_stops,
                            cache_days=cache_days,
                        )
                    except Exception as exc:  # noqa: BLE001
                        writer.writerow(
                            {
                                "search_date": flight_date,
                                "from_airport": from_airport,
                                "to_airport": to_airport,
                                "status": "error",
                                "error_message": str(exc),
                            }
                        )
                        continue

                    if not result["flights"]:
                        writer.writerow(
                            {
                                "search_date": flight_date,
                                "from_airport": from_airport,
                                "to_airport": to_airport,
                                "current_price": result["current_price"] or "",
                                "from_cache": result["from_cache"],
                                "cached_at": result["cached_at"],
                                "status": "ok",
                                "error_message": "",
                                "no_results": "true",
                            }
                        )
                        continue

                    for flight in result["flights"]:
                        writer.writerow(
                            {
                                "search_date": flight_date,
                                "from_airport": from_airport,
                                "to_airport": to_airport,
                                "airline": flight["name"],
                                "departure": _excel_datetime(flight["departure"], flight_date),
                                "arrival": _excel_datetime(flight["arrival"], flight_date),
                                "duration": _excel_duration(flight["duration"]),
                                "stops": flight["stops"],
                                "delay": flight["delay"] or "",
                                "price": _normalized_price(flight["price"]),
                                "is_best": flight["is_best"],
                                "current_price": result["current_price"],
                                "from_cache": result["from_cache"],
                                "cached_at": result["cached_at"],
                                "status": "ok",
                                "error_message": "",
                                "no_results": "false",
                            }
                        )

        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=flight_results.csv"},
        )

    @app.route("/api/search")
    def api_search():
        """JSON endpoint – same parameters as the GET /search route."""
        from_airports = _parse_airports(request.args.get("from_airports", ""))
        to_airports = _parse_airports(request.args.get("to_airports", ""))
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", date_from)
        if not (from_airports and to_airports and date_from):
            return jsonify({"error": "from_airports, to_airports and date_from are required"}), 400
        dates = _date_range(date_from, date_to)

        trip = request.args.get("trip", "one-way")
        seat = request.args.get("seat", "economy")
        adults = int(request.args.get("adults", 1))
        children = int(request.args.get("children", 0))
        max_stops = _optional_int(request.args.get("max_stops"))
        cache_days = int(request.args.get("cache_days", app.config["CACHE_DAYS"]))

        results = []
        for from_airport in from_airports:
            for to_airport in to_airports:
                for date in dates:
                    try:
                        result = search_flights(
                            from_airport=from_airport,
                            to_airport=to_airport,
                            date=date,
                            trip=trip,
                            seat=seat,
                            adults=adults,
                            children=children,
                            max_stops=max_stops,
                            cache_days=cache_days,
                        )
                        results.append(
                            {
                                "from_airport": from_airport,
                                "to_airport": to_airport,
                                "date": date,
                                "result": result,
                                "error": None,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        results.append(
                            {
                                "from_airport": from_airport,
                                "to_airport": to_airport,
                                "date": date,
                                "result": None,
                                "error": str(exc),
                            }
                        )

        return jsonify({"results": results})

    return app


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "" or value == "any":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_airports(value: str) -> list[str]:
    airports = [code.strip().upper() for code in value.split(",") if code.strip()]
    unique: list[str] = []
    for code in airports:
        if re.fullmatch(r"[A-Z]{3}", code) and code not in unique:
            unique.append(code)
    return unique


def _date_range(start: str, end: str) -> list[str]:
    start_date = dt_date.fromisoformat(start)
    end_date = dt_date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError("date_to must be on or after date_from")
    days = (end_date - start_date).days
    return [(start_date + timedelta(days=offset)).isoformat() for offset in range(days + 1)]


def _normalized_price(value: str) -> str:
    return re.sub(r"^[^0-9-]+", "", (value or "").strip())


def _excel_datetime(value: str, fallback_date: str) -> str:
    cleaned = (value or "").strip()
    match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)\s+on\s+\w{3},\s+(\w{3})\s+(\d{1,2})", cleaned)
    if not match:
        return cleaned

    time_part, month_part, day_part = match.groups()
    fallback = dt_date.fromisoformat(fallback_date)
    parsed = datetime.strptime(
        f"{fallback.year} {month_part} {day_part} {time_part.upper()}",
        "%Y %b %d %I:%M %p",
    )
    return parsed.strftime("%Y-%m-%d %H:%M")


def _excel_duration(value: str) -> str:
    cleaned = (value or "").strip().lower()
    hour_match = re.search(r"(\d+)\s*h(?:r|our)?", cleaned)
    minute_match = re.search(r"(\d+)\s*m(?:in)?", cleaned)

    if not hour_match and not minute_match:
        return value

    hours = int(hour_match.group(1)) if hour_match else 0
    minutes = int(minute_match.group(1)) if minute_match else 0
    return f"{hours:02d}:{minutes:02d}"


# ------------------------------------------------------------------ #
#  Entry-point                                                         #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug)
