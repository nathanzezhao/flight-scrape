#!/usr/bin/env python3
"""CLI to scrape Trip.com for a route/date and store the results.

Usage:
    python3 run_scrapers.py --origin YVR --destination PVG --depart-date 2026-09-26

Reads DATABASE_URL from the environment (or a local .env file) for the target
Postgres DB; falls back to a local SQLite file (dev_fares.db) if unset, so this
can be exercised without a real Postgres instance.

Intended to run on a schedule (cron on Mac/Linux, Task Scheduler on Windows) on
a real machine with a real, visible Chrome window (see scraper/tripcom.py
docstring — headless mode is blocked by the site). Note: the web API
(web/api/index.py) can also trigger a scrape on demand now when a search finds
no stored data — see its "on-demand scrape" note for why that changes where
the web layer can be hosted.
"""
import argparse
from datetime import datetime

from dotenv import load_dotenv

from scraper import tripcom
from scraper.common.db import init_db, save_fares
from scraper.common.schema import SearchRequest

load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape Trip.com for flight fares and store results.")
    parser.add_argument("--origin", required=True, help="Origin IATA code, e.g. YVR")
    parser.add_argument("--destination", required=True, help="Destination IATA code, e.g. PVG")
    parser.add_argument("--depart-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--currency", default="CAD")
    parser.add_argument("--headless", action="store_true", help="Not recommended — Trip.com blocks headless Chrome.")
    return parser.parse_args()


def main():
    args = parse_args()
    request = SearchRequest(
        origin=args.origin.upper(),
        destination=args.destination.upper(),
        depart_date=datetime.strptime(args.depart_date, "%Y-%m-%d").date(),
    )

    print(f"Searching Trip.com: {request.origin}->{request.destination} on {request.depart_date} ...")
    results = tripcom.search(request, currency=args.currency, headless=args.headless)
    print(f"Found {len(results)} fare(s).")

    init_db()
    saved = save_fares(results)
    print(f"Saved {saved} fare(s) to the database.")


if __name__ == "__main__":
    main()
