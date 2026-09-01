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
this project's specific list of 13 airlines of interest, not a general fix
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
  - Anti-bot hardening for scheduled/unattended runs — **this actually
    happened live (2026-08-30)**, not just a theoretical risk: a user
    selecting "all airlines" across several dates in one session got only
    2 of 13 airlines back, no error surfaced. Root cause: enough rapid
    per-airline reloads in one browser session triggered Trip.com's
    slide-to-verify challenge partway through; every pass after that
    silently found zero cards and was skipped (a CAPTCHA page looks
    identical to "no cards found" to the current code — see `_wait_for_cards`).
    Mitigated (not solved) by `EXTRA_PASS_DELAY_SECONDS` above, which paces
    the reloads to make triggering it less likely — this project does not
    attempt to detect or defeat the challenge itself, so a scrape can still
    silently under-report if it does get challenged. If results look
    suspiciously sparse across many airlines, this is the likely cause;
    consider narrowing `priority_codes` for that request, or spacing out
    separate scrapes more, rather than assuming the code is broken. Also
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

# Pause before each per-airline extra-pass reload (see the loop in search()).
# Confirmed live (2026-08-30): a multi-airline scrape doing many reloads in
# quick succession in the same browser session can trigger Trip.com's
# slide-to-verify bot challenge partway through, after which every remaining
# pass silently finds no cards and is skipped — a user selecting "all
# airlines" got only 2 of 13 back with no error. This delay isn't a fix for
# the challenge itself (not something this project tries to defeat), just a
# way to reduce how often rapid-fire reloads trigger it in the first place.
EXTRA_PASS_DELAY_SECONDS = 4

# On top of the per-reload delay above, this still wasn't enough on its own —
# confirmed live: even with EXTRA_PASS_DELAY_SECONDS in place, a full
# "all 13 airlines" scrape across several dates (each date doing its own
# up-to-13-reload extra pass) still got challenged, since checking all 13 in
# one continuous run is still 13 back-to-back reloads regardless of a short
# per-reload pause. So the extra-pass loop is also split into batches, with a
# much longer cooldown between batches rather than one continuous run of
# reloads — spreading a large request out over time instead of just slowing
# each individual step down a little.
EXTRA_PASS_BATCH_SIZE = 4
EXTRA_PASS_BATCH_COOLDOWN_SECONDS = 25

# Priority carriers this project wants guaranteed to be checked, even when
# Trip.com's default view hides them — the user's explicit list of airlines
# to always query: AC (Air Canada), MU (China Eastern), CZ (China Southern),
# CA (Air China), HU (Hainan), MF (Xiamen), 3U (Sichuan), KE (Korean Air),
# HX (Hong Kong Airlines), CX (Cathay Pacific), BR (EVA Air), CI (China
# Airlines), JL (Japan Airlines). Not meant to be exhaustive of every airline
# Trip.com lists, just this specific set. Codes per Trip.com's own
# `data-code` attribute, confirmed for CZ/CA live; others taken from IATA
# codes and not yet individually confirmed against a route where they appear.
PRIORITY_AIRLINE_CODES = ["AC", "MU", "CZ", "CA", "HU", "MF", "3U", "KE", "HX", "CX", "BR", "CI", "JL"]

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
    if "," in airline_name:
        # A comma means Trip.com is showing a combined marketing/operating-
        # carrier codeshare listing (e.g. "Air Canada, Cathay Pacific" for an
        # AC-marketed, CX-operated flight) rather than a single carrier's own
        # flight. Confirmed live (YVR->PVG, 2026-09-23): this card is
        # genuinely present in Trip.com's own default view, not a scraping
        # artifact — but it's not a clean single-carrier fare, which is what
        # this project's airline-by-airline comparison needs, so it's
        # dropped here rather than saved under whichever code happens to be
        # extracted from its logo.
        return None
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


def _expand_airline_filter_list(page):
    """Click any visible "Show More" toggle in the filter sidebar.

    Confirmed live (2026-08-30, YVR->PVG 2026-10-15): the Airlines filter
    section only shows the top ~5 airlines by default (by rank, e.g. United/
    Air China/Korean Air/Air Canada/EVA Air) — every other airline's
    `.filter-item[data-code]`, including several of this project's priority
    carriers (confirmed: China Eastern, Hainan, Hong Kong Airlines, China
    Airlines all hidden this way), exists in the DOM but has zero width/
    height until "Show More" is clicked, so `filter_el.click()` on one of
    them raised DrissionPage's "no location or size" error and the
    exception handler in search()'s extra-pass loop silently treated that as
    "this airline has no inventory" — when it may have real flights sitting
    one click away. This was very likely the root cause of an earlier
    finding in this project's history that China Eastern "has zero
    inventory" on Trip.com for a given route/date — re-verify any such
    earlier claim now that this is fixed, since it may have just been this
    silent click failure, not real unavailability.

    Best-effort: harmless if there's no "Show More" to click, or if the
    click itself fails for some other reason.
    """
    try:
        page.run_js(
            """
            Array.from(document.querySelectorAll('span')).forEach(s => {
                if (s.textContent.trim() === 'Show More') {
                    const r = s.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) s.click();
                }
            });
            """
        )
        time.sleep(1)
    except Exception:
        pass


def search(
    request: SearchRequest,
    currency: str = "CAD",
    headless: bool = False,
    priority_codes: Optional[List[str]] = None,
) -> List[FareResult]:
    """Search Trip.com for a route/date and return one FareResult per listed flight.

    Includes an extra pass per priority carrier not already present in the
    default view, since Trip.com's default view hides most of its real
    inventory behind per-airline sidebar filters (see module docstring's
    "DEFAULT RESULTS ARE INCOMPLETE" section). Checks PRIORITY_AIRLINE_CODES
    by default; pass `priority_codes` to check a smaller/different set
    instead — e.g. the web layer passes through whatever subset the user
    actually selected, since each extra carrier costs a full page reload
    plus EXTRA_PASS_DELAY_SECONDS of pacing (~9-12s+ per carrier) and
    checking all 13 by default is the main cost of a scrape.
    """
    codes_to_check = priority_codes if priority_codes is not None else PRIORITY_AIRLINE_CODES
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

        reload_count = 0
        for code in codes_to_check:
            if code in seen_codes:
                continue
            # Pace reloads to reduce the odds of tripping Trip.com's bot
            # challenge: a short pause before every reload, plus a much
            # longer cooldown every EXTRA_PASS_BATCH_SIZE reloads so a large
            # request (many airlines) is spread out over time rather than
            # run as one continuous burst — confirmed live that the short
            # pause alone wasn't enough to stop a full 13-airline scrape
            # from getting challenged partway through.
            if reload_count > 0 and reload_count % EXTRA_PASS_BATCH_SIZE == 0:
                time.sleep(EXTRA_PASS_BATCH_COOLDOWN_SECONDS)
            else:
                time.sleep(EXTRA_PASS_DELAY_SECONDS)
            reload_count += 1
            # Fresh reload before each filter click — clicking a second
            # filter on the same load was unreliable (see module docstring).
            page.get(url)
            if not _wait_for_cards(page):
                continue
            # Expand any collapsed "Show More" filter list first — most
            # priority carriers live past the default-shown top ~5 airlines
            # (see _expand_airline_filter_list's docstring).
            _expand_airline_filter_list(page)
            filter_el = page.ele(f'css:.filter-item[data-code="{code}"]', timeout=2)
            if not filter_el:
                continue  # this airline has no inventory for this search at all
            try:
                # JS-dispatched click, not DrissionPage's native coordinate
                # click — confirmed live the native click can fail with "no
                # location or size" even on an element that becomes properly
                # visible right after _expand_airline_filter_list runs (a
                # possible layout/paint-timing race), while a JS click on the
                # same element works reliably regardless.
                filter_el.run_js("this.click()")
                time.sleep(2.5)
            except Exception:
                continue  # best-effort: one flaky filter click shouldn't fail the whole search
            for fare in _collect_cards(page, request, currency):
                if fare.airline_code == code:
                    results.append(fare)

        return results
    finally:
        page.quit()
