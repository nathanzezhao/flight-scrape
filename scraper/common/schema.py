"""Common types shared across the scraper layer (currently just scraper/tripcom.py)."""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class SearchRequest:
    origin: str            # IATA code, e.g. 'YVR'
    destination: str       # IATA code, e.g. 'PVG'
    depart_date: date
    return_date: Optional[date] = None
    direct_only: bool = False


@dataclass
class FareResult:
    """One flight option returned by a scraper, in a common shape across airlines.

    airline_code/airline_name are whatever the source (Trip.com) reports for
    that flight — not restricted to a fixed registry, since an aggregator can
    surface airlines beyond whatever list was originally scoped.
    """
    airline_code: str
    airline_name: str
    origin: str
    destination: str
    depart_date: date
    price: float
    currency: str = "CAD"
    return_date: Optional[date] = None
    is_direct: bool = True
    depart_time: Optional[str] = None
    arrive_time: Optional[str] = None
    duration_mins: Optional[int] = None
    stops: int = 0
    raw_details: dict = field(default_factory=dict)
