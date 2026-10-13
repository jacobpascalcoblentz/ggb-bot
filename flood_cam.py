#!/usr/bin/env python3
"""Capture Club Evexia Shoreline Hwy cam snapshots around big high tides.

Builds a dataset for calibrating FLOOD_LEVEL_FT and DRAIN_LAG in bot.py:
each snapshot is logged alongside the modeled Sausalito water level and the
live observed level at the Golden Gate, so we can see what level the water
was at when flooding started and when the road was clear again.

Standard library only, so the workflow can run it on the bare runner without
installing anything (it runs every 15 minutes and usually exits right away).

Usage:
  flood_cam.py --check     print "capture=true|false" (for $GITHUB_OUTPUT)
  flood_cam.py OUT_DIR     save snapshot + append to OUT_DIR/log.csv
"""

import csv
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
TIDE_STATION = "9414806"       # Sausalito (high/low predictions only)
ANOMALY_STATION = "9414290"    # San Francisco (live observations)
SNAPSHOT_URL = "https://ipcamlive.com/player/snapshot.php?alias=69334935af13a"

# Capture around high tides at least this high (after the anomaly correction).
# Lower than bot.py's alert threshold so we also see tides that just miss.
MIN_PEAK_FT = 5.5
# Capture while the modeled level is above this (well under the ~5.9ft flood
# level, so we see the water arrive), and for a while after it drops back
# below, to see how long the road takes to drain.
CAPTURE_LEVEL_FT = 4.8
DRAIN_WATCH = timedelta(minutes=90)
DAYLIGHT = (6, 19)  # the cam is too dark to judge flooding at night

NOAA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


def get_json(url):
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.load(resp)


def fetch_anomaly():
    """Mean observed - predicted at the Golden Gate over the past 24h (>= 0).

    Same correction as bot.fetch_tide_anomaly.
    """
    base = (f"{NOAA}?range=24&station={ANOMALY_STATION}"
            "&datum=MLLW&time_zone=lst_ldt&units=english&format=json")
    try:
        obs = get_json(base + "&product=water_level")
        pred = get_json(base + "&product=predictions")
    except Exception as e:
        print(f"Anomaly fetch failed: {e}", file=sys.stderr)
        return 0.0
    predicted = {p["t"]: float(p["v"]) for p in pred.get("predictions", []) if p.get("v")}
    residuals = [float(o["v"]) - predicted[o["t"]]
                 for o in obs.get("data", []) if o.get("v") and o["t"] in predicted]
    return max(0.0, sum(residuals) / len(residuals)) if residuals else 0.0


def fetch_extremes(today, anomaly):
    """Sausalito high/low predictions from yesterday through tomorrow."""
    begin = today - timedelta(days=1)
    data = get_json(
        f"{NOAA}?begin_date={begin:%Y%m%d}&range=72&interval=hilo"
        f"&station={TIDE_STATION}&product=predictions"
        "&datum=MLLW&time_zone=lst_ldt&units=english&format=json"
    )
    return [
        (datetime.strptime(p["t"], "%Y-%m-%d %H:%M"), float(p["v"]) + anomaly, p["type"])
        for p in data.get("predictions", [])
    ]


def level_at(extremes, t):
    """Half-cosine interpolation between high/low predictions (as in bot.py)."""
    for (ta, ha, _), (tb, hb, _) in zip(extremes, extremes[1:]):
        if ta <= t <= tb:
            phase = (t - ta) / (tb - ta)
            return hb + (ha - hb) * (1 + math.cos(math.pi * phase)) / 2
    return None


def capture_peak(extremes, now):
    """The big high tide whose capture window contains `now`, if any."""
    if not DAYLIGHT[0] <= now.hour < DAYLIGHT[1]:
        return None
    levels = [level_at(extremes, now), level_at(extremes, now - DRAIN_WATCH)]
    if not any(lv is not None and lv >= CAPTURE_LEVEL_FT for lv in levels):
        return None
    # The nearest high tide is the one we're watching
    t, h, _ = min((e for e in extremes if e[2] == "H"), key=lambda e: abs(e[0] - now))
    return (t, h) if h >= MIN_PEAK_FT else None


def observed_now():
    """Latest observed water level at the Golden Gate: (time, ft) or (None, None)."""
    try:
        # date=latest occasionally returns a stale reading; take the last
        # point of the past hour instead
        data = get_json(f"{NOAA}?range=1&station={ANOMALY_STATION}&product=water_level"
                        "&datum=MLLW&time_zone=lst_ldt&units=english&format=json")
        latest = [d for d in data["data"] if d.get("v")][-1]
        return latest["t"], float(latest["v"])
    except Exception as e:
        print(f"Observed level fetch failed: {e}", file=sys.stderr)
        return None, None


def main():
    now = datetime.now(TZ).replace(tzinfo=None)
    anomaly = fetch_anomaly()
    extremes = fetch_extremes(now.date(), anomaly)
    peak = capture_peak(extremes, now)

    if sys.argv[1:] == ["--check"]:
        print(f"capture={'true' if peak else 'false'}")
        return
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if not peak:
        print("Not near a big high tide; nothing to capture.")
        return

    out_dir = sys.argv[1]
    day_dir = os.path.join(out_dir, f"{now:%Y-%m-%d}")
    os.makedirs(day_dir, exist_ok=True)
    image = os.path.join(day_dir, f"{now:%H%M}.jpg")
    req = urllib.request.Request(SNAPSHOT_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(image, "wb") as f:
        f.write(resp.read())

    obs_time, obs_ft = observed_now()
    model_ft = level_at(extremes, now)
    peak_time, peak_ft = peak
    row = {
        "captured_at": f"{now:%Y-%m-%d %H:%M}",
        "image": os.path.relpath(image, out_dir),
        "model_sausalito_ft": f"{model_ft:.2f}" if model_ft is not None else "",
        "observed_sf_ft": f"{obs_ft:.2f}" if obs_ft is not None else "",
        "observed_sf_at": obs_time or "",
        "anomaly_ft": f"{anomaly:.2f}",
        "peak_at": f"{peak_time:%Y-%m-%d %H:%M}",
        "peak_ft": f"{peak_ft:.2f}",
        "flooded": "",  # filled in by hand when reviewing
    }
    log = os.path.join(out_dir, "log.csv")
    new = not os.path.exists(log)
    with open(log, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)
    print(f"Captured {row['image']}: model {row['model_sausalito_ft']}ft, "
          f"observed SF {row['observed_sf_ft']}ft (peak {peak_ft:.1f}ft at {peak_time:%H:%M})")


if __name__ == "__main__":
    main()
