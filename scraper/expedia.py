"""Single-aggregator scraper: Expedia.ca.

Investigated (2026-08-29) as a replacement for scraper/tripcom.py after Trip.com
was confirmed to have zero China Eastern inventory on this project's standard
YVR->PVG test route. An ITA Matrix (matrix.itasoftware.com) alternative was
also investigated and abandoned: its deep-link URL only works when reached via
a real, trusted-gesture click inside its own Angular app — both a from-scratch
URL and a hard-reload of its own generated results URL got stuck forever, the
real search backend call never firing (confirmed via the Performance API).

Live-verified structure (real Chrome + DrissionPage, YVR->PVG, 2026-09-26):
  - Deep-link search URL (no form-filling needed at all — unlike Trip.com,
    which needs a page load but no autocomplete, and unlike ITA Matrix, whose
    equivalent link doesn't work standalone):
      https://www.expedia.ca/Flights-Search?trip=oneway
          &leg1=from:{origin},to:{destination},departure:{MM/DD/YYYY}TANYT
          &passengers=adults:1&mode=search
    `TANYT` is a fixed literal suffix observed on the working URL — not
    further reverse-engineered, just reproduced as-is. Expedia.ca always
    prices in CAD, so there's no currency query param to set.
  - Each fare is an `li[data-test-id="offer-listing"]` card. Within a card,
    `button[data-test-id="standard-offer-select-link"] .is-visually-hidden`
    holds one clean accessibility sentence with everything needed, e.g.:
      "Select Air Canada flight, departing at 10:50 a.m., arriving at 2:05
      p.m., priced at CA $616 One way per traveller. Nonstop. Arrives 1 day
      later."
    or, with a connection:
      "Select Cathay Pacific flight, departing at 2:10 p.m., arriving at
      11:55 p.m., priced at CA $806 One way per traveller. One stop. Arrives
      1 day later., Layover for 1 hour 45 minutes in Hong Kong SAR."
    Querying `.is-visually-hidden` from the card root (rather than scoped to
    this specific button) is unreliable: a sponsored ("Ad") card has an
    *earlier* hidden span for its ad badge that a root-level query matches
    first, returning "Ad" instead of the real sentence — confirmed live.
  - Stop count: rather than parse the "Nonstop"/"One stop"/"Two stops" wording
    (word-form, not digits, and untested for 3+ stops), count occurrences of
    the literal substring "Layover for" in the sentence — one per connection,
    independent of wording. `is_direct = stops == 0`.
  - **Duration must NOT be computed from the parsed depart/arrive local
    times** — confirmed live this produces nonsense (a 12h15m YVR->PVG
    nonstop came out as 1635 minutes / 27h+ when computed this way) for
    exactly the reason scraper/tripcom.py's own docstring already warns
    about: depart_time and arrive_time are each airport's *local* time, and
    Vancouver/Shanghai are ~15h apart, so a naive subtraction double-counts
    the timezone gap. Instead, duration is read from a separate leaf element
    within the card matching `^\d+h\s*\d+m$` exactly (e.g. "17h 43m") — found
    via `_find_total_duration_text()`. This element is *not* inside the
    `.is-visually-hidden` summary span, so it's queried separately per card.
    A card can contain a second, similarly-formatted string for just the
    layover leg (e.g. "3h 33m in SEA") — the exact-match regex (no trailing
    text allowed) excludes that one.
  - **A second sentence template exists** for some cards (seen live on ~2 of
    25 cards for the test route, cause not identified — possibly a fare
    with limited remaining seats): "Select and show fare information for
    {Airline} flight, departing at {time} from {city}, arriving at {time} in
    {city}, Priced at CA ${price} One way per traveller, N left at this
    price. Arrives N days later. N hours N minutes total travel time, One
    stop, Layover for ... in {city}." — different prefix ("Select and show
    fare information for" vs "Select"), " from {city}"/" in {city}" inserted
    after each time, comma instead of period before "N left at this price",
    and capitalized "Priced". The parsing regexes below tolerate both
    templates (optional prefix/city segments, case-insensitive).
  - A sponsored ("Ad") card can be a verbatim repeat of a real card further
    down the list (confirmed live: the same Hong Kong Airlines flight
    appeared twice, once ad-flagged). Rather than special-case the "Ad"
    badge, final results are de-duplicated by
    (airline_code, depart_time, arrive_time, price) — this collapses the
    ad/non-ad duplicate pair without caring which one was the "Ad".
  - Airline name -> code: Expedia's sentence gives the full display name
    (e.g. "China Eastern Airlines"), not a code. _AIRLINE_NAME_TO_CODE maps
    the observed/likely names for this project's 13 priority airlines (see
    scraper/common/airlines.py) plus a handful of other common carriers seen
    live on this route (United, Cathay Pacific, Lufthansa, etc.). An
    unrecognized name still produces a FareResult with airline_code="UNK"
    rather than being dropped — mirrors tripcom.py's own fallback when its
    logo-URL code extraction fails.

KNOWN LIMITATION — default view is capped, same as Trip.com's own documented
gap, and not yet worked around here: confirmed live that Expedia's default
page load renders exactly 25 cards while the page's own sidebar/filter counts
(seen once, in a since-unreproduced view) implied ~74 real flights existed for
the same search. Two mitigations Trip.com uses were tried against Expedia and
did NOT work: scrolling the results list (repeated `page.scroll.to_bottom()`
calls plateaued at 25 cards, no lazy-loading observed) and locating an
always-visible airline sidebar filter to click per priority carrier (no
reliable, reproducible DOM element was found for this across several live
attempts — the "Airlines" filter renders as a compact pill that opens
something on click, but its content didn't appear in a plain body-text/DOM
query the way Trip.com's sidebar does, and the one time a full sidebar with
airline names+counts WAS seen, it could not be reproduced in later navigations
of the same URL). So unlike tripcom.py, `search()` below does NOT do an extra
pass per priority carrier — it returns whatever's in the default ~25-card
view. **Correction from an earlier (wrong) assumption**: that sidebar view
had shown "China Eastern Airlines (8) CA $1,717" as an aggregate count, which
was read as confirmation Expedia surfaces China Eastern for this route — but
live testing of the actual parsed default-view cards (several separate runs)
never once included China Eastern, only Air Canada/Cathay Pacific/Hong Kong
Airlines/Delta/United/American/Korean Air. The sidebar's aggregate count is
not the same as what's in the scrapeable card list — this turns out to be
the same "default view is a curated subset, not full inventory" limitation
Trip.com already has, just without a working filter mechanism to reach the
rest. Whether China Eastern specifically is reachable at all here is
unresolved. General full-inventory coverage (especially the rarer priority
carriers — Hainan, Xiamen, Sichuan, Japan Airlines, China Airlines, China
Southern, none of which appeared in the default view for this one test
route/date across several runs) is unconfirmed and likely incomplete.
`priority_codes` is still accepted for
interface compatibility with tripcom.py's `search()` and is used only to
*filter* the returned results to that subset — it does not trigger any extra
scraping pass. Revisit this if/when a reliable way to reach Expedia's full
inventory is found (e.g. by properly reverse-engineering the "Airlines" quick
filter's modal, which was not resolved here).

HEADLESS MODE DOES NOT WORK against this site either: verified live that
`ChromiumOptions().headless()` gets served a "Bot or Not?" challenge page
instead of real results — same headless-blocking behavior Trip.com has.
`search()` defaults to `headless=False` for this reason, same as tripcom.py.
"""
import re
import time
import urllib.parse
from datetime import datetime
from typing import List, Optional

from DrissionPage import ChromiumPage
from DrissionPage import ChromiumOptions

from scraper.common.airlines import PRIORITY_AIRLINE_CODES
from scraper.common.schema import FareResult, SearchRequest

CARD_SELECTOR = 'css:li[data-test-id="offer-listing"]'
OFFER_BUTTON_SELECTOR = 'css:button[data-test-id="standard-offer-select-link"]'
RESULTS_WAIT_SECONDS = 20

# Expedia's exact display name for each of this project's 13 priority
# airlines (scraper/common/airlines.py), plus a handful of other common
# carriers seen live on the YVR->PVG test route. Confirmed live: Air Canada,
# China Eastern Airlines, China Southern Airlines, Air China, Hong Kong
# Airlines, Cathay Pacific, Korean Air, EVA Airways, China Airlines. NOT
# confirmed live (Hainan/Xiamen/Sichuan/Japan Airlines didn't appear on this
# one test route/date) — using their standard English trade names as a
# best guess, verify if/when a route surfaces them.
_AIRLINE_NAME_TO_CODE = {
    "Air Canada": "AC",
    "China Eastern Airlines": "MU",
    "China Southern Airlines": "CZ",
    "Air China": "CA",
    "Hainan Airlines": "HU",
    "Xiamen Airlines": "MF",
    "Xiamen Air": "MF",
    "Sichuan Airlines": "3U",
    "Korean Air": "KE",
    "Hong Kong Airlines": "HX",
    "Cathay Pacific": "CX",
    "EVA Airways": "BR",
    "EVA Air": "BR",
    "China Airlines": "CI",
    "Japan Airlines": "JL",
    # Other carriers observed live on the test route — not priority airlines,
    # but still worth a real code rather than falling through to "UNK".
    "United": "UA",
    "Delta": "DL",
    "Lufthansa": "LH",
    "American Airlines": "AA",
    "Alaska Airlines": "AS",
    "Condor": "DE",
    "KLM": "KL",
    "All Nippon Airways": "NH",
    "Asiana Airlines": "OZ",
    "Philippine Airlines": "PR",
    "Turkish Airlines": "TK",
    "WestJet": "WS",
}

_SUMMARY_RE = re.compile(
    r"Select(?: and show fare information for)? (?P<airline>.+?) flight,\s*"
    r"departing at (?P<dep>\d{1,2}:\d{2}\s*[ap]\.m\.)(?:\s+from\s+[^,]+)?,\s*"
    r"arriving at (?P<arr>\d{1,2}:\d{2}\s*[ap]\.m\.)(?:\s+in\s+[^,]+)?,\s*"
    r"priced at CA\s*\$(?P<price>[\d,]+)",
    re.IGNORECASE,
)
_DAYS_LATER_RE = re.compile(r"Arrives (?P<days>\d+) days? later", re.IGNORECASE)
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*(h|m)\b", re.IGNORECASE)


def _parse_ampm_time(text: str):
    cleaned = text.replace(".", "").replace(" ", "").upper()
    return datetime.strptime(cleaned, "%I:%M%p").time()


def _parse_duration_mins(text: str) -> Optional[int]:
    tokens = _DURATION_TOKEN_RE.findall(text or "")
    if not tokens:
        return None
    total = 0
    for value, unit in tokens:
        total += int(value) * 60 if unit.lower() == "h" else int(value)
    return total


def _find_total_duration_text(card) -> str:
    """Find the card's total-trip-duration leaf text (e.g. "17h 43m", or just
    "41h" for a whole-hour duration with no remainder minutes — confirmed
    live this rendering exists), distinct from a similarly-formatted
    per-layover duration (e.g. "3h 33m in SEA") which has trailing text and
    so doesn't match the exact pattern."""
    return card.run_js(
        r"""
        const el = this;
        let found = '';
        el.querySelectorAll('*').forEach(n => {
            if (found || n.children.length !== 0) return;
            const t = (n.textContent || '').trim();
            if (/^\d+h(?:\s*\d+m)?$/.test(t)) found = t;
        });
        return found;
        """
    )


def build_search_url(request: SearchRequest) -> str:
    depart_str = request.depart_date.strftime("%m/%d/%Y")
    leg1 = f"from:{request.origin},to:{request.destination},departure:{depart_str}TANYT"
    # Only "/" needs escaping to reproduce the confirmed-working URL — ":" and
    # "," stay literal.
    return (
        "https://www.expedia.ca/Flights-Search?trip=oneway"
        f"&leg1={urllib.parse.quote(leg1, safe=':,')}"
        "&passengers=adults:1&mode=search"
    )


def _parse_card_text(
    text: str, duration_text: str, request: SearchRequest, currency: str
) -> Optional[FareResult]:
    m = _SUMMARY_RE.search(text or "")
    if not m:
        return None

    airline_name = m.group("airline").strip()
    airline_code = _AIRLINE_NAME_TO_CODE.get(airline_name, "UNK")

    price = float(m.group("price").replace(",", ""))

    dep_time = _parse_ampm_time(m.group("dep"))
    arr_time = _parse_ampm_time(m.group("arr"))

    days_match = _DAYS_LATER_RE.search(text or "")
    day_offset = int(days_match.group("days")) if days_match else 0

    duration_mins = _parse_duration_mins(duration_text)

    stops = len(re.findall(r"layover for", text or "", re.IGNORECASE))
    is_direct = stops == 0

    arrive_time_str = arr_time.strftime("%H:%M") + (f"+{day_offset}" if day_offset else "")

    return FareResult(
        airline_code=airline_code,
        airline_name=airline_name,
        origin=request.origin,
        destination=request.destination,
        depart_date=request.depart_date,
        price=price,
        currency=currency,
        return_date=request.return_date,
        is_direct=is_direct,
        depart_time=dep_time.strftime("%H:%M"),
        arrive_time=arrive_time_str,
        duration_mins=duration_mins,
        stops=stops,
        raw_details={"source": "expedia.ca", "summary": text.strip()},
    )


def _wait_for_cards(page):
    """Poll until the results list stops growing, not just until the first
    card appears. Confirmed live: `page.eles(..., timeout=N)` returns as soon
    as any cards exist, which can be well before the full ~25-card default
    view has rendered (observed: collecting immediately after the first
    cards appeared captured only 4 of the eventual 25) — so "stable for two
    consecutive checks" is used as the completion signal instead of "any
    cards found at all"."""
    deadline = time.time() + RESULTS_WAIT_SECONDS
    last_count = -1
    stable_checks = 0
    cards = []
    while time.time() < deadline:
        cards = page.eles(CARD_SELECTOR, timeout=1)
        if cards:
            if len(cards) == last_count:
                stable_checks += 1
                if stable_checks >= 2:
                    return cards
            else:
                stable_checks = 0
            last_count = len(cards)
        time.sleep(1.5)
    return cards


def _collect_cards(page, request: SearchRequest, currency: str) -> List[FareResult]:
    results = []
    for card in page.eles(CARD_SELECTOR, timeout=1):
        btn = card.ele(OFFER_BUTTON_SELECTOR, timeout=1)
        if not btn:
            continue
        hidden = btn.ele("css:.is-visually-hidden", timeout=1)
        if not hidden:
            continue
        duration_text = _find_total_duration_text(card)
        fare = _parse_card_text(hidden.text, duration_text, request, currency)
        if fare:
            results.append(fare)
    return results


def _dedupe(results: List[FareResult]) -> List[FareResult]:
    seen = set()
    deduped = []
    for r in results:
        key = (r.airline_code, r.depart_time, r.arrive_time, r.price)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def search(
    request: SearchRequest,
    currency: str = "CAD",
    headless: bool = False,
    priority_codes: Optional[List[str]] = None,
) -> List[FareResult]:
    """Search Expedia.ca for a route/date and return one FareResult per listed flight.

    Unlike scraper/tripcom.py's search(), this does NOT do an extra pass per
    priority carrier — no reliable way to surface Expedia's hidden inventory
    (beyond the default ~25-card view) was found live (see module docstring's
    "KNOWN LIMITATION" section). `priority_codes`, when given, only filters
    the returned results to that subset; it does not affect what gets
    scraped. Kept as a parameter for interface compatibility with tripcom.py.
    """
    if request.return_date is not None:
        raise NotImplementedError("Round-trip search not yet implemented for Expedia.")

    options = ChromiumOptions()
    if headless:
        options.headless()

    url = build_search_url(request)
    page = ChromiumPage(addr_or_opts=options)
    try:
        page.get(url)
        cards = _wait_for_cards(page)
        if not cards:
            raise RuntimeError(
                "No fare cards found on Expedia.ca results page within "
                f"{RESULTS_WAIT_SECONDS}s — site structure may have changed, "
                "the route/date returned zero results, or the 'Bot or Not?' "
                "challenge page was served (see module docstring)."
            )

        results = _dedupe(_collect_cards(page, request, currency))

        wanted = priority_codes if priority_codes is not None else PRIORITY_AIRLINE_CODES
        if priority_codes is not None:
            results = [r for r in results if r.airline_code in wanted]

        return results
    finally:
        page.quit()
