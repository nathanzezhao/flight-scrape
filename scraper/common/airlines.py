"""Single source of truth for the 13 airlines this project always checks.

Previously duplicated by hand in scraper/tripcom.py (PRIORITY_AIRLINE_CODES)
and web/public/index.html (TRACKED_AIRLINES), kept in sync only by comment.
The frontend still keeps its own JS copy (no build step, can't import
Python) but should reference this file as the source of truth in its comment.
"""

PRIORITY_AIRLINES = [
    ("AC", "Air Canada"),
    ("MU", "China Eastern"),
    ("CZ", "China Southern"),
    ("CA", "Air China"),
    ("HU", "Hainan Airlines"),
    ("MF", "Xiamen Airlines"),
    ("3U", "Sichuan Airlines"),
    ("KE", "Korean Air"),
    ("HX", "Hong Kong Airlines"),
    ("CX", "Cathay Pacific"),
    ("BR", "EVA Air"),
    ("CI", "China Airlines"),
    ("JL", "Japan Airlines"),
]

PRIORITY_AIRLINE_CODES = [code for code, _ in PRIORITY_AIRLINES]
