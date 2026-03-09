from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class FlightQuery(db.Model):
    """Stores each flight search query along with its results for caching."""

    __tablename__ = "flight_queries"

    id = db.Column(db.Integer, primary_key=True)
    query_hash = db.Column(db.String(64), nullable=False, index=True)
    from_airport = db.Column(db.String(3), nullable=False)
    to_airport = db.Column(db.String(3), nullable=False)
    date = db.Column(db.String(10), nullable=False)
    trip = db.Column(db.String(20), nullable=False)
    seat = db.Column(db.String(20), nullable=False)
    adults = db.Column(db.Integer, nullable=False, default=1)
    children = db.Column(db.Integer, nullable=False, default=0)
    max_stops = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    results_json = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return (
            f"<FlightQuery {self.from_airport}->{self.to_airport} "
            f"on {self.date} at {self.timestamp}>"
        )
