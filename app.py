import os
import re

from flask import Flask, jsonify, redirect, render_template, request, url_for

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
                "from_airport": request.form.get("from_airport", "").strip().upper(),
                "to_airport": request.form.get("to_airport", "").strip().upper(),
                "date": request.form.get("date", ""),
                "trip": request.form.get("trip", "one-way"),
                "seat": request.form.get("seat", "economy"),
                "adults": int(request.form.get("adults", 1)),
                "children": int(request.form.get("children", 0)),
                "max_stops": _optional_int(request.form.get("max_stops")),
                "cache_days": int(
                    request.form.get("cache_days", app.config["CACHE_DAYS"])
                ),
            }
            return redirect(url_for("search", **params))

        # GET – run the search (or show the form if params are missing)
        from_airport = request.args.get("from_airport", "").strip().upper()
        to_airport = request.args.get("to_airport", "").strip().upper()
        date = request.args.get("date", "")

        if not (from_airport and to_airport and date):
            return redirect(url_for("index"))

        trip = request.args.get("trip", "one-way")
        seat = request.args.get("seat", "economy")
        adults = int(request.args.get("adults", 1))
        children = int(request.args.get("children", 0))
        max_stops = _optional_int(request.args.get("max_stops"))
        cache_days = int(request.args.get("cache_days", app.config["CACHE_DAYS"]))

        error = None
        result = None
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
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        return render_template(
            "results.html",
            result=result,
            error=error,
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

    @app.route("/api/search")
    def api_search():
        """JSON endpoint – same parameters as the GET /search route."""
        from_airport = request.args.get("from_airport", "").strip().upper()
        to_airport = request.args.get("to_airport", "").strip().upper()
        date = request.args.get("date", "")

        if not (from_airport and to_airport and date):
            return jsonify({"error": "from_airport, to_airport and date are required"}), 400

        trip = request.args.get("trip", "one-way")
        seat = request.args.get("seat", "economy")
        adults = int(request.args.get("adults", 1))
        children = int(request.args.get("children", 0))
        max_stops = _optional_int(request.args.get("max_stops"))
        cache_days = int(request.args.get("cache_days", app.config["CACHE_DAYS"]))

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
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 500

        return jsonify(result)

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


# ------------------------------------------------------------------ #
#  Entry-point                                                         #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug)
