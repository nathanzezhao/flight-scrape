# QA Team

A 3-person QA team, split by the same natural boundary as engineering: scraper
correctness is a fundamentally different kind of testing (does this match what
Trip.com's own site actually shows, and does Trip.com itself match reality)
than API/UI correctness (does the app correctly display what's in the
database).

## Roles

### QA 1 — Scraper correctness
Verifies `scraper/tripcom.py`'s output against Trip.com's own site (not
against an airline's own site — see README for why this project intentionally
compares against an aggregator rather than 7 individual airline sites):
- Run a real search on trip.com by hand for a given route/date, and compare
  airline, price, times, and direct/stopover status against what the scraper
  wrote to the DB for the same search. (A first real run already matched: see
  README's "Current status" for the verified YVR→PVG example.)
- Check edge cases: a route/date with zero results, a route with only
  connecting (no nonstop) options, a date far enough out that prices/schedules
  might not be published yet.
- Multi-stop (2+ layover) parsing was verified in the 2026-08-27 QA pass (a
  Halifax→Hanoi search returned genuine 2-stop itineraries, correctly parsed
  as `stops: 2`, not the earlier 0-or-1 approximation) — re-check periodically
  since it still depends on the exact `stopInfoText` phrasing ("N stops in
  city1, city2, ...") not changing.
- Watch for **site-structure drift**: periodically confirm the scraper still
  returns sane data, since this depends entirely on Trip.com's DOM not
  changing. A scraper that silently starts returning 0 or garbage results is
  the main long-term risk of the aggregator approach.
- **New: Chinese-carrier extra pass.** `search()` now does one extra
  filtered reload per Chinese carrier (MU/CZ/CA/MF/3U/HU) not already in the
  default view, since Trip.com's default view hides most inventory (confirmed
  live: 8 of 38 actual flights shown by default on one real search) behind
  per-airline sidebar filters. Verified live this successfully surfaces China
  Southern/Air China in most runs (~3 of 4 test runs), but it's inherently
  best-effort — a missed carrier could mean genuine no-inventory or a flaky
  filter click, and the code doesn't distinguish the two. Periodically verify
  by hand against trip.com's own filter sidebar counts. Also confirmed live:
  China Eastern specifically has **zero** inventory on Trip.com for the
  YVR→PVG test route — re-verify against other routes before assuming this
  generalizes; it may show up on routes closer to its own hub network.
- Verify scheduled/repeated runs don't trip Trip.com's bot detection (the
  scraper already confirmed headless mode gets blocked outright — check that
  headed, repeated runs over time don't eventually get blocked too).

### QA 2 — API & data correctness
Tests `web/api/` against the database directly, independent of the scraper:
- Seed the dev DB (SQLite fallback or a test Postgres) with known fare rows
  and verify `/api/fares` returns the right rows, sorted cheapest-first, only
  the latest scrape per airline (not duplicates from repeated runs).
- Verify filtering by airline codes (`?airlines=CZ,MF`) returns only those
  airlines.
- Verify error handling: missing `DATABASE_URL`, malformed date, unknown
  origin/destination — confirm the API fails clearly rather than silently
  returning wrong data.
- Verify `/api/airlines` reflects whatever's actually in the DB (it's derived
  from real scraped data now, not a fixed list — see Engineering notes) —
  confirm it updates as new airlines show up in scrapes.
- **New: on-demand scrape path.** `/api/fares` now triggers a live scrape on a
  cache miss instead of just returning empty (verified working live — a fresh
  Toronto→Seoul search and a fresh Montreal→Hanoi search both correctly
  scraped, saved, and returned real results, with `scraped_now: true` in the
  response; a repeat of the same search then returned instantly from the DB
  with `scraped_now: false`). Verify: a genuinely zero-result route/date
  (confirmed via a real scrape) returns an empty list cleanly rather than an
  error; a scrape failure (e.g. Chrome unavailable) returns a clear 5xx with a
  useful `detail` message, not a silent empty result; concurrent requests for
  the *same* uncached route/date don't trigger redundant simultaneous scrapes
  (not yet tested — worth checking, since nothing currently de-dupes in-flight
  scrapes for the same key).
- **New: price trend endpoints** `/api/fares/by-date` and `/api/fares/history`
  (added 2026-08-27, backing the two chart tabs on the frontend). **Updated
  same day**: both now on-demand scrape too (originally shipped read-only,
  changed after the project owner explicitly asked for live-scrape-on-click
  on the by-date chart). Verified live: a fresh 2-date range scraped both
  dates correctly in ~53s total and included China Eastern (MU) and Hainan
  (HU) results — good real-world confirmation the Chinese-carrier pass
  actually surfaces MU on routes/dates where it has inventory, not just China
  Southern/Air China as earlier testing had shown. Verify: a repeat request
  for the same range returns instantly from cache (`scraped_now: false`); a
  range over `MAX_ONDEMAND_RANGE_DAYS` (14) returns a clean 422 rather than
  either scraping for 20+ minutes or silently truncating; one bad date within
  a range (scrape failure) doesn't blank out the rest of an otherwise-good
  range (best-effort, continues to the next date); `history` only scrapes
  when there's zero existing history (not on every visit) — confirm this
  stays true, since re-scraping on every page view would multiply Trip.com
  load a lot for little benefit (the whole point of that view is watching a
  price trend build over separate visits, not refreshing on every load).
  Verify: `end_date` before `start_date` returns a clean 422 (not a
  confusing empty result or crash); a route/date with zero flights anywhere
  in range returns an empty list with `scraped_now: true`, not an error;
  large date ranges (near the 14-day cap) don't silently truncate the
  *response* (no LIMIT applied to either query — confirm this stays true or
  gets a real pagination story before it becomes a problem at scale).

### QA 3 — End-to-end / UI
Tests the deployed site as a real user would:
- Full flow: pick a route/date in the UI, hit Compare, confirm the table
  renders correctly (right airline names, prices formatted, cheapest row
  highlighted, duration formatted as Xh Ym).
- No-data case: search a route/date nothing has scraped yet, confirm the UI
  shows the "Searching... may take up to 30 seconds" message, a real Chrome
  window visibly opens, and results appear afterward labeled "(just scraped
  live)" — verified working for a fresh route in the 2026-08-27 pass. Also
  confirm a route/date genuinely without any flights shows a clean "no
  flights found (just checked live)" message, not stuck on "Searching...".
- Cross-browser/mobile check of the comparison page (it's meant to be usable
  by a non-technical end user, the client's mother).
- **New: Price Trends charts.** Both tabs ("By departure date" / "By scrape
  date") render correctly with real multi-point seeded data — verified
  visually in the 2026-08-27 pass. Two real bugs already caught and fixed:
  (1) Chart.js's default axis/legend text color is dark gray, meant for a
  light background, and was nearly invisible against this page's dark theme
  — check this doesn't regress if the chart styling is touched again
  (`textColor`/`gridColor` in `renderTrendChart()`, which follow
  `prefers-color-scheme` since the page itself isn't locked to one theme).
  (2) The canvas ballooned to 7592px tall with real multi-airline data (a
  Chart.js `responsive: true` + no-fixed-height-parent feedback-loop bug) —
  looked completely blank/broken in a normal viewport. Fixed with a
  `.chart-wrap` container (fixed CSS height) + `maintainAspectRatio: false`
  — watch for this recurring if the chart layout changes.
  (3) The x-axis category order came from dataset insertion order, not a
  sort, so multi-airline data with different missing dates per airline
  produced a scrambled, non-chronological axis and chaotic zigzagging lines
  — fixed by explicitly building a sorted `labels` array. **Always verify
  the x-axis reads left-to-right in order after any change here**, not just
  that lines are visible — a "looks like a chart" render can still be
  wrong in this specific way.
  (4) Originally shipped plotting every airline found (10+ on some routes) —
  unreadably cluttered. Added an airline picker capped at 3 selections
  (`renderAirlinePicker()`), defaulting to the first 3 found. **First version
  of this picker disabled the other checkboxes once 3 were selected — user
  feedback: "it doesn't allow you to choose airlines," since clicking the
  specific airline you actually wanted (not one of the arbitrary default 3)
  just did nothing with no visible reason why.** Rewritten: no checkbox is
  ever disabled; checking a 4th automatically evicts the longest-selected
  one (oldest-first). Verify every airline is always clickable and produces
  an immediate, correct chart update — don't reintroduce a disabled state
  here even to "prevent over-selecting," it's a worse interaction than
  auto-eviction.
  Also verify the empty-state message on each tab, and that `end_date`
  before `start_date` shows the API's actual error text rather than a
  generic "API error 422".
- **New: pre-scrape airline selector, distinct from the ≤3 compare picker.**
  There are now *two* airline UIs per trend form — one before submit
  (`#*-scrape-airlines`, all 13 checked by default, controls what actually
  gets requested/scraped) and the existing post-fetch compare picker (caps
  at 3, controls what's charted from whatever came back). Don't conflate
  them in testing. Verified live: narrowing to 1 airline cut a real scrape
  from ~57-59s to ~6s; requesting a different airline subset for an
  already-scraped date/range doesn't trigger a wasteful rescrape (returns
  instantly, `scraped_now: false`, possibly empty if that airline was never
  actually scraped for that date — this is correct/expected, not a bug).
- **New: color consistency fix.** `colorForAirline(code)` used to take a
  `seenCodes` array built fresh per call — the picker (all airlines) and the
  chart (≤3 selected) built different arrays, so the same airline could get
  a different color in each, and selected airlines always landed at the
  same 2-3 palette slots in the chart regardless of which ones were picked
  (confirmed live: chart lines were always blue/orange/green no matter the
  selection). Fixed: `colorForAirline` is now a pure function keyed off each
  airline's fixed index in `TRACKED_AIRLINES` (14-color palette, one per
  tracked airline plus one spare). Verify by comparing a picker swatch's
  computed color to the matching chart dataset's `borderColor` — should be
  byte-identical, not just "look similar" — across different selections,
  not just the default 3.
- **New: data table + Excel export below each chart.** Verified live: table
  rows match the chart's plotted points; "Export to Excel" produces a real
  `.xlsx` (checked ZIP magic bytes `PK` + correct row count/values, not just
  "no exception thrown"). The exported timestamp column uses the same
  formatted display value as the table (not the raw ISO string) — verify
  this stays true if the row-building logic (`trendRows()`) changes, since
  the raw vs. formatted distinction is an easy thing to accidentally
  reintroduce a mismatch on.
- Post-deploy smoke test after every deploy: confirm the live site can reach
  the real Postgres DB and isn't silently serving stale/cached data. (The
  target is no longer necessarily a Vercel URL — see README's Architecture
  warning; smoke-test whatever host is actually chosen.)

## Cadence
QA 1's site-structure-drift check should run on some regular cadence (e.g.
before each scheduled scrape run is trusted for a new route), since it's the
single point of failure for the whole aggregator approach. QA 2 and QA 3 can
run continuously against whatever's already in the DB, independent of new
scraper runs.
