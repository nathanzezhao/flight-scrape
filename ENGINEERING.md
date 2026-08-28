# Engineering Team

A 3-person team is enough to carry this project from its current state (a
working, verified Trip.com scraper + deployable web layer) to a polished
product: 1 TPM + 2 software engineers.

## Roles

### TPM (Technical Program Manager)
Owns scope and coordination between the scraper and web-layer workstreams,
since they land on different machines and different schedules.

Responsibilities:
- **Resolve the deploy target question, top priority.** `web/api/index.py` now
  does on-demand scraping on a cache miss, which requires a real Chrome +
  display wherever it runs — the original "web layer on Vercel serverless"
  plan no longer works as-is (see README's Architecture warning and
  Deploying section). Decide between: running the whole app locally on the
  end user's own machine (simplest, no hosting), or a persistent server/VM
  with a virtual display behind a reverse proxy (more infra, a real hosted
  site). This blocks any real production deploy.
- Own the scraper backlog: round-trip search and anti-bot/rate-limit
  resilience for repeated runs (see `scraper/tripcom.py` docstring's "NOT yet
  handled" section) — track these as they're picked up. (Multi-stop accuracy
  was already fixed in the 2026-08-27 QA pass — don't re-flag it as an
  approximation.)
- Watch for **site-structure drift**: this whole approach depends on Trip.com's
  DOM (class names, `data-testid` attributes) not changing. If the scraper
  starts returning 0 results or garbage, that's the first thing to check
  against the live site.
- Decide if/when to revisit the paid-API alternative (Amadeus, Skyscanner via
  RapidAPI) if aggregator-scraping accuracy, reliability, or the on-demand
  latency (a cache-miss search takes several seconds while Chrome scrapes
  live) becomes a problem.

### Software Engineer 1 — Scraper / data layer
Owns `scraper/tripcom.py` and `scraper/common/`.

Responsibilities:
- Extend the scraper: round-trip search (`triptype=rt` + return date, not yet
  implemented), pagination if a route has more results than the initial page
  loads.
- Harden it for unattended/repeated runs (both the scheduled CLI path and the
  new on-demand path triggered from the web layer): back off / retry if
  Trip.com rate-limits or challenges repeated automated requests, and keep the
  "must run in a real visible Chrome window, not headless" constraint in mind
  for any deploy automation.
- Keep `scraper/common/models.py` / `db/schema.sql` in sync if new fields are
  needed (e.g. layover city, fare class).
- Set up and verify the scheduled run on a real machine, writing to the
  shared Postgres DB.

### Software Engineer 2 — Web layer
Owns everything under `web/`.

Responsibilities:
- Price trend charts (by departure date, and by scrape date/price history)
  shipped 2026-08-27 — see README's "Price trend charts" section. Follow-ups
  still open: an airline filter/picker on the charts (currently plots every
  airline present in the data with no way to narrow it down), a mobile-
  friendly layout for the two-tab chart panels, and revisiting the date-range
  picker UX for "by departure date" (currently plain start/end date inputs,
  no indication of which dates in range actually have data before submitting).
- Extend `web/api/index.py` / `web/api/_db.py` as new query needs come up
  (e.g. round-trip once the scraper supports it).
- Work with the TPM on the deploy-target decision (see TPM section) and
  implement whichever option is chosen — this replaces the original "own the
  Vercel deploy" responsibility, since pure Vercel serverless no longer fits
  now that `/api/fares` can trigger a live scrape.
- Keep the frontend/API in sync with whatever fields Software Engineer 1 adds
  to the common fare schema.
- If Bklit UI (the React chart library the project owner originally asked
  about) ever becomes worth adopting for real — e.g. if the frontend gets
  migrated to React for unrelated reasons — revisit the "why Chart.js
  instead" tradeoff documented in the README; nothing here rules it out
  permanently, it just didn't fit the current static-HTML frontend.

## How the current state maps to this team
`scraper/tripcom.py` is implemented and verified against the live site, and
the web layer (DB schema, FastAPI API — including on-demand scrape fallback,
frontend) is built and locally verified end-to-end with real scraped data,
including the on-demand path itself (tested live against a fresh route with
no prior data). Both engineers can work in parallel from here: the scraper
engineer doesn't need the web layer running to test scraping, and the web
engineer doesn't need to run real scrapes to build out the UI/API — they can
seed the dev SQLite DB directly for most work.
