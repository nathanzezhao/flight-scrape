"""SQLAlchemy model for the shared `fares` table.

Works against both Postgres (production, via DATABASE_URL) and local SQLite
(dev/testing, default) so the scraper layer can be exercised without a real
Postgres instance. Schema matches db/schema.sql.
"""
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Fare(Base):
    __tablename__ = "fares"

    id = Column(Integer, primary_key=True, autoincrement=True)
    airline_code = Column(String, nullable=False)
    airline_name = Column(String, nullable=False)
    origin = Column(String, nullable=False)
    destination = Column(String, nullable=False)
    depart_date = Column(Date, nullable=False)
    return_date = Column(Date, nullable=True)
    price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String, nullable=False, default="CAD")
    is_direct = Column(Boolean, nullable=False)
    depart_time = Column(String, nullable=True)
    arrive_time = Column(String, nullable=True)
    duration_mins = Column(Integer, nullable=True)
    stops = Column(Integer, nullable=False, default=0)
    raw_details = Column(JSON, nullable=True)
    scraped_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "airline_code": self.airline_code,
            "airline_name": self.airline_name,
            "origin": self.origin,
            "destination": self.destination,
            "depart_date": self.depart_date.isoformat() if isinstance(self.depart_date, date) else self.depart_date,
            "return_date": self.return_date.isoformat() if isinstance(self.return_date, date) else self.return_date,
            "price": float(self.price),
            "currency": self.currency,
            "is_direct": self.is_direct,
            "depart_time": self.depart_time,
            "arrive_time": self.arrive_time,
            "duration_mins": self.duration_mins,
            "stops": self.stops,
            "scraped_at": self.scraped_at.isoformat() if isinstance(self.scraped_at, datetime) else self.scraped_at,
        }
