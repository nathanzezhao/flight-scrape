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
import time
from datetime import datetime, timedelta

# Make the sibling `scraper` package importable regardless of cwd, since the
# on-demand scrape path below needs it. Only relevant for the non-Vercel
# persistent-host deployment this feature now implies (see module docstring).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from scraper.common.airlines import PRIORITY_AIRLINE_CODES

from ._db import (
    fetch_fares,
    fetch_fares_by_date,
    fetch_known_airlines,
    fetch_price_history,
    fetch_scraped_airline_codes,
)

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

# get_fares_by_date() scrapes one date at a time in a loop — each date's own
# scraper.tripcom.search() call can itself do up to 13 reloads (one per
# priority airline, see scraper/tripcom.py's EXTRA_PASS_BATCH_* constants),
# so a wide date range compounds that into a very large number of Trip.com
# hits in one continuous request. Confirmed live (2026-08-30) this can
# trigger Trip.com's bot challenge even with tripcom.py's own per-airline
# pacing in place. Same fix applied here at the date level: a cooldown every
# few actual scrapes (not every loop iteration — a date that's already
# covered and gets skipped doesn't count) so a many-date request is spread
# out over time too, not just each individual date's internal reloads.
DATE_BATCH_SIZE = 3
DATE_BATCH_COOLDOWN_SECONDS = 30


class ScraperUnavailable(Exception):
    """Raised when the on-demand scrape path can't run at all in this
    environment (missing DrissionPage/scraper deps) — distinct from a scrape
    that ran but failed, or one that legitimately found zero flights."""


def _live_scrape(origin: str, destination: str, depart_date, priority_codes=None) -> list:
    """Trigger a live Trip.com scrape for one route/date and save any results.
    Returns the list of FareResult saved (empty list if the site legitimately
    had zero flights for this search — not an error). Raises ScraperUnavailable
    if the scraper dependencies aren't available in this environment; lets any
    other scrape failure propagate as-is for the caller to handle.

    `priority_codes`, when given, limits the extra per-airline pass Trip.com's
    scraper does beyond its default view (see scraper/tripcom.py) to just
    those codes instead of the full 13-airline default — the main lever for
    cutting scrape time when the caller only cares about a few airlines."""
    try:
        from scraper import tripcom
        from scraper.common.db import init_db, save_fares
        from scraper.common.schema import SearchRequest
    except ImportError as e:
        raise ScraperUnavailable(str(e)) from e

    request = SearchRequest(origin=origin.upper(), destination=destination.upper(), depart_date=depart_date)
    try:
        results = tripcom.search(request, priority_codes=priority_codes)
    except RuntimeError:
        # tripcom.search raises RuntimeError when the site returns zero
        # results — that's a confirmed "no flights", not a scrape failure.
        results = []

    if results:
        init_db()
        save_fares(results)
    return results


def _parse_date(value: str, field_name: str):
    """Parse a YYYY-MM-DD string into a date, raising a clean 422 for a
    calendar-invalid value (e.g. 2026-13-45) rather than letting it surface
    later as a confusing 5xx (a raw ValueError from a bad date used to slip
    past the query-param regex, which only checks shape, not validity, and
    get caught downstream as a bogus "live scrape failed" error)."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid {field_name}: {value!r} is not a real calendar date")


def _uncovered_airlines(airline_list, scraped_codes: set) -> bool:
    """Whether at least one *recognized* requested airline still needs
    scraping for this route/date. "Recognized" means it's one of this
    project's tracked airlines (PRIORITY_AIRLINE_CODES) — an unrecognized
    code (a typo like "ZZ", or just not one of the 13 tracked airlines) is
    never treated as needing a scrape on its own, preserving the original
    `airlines=ZZ` fix (don't waste a scrape chasing a code that was never
    going to have data). A recognized code not yet in `scraped_codes` (e.g.
    AC, after only CX had ever been scraped for this route/date) does need
    one — this is the part the original fix over-corrected and broke."""
    if airline_list is None:
        return not scraped_codes
    recognized = [c for c in airline_list if c in PRIORITY_AIRLINE_CODES]
    return not set(recognized).issubset(scraped_codes)


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
    depart_date_obj = _parse_date(depart_date, "depart_date")
    airline_list = [a.strip().upper() for a in airlines.split(",")] if airlines else None
    try:
        rows = fetch_fares(origin, destination, depart_date, airline_list)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Only scrape when the requested airline(s) genuinely aren't covered yet
    # — not just when *some* data exists for this route/date at all. Checks
    # the actual set of airline codes already scraped rather than "is there
    # any row at all," so a genuinely new airline (e.g. AC, after only CX had
    # ever been scraped for this route/date) correctly triggers a fresh
    # scrape instead of being silently skipped. Still avoids wastefully
    # rescraping for a typo'd/nonexistent code once real data exists — found
    # live: `airlines=ZZ` on a route/date with real data for other airlines
    # was triggering a scrape; that check (checking the actual scraped-code
    # set instead of "any row exists") stays correct here too.
    scraped_codes = fetch_scraped_airline_codes(origin, destination, depart_date)
    needs_scrape = not rows and _uncovered_airlines(airline_list, scraped_codes)

    scraped_now = False
    if needs_scrape:
        try:
            _live_scrape(origin, destination, depart_date_obj, priority_codes=airline_list)
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
    airlines: str = Query(None, description="Comma-separated airline codes, e.g. CZ,MF,AC"),
):
    """Price trend by departure date ("which day is cheapest"). Scrapes live
    for every date in the range that has no stored fares yet — this can mean
    several sequential live scrapes (each up to ~60s), so the range is capped
    at MAX_ONDEMAND_RANGE_DAYS to keep a single request bounded. `airlines`,
    when given, both limits which airlines get the extra scrape pass (the
    main lever for cutting scrape time) and filters the returned data —
    but a date's "already covered, don't rescrape" check always looks at
    unfiltered data, so picking a different airline subset later doesn't
    force a wasteful rescrape of dates that already have other airlines'
    data (mirrors the same fix already applied to /api/fares)."""
    start_d = _parse_date(start_date, "start_date")
    end_d = _parse_date(end_date, "end_date")
    if end_d < start_d:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date")
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

    airline_list = [a.strip().upper() for a in airlines.split(",")] if airlines else None

    # Per-date "which airline codes are already covered" check — checking
    # the actual scraped-code set per date (not just "does any row exist for
    # this date") so a genuinely new airline (e.g. AC, after only CX had ever
    # been scraped for this date) correctly triggers a fresh scrape instead
    # of being silently skipped. Still avoids rescraping for a typo'd/
    # nonexistent code once real data exists for that date — same fix
    # applied to /api/fares, see get_fares().
    codes_by_date = {}
    for r in fetch_fares_by_date(origin, destination, start_date, end_date):
        codes_by_date.setdefault(r["depart_date"], set()).add(r["airline_code"])

    scraped_now = False
    scrape_count = 0
    d = start_d
    while d <= end_d:
        covered_codes = codes_by_date.get(d.isoformat(), set())
        if _uncovered_airlines(airline_list, covered_codes):
            if scrape_count > 0 and scrape_count % DATE_BATCH_SIZE == 0:
                time.sleep(DATE_BATCH_COOLDOWN_SECONDS)
            try:
                _live_scrape(origin, destination, d, priority_codes=airline_list)
                scraped_now = True
            except ScraperUnavailable as e:
                raise _scraper_unavailable_response(e)
            except Exception:
                # Best-effort across the range: one date's scrape failing
                # (site hiccup, transient block, etc.) shouldn't blank out
                # the rest of an otherwise-successful range.
                pass
            scrape_count += 1
        d += timedelta(days=1)

    rows = fetch_fares_by_date(origin, destination, start_date, end_date, airline_list)

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
    airlines: str = Query(None, description="Comma-separated airline codes, e.g. CZ,MF,AC"),
):
    """Price trend over scrape time for one fixed departure date ("should I
    book now or wait"). If there's no history yet at all, does one live
    scrape to seed the first data point — repeated visits over following
    days/weeks (each also scraping if there's still nothing) are what build
    the trend. `airlines`, when given, limits the scrape pass and filters
    the returned data, but (like /api/fares and /api/fares/by-date) never
    triggers a scrape just because the filter itself matches nothing — only
    when there's truly no history for this route/date at all."""
    depart_date_obj = _parse_date(depart_date, "depart_date")
    airline_list = [a.strip().upper() for a in airlines.split(",")] if airlines else None
    rows = fetch_price_history(origin, destination, depart_date, airline_list)

    # Same fix as get_fares()/get_fares_by_date(): check the actual set of
    # airline codes already scraped for this route/date, not just "does any
    # history exist at all" — the latter incorrectly skips scraping a
    # genuinely new airline once any other airline has history here.
    scraped_codes = fetch_scraped_airline_codes(origin, destination, depart_date)
    needs_scrape = not rows and _uncovered_airlines(airline_list, scraped_codes)

    scraped_now = False
    if needs_scrape:
        try:
            _live_scrape(origin, destination, depart_date_obj, priority_codes=airline_list)
        except ScraperUnavailable as e:
            raise _scraper_unavailable_response(e)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"No price history for this route/date, and the live scrape failed: {e}",
            )
        rows = fetch_price_history(origin, destination, depart_date, airline_list)
        scraped_now = True

    return {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "depart_date": depart_date,
        "history": rows,
        "scraped_now": scraped_now,
    }
