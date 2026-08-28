"""Web API for the flight fare comparison site.

Exports `app`, a FastAPI ASGI instance. Primarily reads fares the scraper
layer has already written to the DB, but every fare endpoint below can also
trigger a live scrape itself when there's nothing stored yet.

IMPORTANT — this means these endpoints are no longer purely read-only, and
that has a real deploy consequence: triggering scraper.tripcom.search()
requires a real, visible Chrome browser on the machine running this process
(see scraper/tripcom.py — Trip.com blocks headless Chrome outright). Vercel's
Python serverless functions cannot do that (no persistent browser, no display,
strict execution time limits). So wherever this app actually gets deployed for
production use, it needs to be a persistent host with a real Chrome + display
available (e.g. a machine running under a virtual display, or literally the
same machine the site's user is on) — not Vercel's serverless functions as
originally planned. `web/vercel.json` / the Vercel deploy docs in the repo
predate this feature and need revisiting before an actual Vercel deploy.
"""
import os
import sys
from datetime import datetime, timedelta

# Make the sibling `scraper` package importable regardless of cwd, since the
# on-demand scrape path below needs it. Only relevant for the non-Vercel
# persistent-host deployment this feature now implies (see module docstring).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ._db import fetch_fares, fetch_fares_by_date, fetch_known_airlines, fetch_price_history

app = FastAPI(title="Flight Fare Comparison API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# /api/fares/by-date scrapes one date at a time for every missing day in the
# requested range — bounded here so one request can't trigger dozens of slow
# live scrapes (each can take up to ~60s, see scraper/tripcom.py). Reject
# ranges bigger than this instead of silently truncating, so the caller knows
# to narrow the range rather than getting a partial, confusing result.
MAX_ONDEMAND_RANGE_DAYS = 14


class ScraperUnavailable(Exception):
    """Raised when the on-demand scrape path can't run at all in this
    environment (missing DrissionPage/scraper deps) — distinct from a scrape
    that ran but failed, or one that legitimately found zero flights."""


def _live_scrape(origin: str, destination: str, depart_date) -> list:
    """Trigger a live Trip.com scrape for one route/date and save any results.
    Returns the list of FareResult saved (empty list if the site legitimately
    had zero flights for this search — not an error). Raises ScraperUnavailable
    if the scraper dependencies aren't available in this environment; lets any
    other scrape failure propagate as-is for the caller to handle."""
    try:
        from scraper import tripcom
        from scraper.common.db import init_db, save_fares
        from scraper.common.schema import SearchRequest
    except ImportError as e:
        raise ScraperUnavailable(str(e)) from e

    request = SearchRequest(origin=origin.upper(), destination=destination.upper(), depart_date=depart_date)
    try:
        results = tripcom.search(request)
    except RuntimeError:
        # tripcom.search raises RuntimeError when the site returns zero
        # results — that's a confirmed "no flights", not a scrape failure.
        results = []

    if results:
        init_db()
        save_fares(results)
    return results


def _scraper_unavailable_response(e: ScraperUnavailable) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail=(
            "No stored fares for this search, and on-demand scraping isn't "
            f"available in this environment (missing dependency: {e}). This "
            "endpoint needs to run somewhere with DrissionPage + a real Chrome "
            "browser installed — see module docstring."
        ),
    )


@app.get("/api/airlines")
def list_airlines():
    """Airlines actually seen in scraped data so far (not a fixed list —
    Trip.com can surface more airlines than any static registry would)."""
    return fetch_known_airlines()


@app.get("/api/fares")
def get_fares(
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
    depart_date: str = Query(..., description="YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    airlines: str = Query(None, description="Comma-separated airline codes, e.g. CZ,MF,AC"),
):
    airline_list = [a.strip().upper() for a in airlines.split(",")] if airlines else None
    try:
        rows = fetch_fares(origin, destination, depart_date, airline_list)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    scraped_now = False
    if not rows:
        try:
            _live_scrape(origin, destination, datetime.strptime(depart_date, "%Y-%m-%d").date())
        except ScraperUnavailable as e:
            raise _scraper_unavailable_response(e)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"No stored fares for this search, and the live scrape failed: {e}",
            )
        rows = fetch_fares(origin, destination, depart_date, airline_list)
        scraped_now = True

    return {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "depart_date": depart_date,
        "fares": rows,
        "scraped_now": scraped_now,
    }


@app.get("/api/fares/by-date")
def get_fares_by_date(
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
    start_date: str = Query(..., description="YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., description="YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Price trend by departure date ("which day is cheapest"). Scrapes live
    for every date in the range that has no stored fares yet — this can mean
    several sequential live scrapes (each up to ~60s), so the range is capped
    at MAX_ONDEMAND_RANGE_DAYS to keep a single request bounded."""
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date")

    start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
    span_days = (end_d - start_d).days + 1
    if span_days > MAX_ONDEMAND_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Range too large ({span_days} days) — live-scraping each missing date "
                f"is capped at {MAX_ONDEMAND_RANGE_DAYS} days per request. Narrow the "
                "range, or run it again for the next stretch of dates."
            ),
        )

    rows = fetch_fares_by_date(origin, destination, start_date, end_date)
    covered_dates = {r["depart_date"] for r in rows}

    scraped_now = False
    d = start_d
    while d <= end_d:
        if d.isoformat() not in covered_dates:
            try:
                _live_scrape(origin, destination, d)
                scraped_now = True
            except ScraperUnavailable as e:
                raise _scraper_unavailable_response(e)
            except Exception:
                # Best-effort across the range: one date's scrape failing
                # (site hiccup, transient block, etc.) shouldn't blank out
                # the rest of an otherwise-successful range.
                pass
        d += timedelta(days=1)

    if scraped_now:
        rows = fetch_fares_by_date(origin, destination, start_date, end_date)

    return {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "start_date": start_date,
        "end_date": end_date,
        "fares": rows,
        "scraped_now": scraped_now,
    }


@app.get("/api/fares/history")
def get_fares_history(
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
    depart_date: str = Query(..., description="YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Price trend over scrape time for one fixed departure date ("should I
    book now or wait"). If there's no history yet, does one live scrape to
    seed the first data point — repeated visits over following days/weeks
    (each also scraping if there's nothing newer) are what build the trend."""
    rows = fetch_price_history(origin, destination, depart_date)

    scraped_now = False
    if not rows:
        try:
            _live_scrape(origin, destination, datetime.strptime(depart_date, "%Y-%m-%d").date())
        except ScraperUnavailable as e:
            raise _scraper_unavailable_response(e)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"No price history for this route/date, and the live scrape failed: {e}",
            )
        rows = fetch_price_history(origin, destination, depart_date)
        scraped_now = True

    return {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "depart_date": depart_date,
        "history": rows,
        "scraped_now": scraped_now,
    }
