"""One-off seed script for E2E testing — NOT part of the app, just fixtures.
Populates dev_fares.db with deterministic data covering multiple routes
(including the newly-added SIN/TSN destinations), multiple departure dates
per route (for the by-date chart), and multiple scrape timestamps for one
route/date (for the history chart). Safe to delete after testing.
"""
import sys
sys.path.insert(0, "/Users/fenghan/webscrapeflight")

from datetime import date, datetime, timedelta
from scraper.common.db import init_db, get_session
from scraper.common.models import Fare

init_db()
s = get_session()

ROUTES = [
    ("YVR", "PVG"), ("YVR", "SIN"), ("YVR", "TSN"),
    ("YYZ", "ICN"), ("YYZ", "HKG"),
    ("YYC", "BKK"), ("YUL", "CAN"), ("YHZ", "NRT"),
]
AIRLINES = [
    ("AC", "Air Canada"), ("MU", "China Eastern"), ("CZ", "China Southern"),
    ("CA", "Air China"), ("CX", "Cathay Pacific"), ("KE", "Korean Air"),
]

base_date = date(2026, 10, 1)
count = 0

# Multi-route, multi-airline single-date snapshots (for /api/fares tests)
for oi, (o, d) in enumerate(ROUTES):
    dep = base_date + timedelta(days=oi)
    for ai, (code, name) in enumerate(AIRLINES):
        s.add(Fare(
            airline_code=code, airline_name=name, origin=o, destination=d,
            depart_date=dep, price=500 + oi * 37 + ai * 21, currency="CAD",
            is_direct=(ai % 2 == 0), stops=0 if ai % 2 == 0 else 1,
            depart_time="10:00", arrive_time="18:00",
            duration_mins=600 + ai * 15,
        ))
        count += 1

# Multi-date range for YVR-PVG (for /api/fares/by-date tests) — CONTIGUOUS
# dates on purpose: a gap would make a full-range query trigger a real live
# scrape for the missing day (on-demand scraping is now the default for
# every fare endpoint), which is not what a "fast, seeded-data" test wants.
for i in range(6):
    dep = date(2026, 11, 1) + timedelta(days=i)
    for code, name in AIRLINES[:3]:
        s.add(Fare(
            airline_code=code, airline_name=name, origin="YVR", destination="PVG",
            depart_date=dep, price=600 + i * 12, currency="CAD",
            is_direct=True, stops=0,
        ))
        count += 1

# Multi-scrape-time history for one fixed route/date (for /api/fares/history tests)
fixed_dep = date(2026, 12, 15)
now = datetime(2026, 8, 1, 9, 0, 0)
for i in range(8):
    ts = now + timedelta(days=i)
    for code, name in AIRLINES[:4]:
        s.add(Fare(
            airline_code=code, airline_name=name, origin="YYZ", destination="ICN",
            depart_date=fixed_dep, price=900 - i * 5, currency="CAD",
            is_direct=(code == "AC"), stops=0 if code == "AC" else 1,
            scraped_at=ts,
        ))
        count += 1

# Duplicate-scraped-airline case (dedup correctness): same airline, same
# route/date, two different scraped_at, different prices — latest must win.
tie_dep = date(2026, 10, 20)
s.add(Fare(airline_code="AC", airline_name="Air Canada", origin="YUL", destination="CAN",
           depart_date=tie_dep, price=999.00, currency="CAD", is_direct=True, stops=0,
           scraped_at=datetime(2026, 8, 1, 8, 0, 0)))
s.add(Fare(airline_code="AC", airline_name="Air Canada", origin="YUL", destination="CAN",
           depart_date=tie_dep, price=777.00, currency="CAD", is_direct=True, stops=0,
           scraped_at=datetime(2026, 8, 2, 8, 0, 0)))
count += 2

s.commit()
s.close()
print(f"Seeded {count} fare rows.")
