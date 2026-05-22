import logging

import requests

import config

log = logging.getLogger(__name__)

TREND_ARROWS = {
    "DoubleUp": "↑↑",
    "SingleUp": "↑",
    "FortyFiveUp": "↗",
    "Flat": "→",
    "FortyFiveDown": "↘",
    "SingleDown": "↓",
    "DoubleDown": "↓↓",
}


def fetch_bg():
    """Fetch last 4 BG entries from Nightscout.

    Returns a dict:
      bg            float  current BG in mmol/L
      direction     str    raw Nightscout trend string
      trend_arrow   str    display arrow character
      delta         float  mmol/L change vs previous reading
      previous_bgs  list   [bg-5min, bg-10min, bg-15min] in mmol/L
      timestamp     float  unix timestamp of current reading
    """
    url = f"{config.NIGHTSCOUT_URL}/api/v1/entries.json"
    params = {"count": 4, "token": config.NIGHTSCOUT_TOKEN}

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()

    entries = resp.json()
    if len(entries) < 2:
        raise ValueError(
            f"Nightscout returned only {len(entries)} entr(ies) - need at least 2"
        )

    current = entries[0]
    prev = entries[1]

    current_bg = current["sgv"] / 18.0
    delta = (current["sgv"] - prev["sgv"]) / 18.0
    direction = current.get("direction", "Flat")

    import time
    reading_age_min = (time.time() - current["date"] / 1000) / 60
    if reading_age_min > 15:
        log.warning(
            f"Nightscout reading is {reading_age_min:.1f} min old - CGM may be disconnected"
        )

    return {
        "bg": current_bg,
        "direction": direction,
        "trend_arrow": TREND_ARROWS.get(direction, "?"),
        "delta": delta,
        "previous_bgs": [e["sgv"] / 18.0 for e in entries[1:]],
        "timestamp": current["date"] / 1000,
    }
