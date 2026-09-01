"""Read-only DB access for the Vercel API layer.

Uses SQLAlchemy (dialect-agnostic) rather than raw psycopg2 so this can also be
run locally against the dev SQLite fallback the scraper layer uses, without
needing a real Postgres instance for local testing. In production on Vercel,
set DATABASE_URL (Vercel Postgres / Neon integration sets this automatically).
"""
import json
import os

from sqlalchemy import bindparam, create_engine, text

_DEV_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "dev_fares.db")

_engine = None


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        _engine = create_engine(database_url, pool_pre_ping=True)
    else:
        _engine = create_engine(f"sqlite:///{os.path.abspath(_DEV_SQLITE_PATH)}")
    # Ensure the `fares` table exists before any query runs — on a brand new
    # DB (nothing scraped yet), querying a nonexistent table raised an
    # unhandled 500 instead of a clean empty result. create_all() is a no-op
    # if the table already exists (checkfirst=True by default).
    from scraper.common.models import Base

    Base.metadata.create_all(_engine)
    return _engine


def fetch_known_airlines() -> list:
    """Distinct (code, name) pairs ever scraped — the source (Trip.com) can surface
    more airlines than any fixed list would, so this is derived from real data
    rather than hardcoded."""
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT airline_code, airline_name FROM fares ORDER BY airline_name")
        )
        return [{"code": r[0], "name": r[1]} for r in rows]


def fetch_scraped_airline_codes(origin: str, destination: str, depart_date: str) -> set:
    """Which airline codes have ever been scraped for this exact route/date —
    used to decide whether a *specific* requested airline needs a fresh
    scrape, as opposed to "does any data at all exist for this route/date"
    (the latter incorrectly skips scraping a genuinely new airline once any
    other airline has been cached — see web/api/index.py's needs_scrape)."""
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT airline_code FROM fares "
                "WHERE origin = :origin AND destination = :destination AND depart_date = :depart_date"
            ),
            {"origin": origin.upper(), "destination": destination.upper(), "depart_date": depart_date},
        )
        return {r[0] for r in rows}


def fetch_fares(origin: str, destination: str, depart_date: str, airlines=None) -> list:
    """Latest fare per airline for a given route/date, cheapest first."""
    query = """
        SELECT id, airline_code, airline_name, origin, destination, depart_date, return_date,
               price, currency, is_direct, depart_time, arrive_time, duration_mins, stops,
               raw_details, scraped_at
        FROM fares
        WHERE origin = :origin AND destination = :destination AND depart_date = :depart_date
    """
    params = {"origin": origin.upper(), "destination": destination.upper(), "depart_date": depart_date}
    if airlines:
        query += " AND airline_code IN :airlines"
        params["airlines"] = tuple(airlines)
    # Tie-break on id when scraped_at is identical (e.g. a batch scrape run
    # that stamps every row with the same timestamp) so "most recent" is
    # deterministic instead of depending on unspecified row-return order.
    query += " ORDER BY scraped_at DESC, id DESC"

    engine = _get_engine()
    with engine.connect() as conn:
        stmt = text(query)
        if airlines:
            # IN with a tuple parameter needs an "expanding" bindparam so
            # SQLAlchemy expands it into (?, ?, ...) placeholders at execute
            # time; without this, binding a tuple to a plain ":airlines"
            # placeholder raises an OperationalError on every call.
            stmt = stmt.bindparams(bindparam("airlines", expanding=True))
        rows = [dict(r._mapping) for r in conn.execute(stmt, params)]

    # Keep only the most recently scraped row per airline (rows are already
    # ordered scraped_at DESC, so the first occurrence per airline wins).
    latest_by_airline = {}
    for r in rows:
        latest_by_airline.setdefault(r["airline_code"], r)
    result = list(latest_by_airline.values())
    result.sort(key=lambda r: float(r["price"]))

    for r in result:
        r.pop("id", None)
        r["price"] = float(r["price"])
        for k in ("depart_date", "return_date", "scraped_at"):
            v = r.get(k)
            if v is not None and hasattr(v, "isoformat"):
                r[k] = v.isoformat()
        # raw_details is a JSON column, but this query goes through raw text()
        # SQL rather than the ORM, so it isn't auto-decoded — the dev SQLite
        # fallback (JSON stored as TEXT, no driver-side JSON support) comes
        # back as a plain string, while Postgres/psycopg2 already hands back
        # a parsed dict. Normalize so the frontend always gets an object.
        raw = r.get("raw_details")
        if isinstance(raw, str):
            try:
                r["raw_details"] = json.loads(raw)
            except (TypeError, ValueError):
                r["raw_details"] = None
    return result


def fetch_fares_by_date(origin: str, destination: str, start_date: str, end_date: str, airlines=None) -> list:
    """Latest fare per (airline, depart_date) across a date range — one point
    per airline per day, for a "price by departure date" trend chart. Only
    returns dates that have actually been scraped; does not fill gaps."""
    query = """
        SELECT id, airline_code, airline_name, depart_date, price, currency, scraped_at
        FROM fares
        WHERE origin = :origin AND destination = :destination
          AND depart_date BETWEEN :start_date AND :end_date
    """
    params = {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "start_date": start_date,
        "end_date": end_date,
    }
    if airlines:
        query += " AND airline_code IN :airlines"
        params["airlines"] = tuple(airlines)
    query += " ORDER BY scraped_at DESC, id DESC"

    engine = _get_engine()
    with engine.connect() as conn:
        stmt = text(query)
        if airlines:
            # See fetch_fares() for why this needs an expanding bindparam.
            stmt = stmt.bindparams(bindparam("airlines", expanding=True))
        rows = [dict(r._mapping) for r in conn.execute(stmt, params)]

    # Keep only the most recently scraped row per (airline, depart_date) pair
    # — same tiebreak logic as fetch_fares, just grouped one level finer.
    latest = {}
    for r in rows:
        key = (r["airline_code"], str(r["depart_date"]))
        latest.setdefault(key, r)
    result = list(latest.values())
    result.sort(key=lambda r: (str(r["depart_date"]), r["airline_code"]))

    for r in result:
        r.pop("id", None)
        r["price"] = float(r["price"])
        for k in ("depart_date", "scraped_at"):
            v = r.get(k)
            if v is not None and hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return result


def fetch_price_history(origin: str, destination: str, depart_date: str, airlines=None) -> list:
    """Every historical scrape for one exact route+date, per airline, ordered
    by scrape time — for a "price by scrape date" tracking chart. Deliberately
    NOT deduped (unlike fetch_fares/fetch_fares_by_date) since the whole point
    here is to show every price observation over time, not just the latest."""
    query = """
        SELECT airline_code, airline_name, price, currency, scraped_at
        FROM fares
        WHERE origin = :origin AND destination = :destination AND depart_date = :depart_date
    """
    params = {"origin": origin.upper(), "destination": destination.upper(), "depart_date": depart_date}
    if airlines:
        query += " AND airline_code IN :airlines"
        params["airlines"] = tuple(airlines)
    query += " ORDER BY airline_code, scraped_at"

    engine = _get_engine()
    with engine.connect() as conn:
        stmt = text(query)
        if airlines:
            stmt = stmt.bindparams(bindparam("airlines", expanding=True))
        rows = [dict(r._mapping) for r in conn.execute(stmt, params)]

    for r in rows:
        r["price"] = float(r["price"])
        if hasattr(r["scraped_at"], "isoformat"):
            r["scraped_at"] = r["scraped_at"].isoformat()
    return rows
