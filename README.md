# findFlights

A web application to search and compare flight prices powered by
[fast-flights](https://github.com/AWeirdDev/flights) (Google Flights scraper).

## Features

- **Search** flights by origin/destination, date, cabin class, trip type, and passenger count
- **Caching** – search results are stored in a SQLite database; identical queries within a
  configurable window (default: 1 day) are served from the cache instead of hitting the
  upstream scraper
- **Filters** – narrow results by airline, number of stops, maximum price, and maximum
  flight duration
- **JSON API** – a `/api/search` endpoint returns the same data in JSON format

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the development server
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## Project structure

```
findFlights/
├── app.py               # Flask application factory & routes
├── models.py            # SQLAlchemy model for cached queries
├── flights_service.py   # Search logic with cache layer
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── index.html       # Search form
│   └── results.html     # Results page with client-side filters
├── static/
│   └── css/style.css
└── tests/
    └── test_flights_service.py
```

## Configuration

| Config key | Default | Description |
|---|---|---|
| `SQLALCHEMY_DATABASE_URI` | `sqlite:///findflights.db` | Database connection string |
| `CACHE_DAYS` | `1` | Cache TTL in days (0 = always fetch live data) |
| `SECRET_KEY` | `change-me-in-prod` | Flask secret key |

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```
