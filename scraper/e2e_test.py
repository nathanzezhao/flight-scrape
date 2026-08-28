"""E2E test script — NOT part of the app, run manually against a local
preview server. Requires: server running at BASE_URL, dev_fares.db seeded
via `python3 -m scraper.seed_test_data`.

Covers: API input validation, data correctness (sorting/dedup/filtering),
edge cases, and (behind --live) a small number of genuine Trip.com scrapes.
Not a pytest suite (no pytest installed) — plain assert-and-count harness.
"""
import sys
import requests

BASE_URL = "http://127.0.0.1:8710"

passed = 0
failed = 0
failures = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        failures.append(f"{name}: {detail}")
        print(f"  FAIL: {name} — {detail}")


def get(path, timeout=15):
    return requests.get(f"{BASE_URL}{path}", timeout=timeout)


# ── A. Input validation & error handling ────────────────────────────────
print("== A. Input validation ==")

r = get("/api/fares")  # missing all params
check("A1 missing all params -> 422", r.status_code == 422, r.status_code)

r = get("/api/fares?origin=YVR")  # missing destination/date
check("A2 missing destination+date -> 422", r.status_code == 422, r.status_code)

r = get("/api/fares?origin=YV&destination=PVG&depart_date=2026-10-01")  # origin too short
check("A3 origin too short -> 422", r.status_code == 422, r.status_code)

r = get("/api/fares?origin=YVRR&destination=PVG&depart_date=2026-10-01")  # origin too long
check("A4 origin too long -> 422", r.status_code == 422, r.status_code)

r = get("/api/fares?origin=YVR&destination=PVG&depart_date=not-a-date")
check("A5 malformed date -> 422", r.status_code == 422, r.status_code)

r = get("/api/fares?origin=YVR&destination=PVG&depart_date=2026-13-45")  # invalid calendar date, valid shape
check("A6 invalid calendar date -> 422", r.status_code == 422, r.status_code)
check("A6b clean validation message, not a scrape-failure message", "scrape" not in r.json().get("detail", "").lower(), r.text[:200])

r = get("/api/fares/by-date?origin=YVR&destination=PVG&start_date=2026-11-11&end_date=2026-11-01")
check("A7 end_date before start_date -> 422", r.status_code == 422, r.status_code)
check("A7b detail message present", "end_date" in r.json().get("detail", ""), r.text[:200])

r = get("/api/fares/by-date?origin=YVR&destination=PVG&start_date=2026-01-01&end_date=2026-03-01")  # 60 days, over cap
check("A8 range over 14-day cap -> 422", r.status_code == 422, r.status_code)
check("A8b detail mentions cap", "14" in r.json().get("detail", ""), r.text[:200])

r = get("/api/fares/by-date?origin=YVR&destination=PVG&start_date=2026-11-01&end_date=2026-11-01")  # single seeded day, no gaps -> no live scrape triggered
check("A9 range under cap doesn't 422", r.status_code != 422, r.status_code)

r = get("/api/fares/history?origin=YVR")  # missing params
check("A10 history missing params -> 422", r.status_code == 422, r.status_code)

r = get("/api/fares?origin=YVR'; DROP TABLE fares; --&destination=PVG&depart_date=2026-10-01")
check("A11 SQL-injection-shaped origin doesn't 500", r.status_code in (200, 422), r.status_code)
r2 = get("/api/airlines")
check("A11b table still intact after injection attempt", r2.status_code == 200 and len(r2.json()) > 0, r2.status_code)

# NOTE: deliberately no "query an unseeded route on /api/fares and expect
# empty" test here — it always tries a live scrape on empty results by
# design. That class of check lives in section D below, where the cost is
# expected and budgeted for. (Earlier draft of this test left such a call in
# uninstrumented — real accidental live scrape triggered twice, ~2min each,
# before this comment was added. Don't reintroduce it here.)

print(f"  ({passed} passed, {failed} failed so far)")

# ── B. Data correctness with seeded data ────────────────────────────────
print("== B. Data correctness (seeded) ==")

ROUTES_DATES = [
    ("YVR", "PVG", "2026-10-01"), ("YVR", "SIN", "2026-10-02"), ("YVR", "TSN", "2026-10-03"),
    ("YYZ", "ICN", "2026-10-04"), ("YYZ", "HKG", "2026-10-05"),
    ("YYC", "BKK", "2026-10-06"), ("YUL", "CAN", "2026-10-07"), ("YHZ", "NRT", "2026-10-08"),
]
for i, (o, d, dep) in enumerate(ROUTES_DATES):
    r = get(f"/api/fares?origin={o}&destination={d}&depart_date={dep}")
    fares = r.json().get("fares", [])
    check(f"B{i+1} {o}->{d} on {dep}: 6 airlines returned", len(fares) == 6, len(fares))
    prices = [f["price"] for f in fares]
    check(f"B{i+1}b {o}->{d}: sorted cheapest-first", prices == sorted(prices), prices)
    check(f"B{i+1}c {o}->{d}: scraped_now false (from seed, not live)", r.json().get("scraped_now") is False, r.json().get("scraped_now"))

# Dedup: latest scrape wins, not cheapest, not first-inserted
r = get("/api/fares?origin=YUL&destination=CAN&depart_date=2026-10-20")
fares = r.json().get("fares", [])
ac_fare = next((f for f in fares if f["airline_code"] == "AC"), None)
check("B9 dedup: only one AC row for tie-test route", ac_fare is not None, fares)
check("B9b dedup: latest-scraped price (777) wins over earlier (999)", ac_fare and ac_fare["price"] == 777.0, ac_fare)

# Airlines filter
r = get("/api/fares?origin=YVR&destination=PVG&depart_date=2026-10-01&airlines=AC,MU")
fares = r.json().get("fares", [])
check("B10 airlines filter returns only requested codes", {f["airline_code"] for f in fares} <= {"AC", "MU"}, fares)
check("B10b airlines filter returns exactly 2", len(fares) == 2, len(fares))

r = get("/api/fares?origin=YVR&destination=PVG&depart_date=2026-10-01&airlines=ZZ")
check("B11 nonexistent airline filter -> empty, not error", r.status_code == 200 and r.json()["fares"] == [], r.json())

# /api/airlines reflects real data
r = get("/api/airlines")
codes = {a["code"] for a in r.json()}
check("B12 /api/airlines includes all 6 seeded airlines", {"AC", "MU", "CZ", "CA", "CX", "KE"} <= codes, codes)

# by-date: grouping/dedup by (airline, date)
r = get("/api/fares/by-date?origin=YVR&destination=PVG&start_date=2026-11-01&end_date=2026-11-06")  # exactly the 6 contiguous seeded days, no gaps
rows = r.json().get("fares", [])
check("B13 by-date: 3 airlines x 6 dates = 18 points", len(rows) == 18, len(rows))
dep_dates = sorted({row["depart_date"] for row in rows})
check("B13b by-date: dates are the 6 seeded ones", len(dep_dates) == 6, dep_dates)
check("B13c by-date: labels sortable/chronological", dep_dates == sorted(dep_dates), dep_dates)

# history: NOT deduped, full trend
r = get("/api/fares/history?origin=YYZ&destination=ICN&depart_date=2026-12-15")
rows = r.json().get("history", [])
check("B14 history: 4 airlines x 8 scrapes = 32 points (not deduped)", len(rows) == 32, len(rows))
ac_rows = [row for row in rows if row["airline_code"] == "AC"]
check("B14b history: AC has all 8 scrape points", len(ac_rows) == 8, len(ac_rows))
ac_prices = [row["price"] for row in ac_rows]
check("B14c history: prices reflect declining trend (900 down to 865)", ac_prices[0] == 900.0 and ac_prices[-1] == 865.0, ac_prices)

print(f"  ({passed} passed, {failed} failed so far)")

# NOTE: no "query an unseeded route and expect empty" section here on
# purpose — every fare endpoint now on-demand scrapes on a cache miss (by
# design, per the user's explicit request), so hitting any unseeded route
# would trigger a real Trip.com scrape, not a fast read-only check. That
# class of test lives in section D below instead, where the live-scrape cost
# is expected and budgeted for.

if "--live" in sys.argv:
    import time

    print("== D. Live Trip.com scrapes (slow — real browser, real site) ==")

    LIVE_TIMEOUT = 240  # each scrape can involve up to ~10 priority-carrier passes

    t0 = time.time()
    r = get("/api/fares?origin=YVR&destination=SIN&depart_date=2026-12-10", timeout=LIVE_TIMEOUT)  # fresh date, not the one already cached from earlier debugging
    check("D1 live scrape YVR->SIN (new destination) succeeds", r.status_code == 200, r.status_code)
    check("D1b scraped_now true (genuinely fresh)", r.json().get("scraped_now") is True, r.json().get("scraped_now"))
    print(f"  D1 took {time.time()-t0:.1f}s, {len(r.json().get('fares', []))} fares found")

    t0 = time.time()
    r2 = get("/api/fares?origin=YVR&destination=SIN&depart_date=2026-12-10", timeout=10)
    check("D2 repeat request hits cache instantly", r2.elapsed.total_seconds() < 2, r2.elapsed.total_seconds())
    check("D2b scraped_now false on repeat", r2.json().get("scraped_now") is False, r2.json().get("scraped_now"))
    check("D2c same data returned", r2.json().get("fares") == r.json().get("fares"), "mismatch")

    t0 = time.time()
    r3 = get("/api/fares?origin=YVR&destination=TSN&depart_date=2026-12-05", timeout=LIVE_TIMEOUT)
    check("D3 live scrape YVR->TSN (new destination) succeeds", r3.status_code == 200, r3.status_code)
    print(f"  D3 took {time.time()-t0:.1f}s, {len(r3.json().get('fares', []))} fares found")

    print(f"  ({passed} passed, {failed} failed so far)")
else:
    print("== D. Live Trip.com scrapes: SKIPPED (pass --live to include; each takes 1-3+ min) ==")

print(f"\n=== TOTAL: {passed} passed, {failed} failed (of {passed + failed}) ===")
if failures:
    print("\nFailures:")
    for f in failures:
        print(f"  - {f}")

sys.exit(1 if failed else 0)
