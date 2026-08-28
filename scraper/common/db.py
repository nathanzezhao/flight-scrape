"""DB connection helper for the scraper layer.

Reads DATABASE_URL from the environment (e.g. a Vercel Postgres / Neon
connection string). Falls back to a local SQLite file for dev/testing when
DATABASE_URL isn't set, so scrapers can be run and verified without a real
Postgres instance.

CAUTION: the engine here (and the separate one in web/api/_db.py) is cached
at module level per-process. If a long-running process (e.g. a dev server)
already has this file open and you delete+recreate dev_fares.db out from
under it (as "cleanup"), that process's cached connection can keep reading/
writing the old deleted file while a fresh connection elsewhere sees the new
one — writes appear to vanish. Hit this exact bug once during dev. If you
need a clean dev DB, restart whatever process holds it open, don't just rm
the file while it's running.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base, Fare

DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "dev_fares.db")


def get_engine():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Vercel Postgres / Neon give postgres:// URLs; SQLAlchemy 1.4+ wants postgresql://
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        return create_engine(database_url, pool_pre_ping=True)
    return create_engine(f"sqlite:///{os.path.abspath(DEFAULT_SQLITE_PATH)}")


_engine = None
_Session = None


def init_db():
    """Create tables if they don't exist yet. Safe to call every run."""
    global _engine, _Session
    _engine = get_engine()
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine)
    return _engine


def get_session():
    if _Session is None:
        init_db()
    return _Session()


def save_fares(results) -> int:
    """Insert a list of scraper.common.schema.FareResult into the DB. Returns count saved."""
    session = get_session()
    try:
        for r in results:
            session.add(
                Fare(
                    airline_code=r.airline_code,
                    airline_name=r.airline_name,
                    origin=r.origin,
                    destination=r.destination,
                    depart_date=r.depart_date,
                    return_date=r.return_date,
                    price=r.price,
                    currency=r.currency,
                    is_direct=r.is_direct,
                    depart_time=r.depart_time,
                    arrive_time=r.arrive_time,
                    duration_mins=r.duration_mins,
                    stops=r.stops,
                    raw_details=r.raw_details,
                )
            )
        session.commit()
        return len(results)
    finally:
        session.close()
