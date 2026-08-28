# Flight Fare Comparison

Scrapes flight prices/times for Canada↔Asia routes and shows a side-by-side
price/time comparison for a given route and date. Built for a China Eastern
manager to compare fares across carriers (e.g. Vancouver → Shanghai) without
checking each airline's site by hand.

## Architecture

**⚠️ Deploy target changed — read this before assuming Vercel serverless works.**
`web/api/index.py` now does **on-demand scraping** on all three fare
endpoints (`/api/fares`, `/api/fares/by-date`, `/api/fares/history`): if a
search finds nothing stored, it triggers a live Trip.com scrape right then
(via `scraper/tripcom.py`, shared through one `_live_scrape()` helper) and
saves+returns the results — a single-date search typically takes a few
seconds, `/api/fares/by-date` can take several minutes for a wide range
since it scrapes one missing date at a time (see "Price trend charts"
below). That's a better experience than "no data, go run a CLI command
yourself" — but it means the API process itself needs a real, visible Chrome
browser available, the same requirement the standalone scraper has. **Vercel's
serverless Python functions cannot do this** (no persistent browser, no
display, execution time limits) — `web/vercel.json` and the Vercel deploy
steps below predate this feature and describe a *pure read-only* API that no
longer matches the code. Until this is resolved, treat the practical deploy
target as **a persistent machine with a real Chrome + display** (could still
be reached over a network the same way a Vercel deploy would be, e.g. via a
reverse proxy/tunnel — just not Vercel's own serverless functions) rather than
literal `vercel --prod`. See `ENGINEERING.md` for next steps here.

Two pieces sharing one Postgres database:

- **`web/`** — the public site. A static comparison page (`web/public/`) plus
  an API (`web/api/`, FastAPI) that queries stored fares and — new — falls
  back to a live scrape on a cache miss (see warning above).
- **`scraper/`** — the data collection layer. `scraper/tripcom.py` uses
  [DrissionPage](https://github.com/g1879/DrissionPage) to drive a real Chrome
  browser against **Trip.com (Ctrip)**, a flight aggregator, and writes results
  to the shared Postgres DB. Runs both standalone on a schedule (cron /
  Windows Task Scheduler) *and* on-demand, triggered by `web/api/index.py`.

Both sides read/write the same `fares` table (`db/schema.sql`).

**Why one aggregator instead of one scraper per airline**: the original plan
scraped each airline's own site directly, but live investigation showed each
one needs hours of bespoke reverse-engineering (cookie walls, JS-driven
autocomplete, anti-bot measures) — genuinely a multi-session effort per
airline. Trip.com lists many airlines (all the Chinese carriers, Air Canada,
Korean Air, and more) for one route/date search on a single well-structured
results page, so one scraper covers what would otherwise be 7+ separate ones.
The tradeoff: aggregator prices can lag or omit an airline's own site-exclusive
fares/promos, so treat this as a "what's roughly cheapest across carriers"
tool rather than a booking-accurate one.

**Current status**: `scraper/tripcom.py` is implemented and **verified working
against the live site** across many real searches (multiple Canada→Asia
routes, near-term and far-future dates, zero-result routes). Known
limitations (see `scraper/tripcom.py` docstring for full details):
- **Headless mode is blocked by the site** — verified live that
  `ChromiumOptions().headless()` gets served an empty page. Scheduled runs
  need a real, visible Chrome window (a logged-in desktop session, or a
  virtual display like Xvfb), not `--headless`.
- Round-trip search isn't implemented yet (one-way only).
- **Trip.com's default results view is incomplete by design** — it only
  renders a curated subset (e.g. 8 of 38 actual flights on one real search),
  hiding the rest behind per-airline sidebar filters with no visible "load
  more". `search()` does one extra filtered pass per Chinese carrier (China
  Eastern, China Southern, Air China, Xiamen, Sichuan, Hainan) not already in
  the default view, since that's this project's specific interest — verified
  live this successfully surfaces China Southern and Air China when they have
  inventory (~75% of test runs; the rest is either genuine live-inventory
  absence or an occasional flaky filter click, not distinguished). China
  Eastern specifically was confirmed *absent from Trip.com's inventory
  entirely* for the YVR→PVG test route/date — not a scraper gap, the airline
  just isn't sold through Trip.com's international storefront for that
  search. This is a real "what Trip.com sells" limitation, not something more
  scraping effort fixes — if China Eastern coverage specifically matters, the
  fallback is scraping ceair.com directly (the original per-airline plan,
  scoped to just this one airline — see project history for why that's a
  multi-hour undertaking per site).
- General exhaustive coverage (every airline, not just the Chinese-carrier
  subset) isn't implemented — would need the same per-filter reload pattern
  applied to every airline in the sidebar, at a real time cost (each extra
  airline is a full page reload, ~5-8s).

## Price trend charts

Besides the single-date comparison table, the site has two price trend
charts (`web/public/index.html`, `GET /api/fares/by-date` and
`GET /api/fares/history` in `web/api/index.py`/`web/api/_db.py`). Both
on-demand scrape, same as `/api/fares` — every fare endpoint now shares one
`_live_scrape()` helper in `web/api/index.py`:
- **By departure date** — one line per airline across a date range, X = the
  date you'd fly. Answers "which day is cheapest." Any date in the range
  with no stored data gets scraped live, one date at a time (real Chrome
  window, up to ~60s each) — so a wide range can take several minutes.
  Capped at `MAX_ONDEMAND_RANGE_DAYS` (14) per request; a wider range gets a
  clean 422 asking you to narrow it or run it again for the next stretch,
  rather than either silently truncating or letting one request scrape for
  20+ minutes.
- **By scrape date (price history)** — one line per airline for one fixed
  departure date, X = when each scrape happened. Answers "should I book now
  or wait." Only scrapes live if there's *no* history at all yet (seeds the
  first point); once any history exists it's read-only, so the trend
  actually builds from repeat visits over days/weeks rather than every page
  load re-scraping the same date.

Charting uses [Chart.js](https://www.chartjs.org/) via a plain CDN
`<script>` tag (`web/public/index.html`) — **not** the Bklit UI library the
project owner originally asked about. Bklit UI (bklit.com /
github.com/bklit/bklit-ui) is legitimate and MIT-licensed, but it's a React
component library distributed through the shadcn registry (source gets
copied into a React/Next.js project) — this site's frontend is a single
static HTML/JS file with no build tooling and no Node.js installed on the
dev machine at the time this was built, so adopting it for real would mean
installing Node.js and migrating the whole frontend to React first. Chart.js
gets equivalent multi-line time-series comparison with zero new tooling.
Revisit this tradeoff if the frontend ever does get migrated to React for
other reasons.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r scraper/requirements.txt -r web/requirements.txt
```

Without `DATABASE_URL` set, both the scraper and the web API fall back to a
local SQLite file (`dev_fares.db`) so you can develop without a real Postgres
instance.

Run the web API + frontend locally, on the same origin (needed for the
frontend's relative `/api/...` calls to work — a bare `uvicorn api.index:app`
only serves the API, not `public/`):

```bash
python3 -m uvicorn web.api.index:app --app-dir . --reload
```

This works because `web/api/index.py` adds the repo root to `sys.path` (for
the on-demand-scrape import of `scraper`) — but it does NOT serve
`web/public/`. For a real combined preview matching production, mount
`StaticFiles` for `web/public/` onto the `app` in a small throwaway script, or
resolve the Vercel-vs-persistent-host deploy question above and use that
platform's own dev server.

Run the scraper:

```bash
python3 -m scraper.run_scrapers --origin YVR --destination PVG --depart-date 2026-09-26
```

(Run with `-m` from the repo root so the `scraper` package resolves. This pops
a real Chrome window — that's expected, see the headless note above.)

## Deploying

**Not done yet** — the on-demand-scrape feature means the plan below (pure
Vercel serverless) doesn't work as-is; this needs to be revisited (see the
Architecture warning above and `ENGINEERING.md`) before an actual production
deploy. Recorded here for reference / as a starting point:

### Web layer — was planned as Vercel, now needs a persistent host instead
The original plan (`vercel link` + Vercel Postgres/Neon + `vercel --prod`)
assumed a purely read-only API, which is no longer true. Whatever runs
`web/api/index.py` in production needs real Chrome + a display, e.g.:
- The simplest option: run it on the same machine the end user (the mother)
  actually uses, as a local app she opens in her own browser — no separate
  hosting needed at all.
- Or a persistent server/VM with a virtual display (Xvfb) exposed via a
  reverse proxy — more infra, but a real "hosted site" rather than a local app.
`db/schema.sql` still needs to be run once against whatever Postgres instance
gets used (Vercel Postgres, Neon, or self-hosted), and `DATABASE_URL` set
accordingly — that part of the plan is unaffected.

### Scraper layer (a real machine — for the scheduled/background path)
Independent of the on-demand path above, `scraper/run_scrapers.py` can still
run on a schedule (cron / Windows Task Scheduler) to pre-populate common
routes so most searches hit the DB instead of triggering a live scrape:
1. Set `DATABASE_URL` (same value the web layer uses) in a local `.env` file
   (see `.env.example`).
2. Schedule `python3 -m scraper.run_scrapers ...` for the routes/dates you
   care about. Because headless mode doesn't work, this needs a real desktop
   session it can open a Chrome window in — e.g. Windows Task Scheduler
   configured to "run only when user is logged on" (not "whether user is
   logged on or not"), or a machine running under a virtual display (Xvfb).

## Repo layout

```
web/                  frontend + API (deploy target TBD — see Deploying)
  public/index.html   comparison UI + price trend charts (Chart.js via CDN)
  api/index.py         FastAPI app (exports `app`) — reads DB, falls back to live scrape
  api/_db.py            DB read queries (fares, airlines, by-date + history trends)
scraper/              needs a real Chrome + display wherever it runs
  tripcom.py            Trip.com aggregator scraper (implemented, verified live)
  run_scrapers.py       CLI entrypoint (for scheduled/background pre-population)
  common/               shared schema, DB models, DB connection + save helpers
db/schema.sql         shared Postgres schema
ENGINEERING.md        engineering team roles/responsibilities
QA.md                 QA team roles/test plan
```
