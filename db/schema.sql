-- Shared fare schema for the flight fare comparison site.
-- Used by both the scraper layer (writes) and the Vercel web/API layer (reads).
-- Run this once against the Postgres database (Vercel Postgres / Neon) before first use.

CREATE TABLE IF NOT EXISTS fares (
    id             SERIAL PRIMARY KEY,
    airline_code   TEXT NOT NULL,          -- e.g. 'MU', 'AC', 'KE', 'CZ', 'CA', 'MF', '3U'
    airline_name   TEXT NOT NULL,          -- e.g. 'China Eastern'
    origin         TEXT NOT NULL,          -- IATA code, e.g. 'YVR'
    destination    TEXT NOT NULL,          -- IATA code, e.g. 'PVG'
    depart_date    DATE NOT NULL,
    return_date    DATE,                   -- NULL for one-way searches
    price          NUMERIC(10, 2) NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'CAD',
    is_direct      BOOLEAN NOT NULL,
    depart_time    TEXT,                   -- e.g. '13:25' (outbound leg, local time)
    arrive_time    TEXT,                   -- e.g. '17:40+1'
    duration_mins  INTEGER,                -- total outbound flight duration in minutes
    stops          INTEGER NOT NULL DEFAULT 0,
    raw_details    JSONB,                  -- full scraped payload for debugging / future fields
    scraped_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fares_route_date
    ON fares (origin, destination, depart_date);

CREATE INDEX IF NOT EXISTS idx_fares_scraped_at
    ON fares (scraped_at);
