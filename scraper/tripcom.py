"""Single-aggregator scraper: Trip.com (Ctrip).

Replaces the earlier per-airline-site approach (scraper/airlines/, kept for
reference/history) after live investigation showed each airline's own site
needs hours of bespoke reverse-engineering per site. Trip.com lists many
airlines (including all of the Chinese carriers and Air Canada/Korean Air) for
a single route/date search in one well-structured results page, so one
scraper here covers what would otherwise be 7+ separate ones.

Live-verified structure (real Chrome + DrissionPage, YVR->PVG, 2026-09-26):
  - Deep-link search URL (no form-filling needed):
      https://www.trip.com/flights/showfarefirst
          ?dcity={origin}&acity={destination}&ddate={YYYY-MM-DD}
          &triptype=ow&class=y&lowpricesource=searchform&quantity=1
          &searchboxarg=t&nonstoponly=off&locale=en-XX&curr={currency}
  - Each fare is a `div.result-item.J_FlightItem` card. Within a card:
      - `.flights-name` -> airline display name (text)
      - `.flt-card-airline-logo img[src]` -> logo URL ending in
        `airline_logo/3x/<code>.webp`; `<code>` uppercased is the IATA code
        (e.g. `ac.webp` -> AC, `ke.webp` -> KE). More reliable than trying to
        map airline names to codes ourselves.
      - `[data-testid^="flight_price_"]` -> has a clean numeric `data-price`
        attribute (e.g. `623`), no need to parse the "CAD 623" display text.
      - `[data-testid="stopInfoText"]` -> one of three formats, confirmed live
        (YHZ->HAN): "Nonstop" (0 stops); "<layover duration> in <city>" for a
        1-stop itinerary, e.g. "3h 40m in Calgary" (the site shows the
        layover length here, not a stop count); or "<N> stops in <city1>,
        <city2>, ..." for 2+ stops, e.g. "2 stops in Vancouver, Manila". Parse
        the leading digit for N when present; otherwise it's Nonstop (0) or a
        single-duration string (1 stop).
      - `.flight-info[aria-label]` contains a sentence with exact depart/arrive
        timestamps: "...departing from <airport> at <YYYY-MM-DD HH:MM:SS> and
        arriving at <airport> at <YYYY-MM-DD HH:MM:SS>. ...One-way price:
        <CUR> <amount>". These timestamps are each airport's **local** time,
        so do NOT compute trip duration by subtracting them directly — YVR-PVG
        is nonstop-12h15m by the site's own duration display, but the raw
        timestamp delta is ~27h because Shanghai is ~15h ahead of Vancouver.
        (Only used here to detect day-rollover for the display string, e.g.
        "14:05+1".) The aria-label's own prose "duration" phrase is also
        unreliable for connections (reports the layover length, not the trip
        total, and can contain a literal unfilled `${2}` placeholder).
      - `[data-testid="flightInfoDuration"]` -> the correct total trip
        duration as e.g. "12h 15m" or "17h 45m" — always exactly one per
        card, verified for both a nonstop and a 1-stop card. Use this, not
        the timestamp delta, for `duration_mins`.

DEFAULT RESULTS ARE INCOMPLETE — confirmed live (YVR->PVG, 2026-09-26): the
default page load only renders a curated top-N subset (8-13 cards) even
though the airline filter sidebar (`.filter-item[data-code]`, one per
airline, e.g. `data-code="CZ"` for China Southern) shows the *real* total can
be much higher (38 flights across 12 airlines for that search — Cathay
Pacific alone had 9). There is no "show more"/pagination control that reveals
the rest; scrolling plateaus early. The only reliable way found to surface a
specific hidden airline's results: click that airline's `.filter-item`,
which filters the list down to just that airline's cards. Confirmed NOT
reliable: clicking a second `.filter-item` on the same page load right after
the first (raises DrissionPage `NoRectError` — the sidebar DOM apparently
doesn't stay in a clickable state after the first filter is applied). The
only pattern that worked consistently: fresh page load -> click exactly one
airline filter -> collect its cards. `search()` below uses this: it collects
the default view, then does one fresh reload + filter click per priority
carrier (see PRIORITY_AIRLINE_CODES) that isn't already covered — targeted at
this project's specific carriers of interest (mainland Chinese carriers plus
Air Canada/Cathay Pacific/Korean Air/Hong Kong Airlines), not a general fix
for exhaustive coverage of every airline on every route (doing that for all
~12 airlines would mean ~12 page loads per search).
Also note: `.filter-item[data-code]` isn't airline-only — the same attribute
is used for stop-count, alliance, airport, cabin-class, and amenity filters
too (e.g. `data-code="DIRECT"`, `data-code="SA"` for Star Alliance,
`data-code="PVG"` for the airport itself) — don't assume every data-code is
an airline without checking against a known airline-code list.

NOT yet handled / next steps for whoever picks this up:
  - Round-trip search (`triptype=ow` is one-way only; round-trip would need
    `triptype=rt` plus a return date param — not investigated).
  - General pagination (getting every airline, not just the Chinese-carrier
    subset above) — would need the same fresh-reload-per-filter pattern
    applied to every `data-code` in the sidebar, at a real time cost.
  - Anti-bot hardening for scheduled/unattended runs (this was tested
    interactively; a cron job hitting this repeatedly may eventually get
    rate-limited or challenged — watch for it and back off if so). Also
    observed live: repeatedly clicking the same filter across several script
    runs in the same browser profile left Trip.com's own session state
    "stuck" showing the last-applied filter even on a fresh navigation to the
    unfiltered URL, until the browser profile was wiped — if results look
    suspiciously pre-filtered, that's the likely cause; clear
    `/var/folders/.../DrissionPage/userData` (or wherever ChromiumOptions
    points the user-data-dir) to reset it.

HEADLESS MODE DOES NOT WORK against this site: verified live that
`ChromiumOptions().headless()` gets served an essentially empty page (~168
bytes, no real markup) instead of the real results — Trip.com appears to
detect and block headless Chrome outright. `search()` defaults to
`headless=False` (a real, visible Chrome window) for this reason. For
unattended/scheduled runs on a real machine, that means either: running it on
a desktop session that's actually logged in (e.g. Windows Task Scheduler
configured to run only when the user is logged in, not "run whether user is
logged in or not"), or running under a virtual display (e.g. Xvfb on
Linux/Mac) so a window still exists even though nothing is physically shown.
Passing `headless=True` was tried and confirmed broken — don't re-attempt it
without also solving the detection problem (e.g. stealth/anti-detection
Chrome flags), which was not investigated here.
"""
import re
import time
from datetime import datetime
from typing import List, Optional

from DrissionPage import ChromiumPage
from DrissionPage import ChromiumOptions

from scraper.common.schema import FareResult, SearchRequest

SEARCH_URL_TEMPLATE = (
    "https://www.trip.com/flights/showfarefirst"
    "?dcity={origin}&acity={destination}&ddate={depart_date}"
    "&triptype=ow&class=y&lowpricesource=searchform&quantity=1"
    "&searchboxarg=t&nonstoponly=off&locale=en-XX&curr={currency}"
)

CARD_SELECTOR = "css:.result-item.J_FlightItem"
RESULTS_WAIT_SECONDS = 20

# Priority carriers this project wants guaranteed to be checked, even when
# Trip.com's default view hides them: mainland Chinese carriers (MU, CZ, CA,
# MF, 3U, HU) plus other airlines of specific interest (AC Air Canada, CX
# Cathay Pacific, KE Korean Air, HX Hong Kong Airlines). Not meant to be
# exhaustive of every airline Trip.com lists, just the ones worth an extra
# per-search round-trip to check for. Codes per Trip.com's own `data-code`
# attribute, confirmed for CZ/CA live; others taken from IATA codes and not
# yet individually confirmed against a route where they appear.
PRIORITY_AIRLINE_CODES = ["AC", "MU", "CX", "HU", "CA", "CZ", "KE", "MF", "HX", "3U"]

_ARIA_LABEL_RE = re.compile(
    r"departing from .*? at (?P<dep>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?"
    r"arriving at .*? at (?P<arr>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
    re.DOTALL,
)
_LOGO_CODE_RE = re.compile(r"airline_logo/3x/([a-z0-9]+)\.webp", re.IGNORECASE)
# Matches individual "<N>h" / "<N>m" tokens anywhere in the string (rather than
# a single fully-optional pattern anchored at position 0) so a stray leading
# character (e.g. a screen-reader prefix) can't produce a spurious zero-width
# match before the real "Xh Ym" text and silently swallow the duration.
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*(h|m)\b", re.IGNORECASE)
_STOPS_COUNT_RE = re.compile(r"(\d+)\s*stops?", re.IGNORECASE)


def _parse_duration_mins(text: str) -> Optional[int]:
    tokens = _DURATION_TOKEN_RE.findall(text or "")
    if not tokens:
        return None
    total = 0
    for value, unit in tokens:
        total += int(value) * 60 if unit.lower() == "h" else int(value)
    return total


def build_search_url(request: SearchRequest, currency: str = "CAD") -> str:
    return SEARCH_URL_TEMPLATE.format(
        origin=request.origin.lower(),
        destination=request.destination.lower(),
        depart_date=request.depart_date.isoformat(),
        currency=currency,
    )


def _parse_card(card, request: SearchRequest, currency: str) -> Optional[FareResult]:
    airline_el = card.ele("css:.flights-name", timeout=1)
    logo_el = card.ele("css:.flt-card-airline-logo img", timeout=1)
    price_el = card.ele('css:[data-testid^="flight_price_"]', timeout=1)
    stop_el = card.ele('css:[data-testid="stopInfoText"]', timeout=1)
    info_el = card.ele("css:.flight-info", timeout=1)
    duration_el = card.ele('css:[data-testid="flightInfoDuration"]', timeout=1)

    if not (airline_el and logo_el and price_el and info_el and duration_el):
        return None

    airline_name = airline_el.text.strip()
    logo_match = _LOGO_CODE_RE.search(logo_el.attr("src") or "")
    airline_code = logo_match.group(1).upper() if logo_match else "UNK"

    price_raw = price_el.attr("data-price")
    if not price_raw:
        return None
    price = float(price_raw.replace(",", ""))

    aria_label = info_el.attr("aria-label") or ""
    m = _ARIA_LABEL_RE.search(aria_label)
    if not m:
        return None
    depart_dt = datetime.strptime(m.group("dep"), "%Y-%m-%d %H:%M:%S")
    arrive_dt = datetime.strptime(m.group("arr"), "%Y-%m-%d %H:%M:%S")
    # NOT (arrive_dt - depart_dt): these are each airport's local time, so a
    # naive delta double-counts the timezone offset (see module docstring).
    duration_mins = _parse_duration_mins(duration_el.text)

    day_offset = (arrive_dt.date() - depart_dt.date()).days
    arrive_time_str = arrive_dt.strftime("%H:%M") + (f"+{day_offset}" if day_offset else "")

    stop_text = stop_el.text.strip() if stop_el else ""
    is_direct = stop_text.lower() == "nonstop"
    stops_match = _STOPS_COUNT_RE.search(stop_text)
    if stops_match:
        stops = int(stops_match.group(1))
    elif is_direct:
        stops = 0
    else:
        # "<duration> in <city>" format (e.g. "3h 40m in Calgary") means a
        # single layover — the site doesn't print a count for this case.
        stops = 1

    return FareResult(
        airline_code=airline_code,
        airline_name=airline_name,
        origin=request.origin,
        destination=request.destination,
        depart_date=depart_dt.date(),
        price=price,
        currency=currency,
        return_date=request.return_date,
        is_direct=is_direct,
        depart_time=depart_dt.strftime("%H:%M"),
        arrive_time=arrive_time_str,
        duration_mins=duration_mins,
        stops=stops,
        raw_details={"source": "trip.com", "stop_text": stop_text},
    )


def _wait_for_cards(page):
    deadline = time.time() + RESULTS_WAIT_SECONDS
    while time.time() < deadline:
        cards = page.eles(CARD_SELECTOR, timeout=1)
        if cards:
            # Card containers can appear slightly before their sub-elements
            # (price/duration/etc.) are populated — observed once as a fresh
            # browser profile's first-ever page load returning 0 parsed
            # results despite cards being present. A short settle delay
            # avoids racing that.
            time.sleep(1.5)
            return page.eles(CARD_SELECTOR, timeout=1)
        time.sleep(1)
    return []


def _collect_cards(page, request: SearchRequest, currency: str) -> List[FareResult]:
    results = []
    for card in page.eles(CARD_SELECTOR, timeout=1):
        fare = _parse_card(card, request, currency)
        if fare:
            results.append(fare)
    return results


def search(request: SearchRequest, currency: str = "CAD", headless: bool = False) -> List[FareResult]:
    """Search Trip.com for a route/date and return one FareResult per listed flight.

    Includes an extra pass per priority carrier (see PRIORITY_AIRLINE_CODES)
    not already present in the default view, since Trip.com's default view
    hides most of its real inventory behind per-airline sidebar filters (see
    module docstring's "DEFAULT RESULTS ARE INCOMPLETE" section).
    """
    if request.return_date is not None:
        raise NotImplementedError("Round-trip search not yet implemented (Trip.com needs triptype=rt + return date).")

    options = ChromiumOptions()
    if headless:
        options.headless()

    url = build_search_url(request, currency)
    page = ChromiumPage(addr_or_opts=options)
    try:
        page.get(url)
        cards = _wait_for_cards(page)
        if not cards:
            raise RuntimeError(
                "No fare cards found on Trip.com results page within "
                f"{RESULTS_WAIT_SECONDS}s — site structure may have changed, "
                "or the route/date returned zero results."
            )

        results = _collect_cards(page, request, currency)
        if not results:
            # Cards existed but nothing parsed — likely the same render-race
            # the settle delay above targets, just not fully caught. One
            # retry after a longer pause rather than silently returning an
            # incomplete (or entirely empty) result set.
            time.sleep(2)
            results = _collect_cards(page, request, currency)
        seen_codes = {r.airline_code for r in results}

        for code in PRIORITY_AIRLINE_CODES:
            if code in seen_codes:
                continue
            # Fresh reload before each filter click — clicking a second
            # filter on the same load was unreliable (see module docstring).
            page.get(url)
            if not _wait_for_cards(page):
                continue
            filter_el = page.ele(f'css:.filter-item[data-code="{code}"]', timeout=2)
            if not filter_el:
                continue  # this airline has no inventory for this search at all
            try:
                filter_el.click()
                time.sleep(2.5)
            except Exception:
                continue  # best-effort: one flaky filter click shouldn't fail the whole search
            for fare in _collect_cards(page, request, currency):
                if fare.airline_code == code:
                    results.append(fare)

        return results
    finally:
        page.quit()
