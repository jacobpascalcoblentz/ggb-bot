#!/usr/bin/env python3
"""Fit bot.py's FLOOD_LEVEL_FT and DRAIN_LAG from flood cam captures.

Reads OUT_DIR/log.csv (written by flood_cam.py), finds when each high tide
started flooding the road and when it was clear again, and writes
OUT_DIR/calibration.md. Prints "ready=true|false" (for $GITHUB_OUTPUT): ready
once enough tides bracket both the onset and the clearing and agree with
each other.

Levels are in the same units bot.py works in: the modeled Sausalito level
(NOAA prediction + anomaly), not the observed Golden Gate level, so the fit
drops straight into bot.py.

Standard library only, like flood_cam.py.
"""

import csv
import os
import re
import statistics
import sys
from datetime import datetime, timedelta

# How many tides with a fully bracketed flood we want, and how tightly they
# must agree, before calling the calibration good.
MIN_EVENTS = 3
MAX_LEVEL_SPREAD_FT = 0.4
MAX_LAG_SPREAD = timedelta(minutes=45)


def current_constants():
    """FLOOD_LEVEL_FT and DRAIN_LAG as currently set in bot.py."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
    level = float(re.search(r"^FLOOD_LEVEL_FT = ([\d.]+)", src, re.M).group(1))
    lag = int(re.search(r"^DRAIN_LAG = timedelta\(minutes=(\d+)\)", src, re.M).group(1))
    return level, timedelta(minutes=lag)


def load_events(log_path):
    """Captures grouped by the high tide they belong to, in time order."""
    events = {}
    with open(log_path, newline="") as f:
        for row in csv.DictReader(f):
            if row["flooded"] not in ("yes", "no") or not row["model_sausalito_ft"]:
                continue
            events.setdefault(row["peak_at"], []).append({
                "t": datetime.strptime(row["captured_at"], "%Y-%m-%d %H:%M"),
                "level": float(row["model_sausalito_ft"]),
                "flooded": row["flooded"] == "yes",
                "peak_ft": float(row["peak_ft"]),
            })
    for caps in events.values():
        caps.sort(key=lambda c: c["t"])
    return events


def falling_crossing(caps, level):
    """When the modeled level drops through `level`, interpolated between captures."""
    for a, b in zip(caps, caps[1:]):
        if a["level"] >= level > b["level"]:
            frac = (a["level"] - level) / (a["level"] - b["level"])
            return a["t"] + (b["t"] - a["t"]) * frac
    return None


def analyze(caps):
    """Onset level and clearing time for one tide.

    The flood is the span from the first to the last flooded capture, with
    single flooded frames at the edges ignored as likely misreads. Onset and
    clearing are only known if there's a dry capture on each side; we take
    the midpoint between the dry and flooded captures.
    """
    flags = [c["flooded"] for c in caps]
    runs = [i for i in range(len(caps) - 1) if flags[i] and flags[i + 1]]
    result = {"peak_ft": caps[0]["peak_ft"], "captures": len(caps),
              "flooded": any(flags), "onset_level": None, "onset_t": None, "clear_t": None}
    if not runs:
        return result
    first, last = runs[0], runs[-1] + 1
    if first > 0 and not flags[first - 1]:
        dry, wet = caps[first - 1], caps[first]
        result["onset_level"] = (dry["level"] + wet["level"]) / 2
        result["onset_t"] = dry["t"] + (wet["t"] - dry["t"]) / 2
    if last < len(caps) - 1 and not flags[last + 1]:
        wet, dry = caps[last], caps[last + 1]
        result["clear_t"] = wet["t"] + (dry["t"] - wet["t"]) / 2
    result["caps"] = caps
    return result


def fmt(t):
    return t.strftime("%-I:%M%p").lower() if t else "—"


def main():
    out_dir = sys.argv[1]
    log = os.path.join(out_dir, "log.csv")
    if not os.path.exists(log):
        print("ready=false")
        return

    cur_level, cur_lag = current_constants()
    results = {peak: analyze(caps) for peak, caps in sorted(load_events(log).items())}

    onset_levels = [r["onset_level"] for r in results.values() if r["onset_level"] is not None]
    fit_level = statistics.median(onset_levels) if onset_levels else None

    # Drain lag: clearing time minus when the water fell back below the
    # fitted flood level
    lags = []
    for r in results.values():
        if fit_level is None or not r["clear_t"]:
            continue
        fell = falling_crossing(r["caps"], fit_level)
        if fell:
            r["lag"] = r["clear_t"] - fell
            lags.append(r["lag"])
    fit_lag = statistics.median(lags) if lags else None

    complete = [r for r in results.values() if r["onset_level"] is not None and r.get("lag") is not None]
    ready = (
        len(complete) >= MIN_EVENTS
        and max(onset_levels) - min(onset_levels) <= MAX_LEVEL_SPREAD_FT
        and max(lags) - min(lags) <= MAX_LAG_SPREAD
    )

    def lag_min(td):
        return f"{td.total_seconds() / 60:.0f} min" if td is not None else "—"

    lines = [
        "# Flood calibration",
        "",
        f"**Status: {'ready ✅' if ready else 'collecting'}** — {len(complete)}/{MIN_EVENTS} tides "
        f"with a fully observed flood (onset and clearing both bracketed by dry captures).",
        "",
        "| | bot.py now | fitted |",
        "|---|---|---|",
        f"| `FLOOD_LEVEL_FT` | {cur_level:.1f} | "
        f"{f'{fit_level:.2f}' if fit_level is not None else '—'} "
        f"{f'(spread {max(onset_levels) - min(onset_levels):.2f}ft, n={len(onset_levels)})' if onset_levels else ''} |",
        f"| `DRAIN_LAG` | {lag_min(cur_lag)} | {lag_min(fit_lag)} "
        f"{f'(spread {lag_min(max(lags) - min(lags))}, n={len(lags)})' if lags else ''} |",
        "",
        f"Ready when ≥{MIN_EVENTS} tides are fully observed, onset levels agree within "
        f"{MAX_LEVEL_SPREAD_FT}ft and drain lags within {lag_min(MAX_LAG_SPREAD)}.",
        "",
        "## Tides",
        "",
        "| peak | peak ft | captures | flooded | onset | onset level | clear | drain lag |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for peak, r in results.items():
        onset_level = f"{r['onset_level']:.2f}" if r["onset_level"] is not None else "—"
        lines.append(
            f"| {peak} | {r['peak_ft']:.2f} | {r['captures']} | {'yes' if r['flooded'] else 'no'} | "
            f"{fmt(r['onset_t'])} | {onset_level} | {fmt(r['clear_t'])} | {lag_min(r.get('lag'))} |"
        )
    lines += [
        "",
        "Levels are modeled Sausalito ft MLLW (NOAA prediction + anomaly), the units bot.py uses. "
        "Haiku's `flooded` labels in log.csv can be corrected by hand; this report is rebuilt on every capture.",
        "",
    ]
    with open(os.path.join(out_dir, "calibration.md"), "w") as f:
        f.write("\n".join(lines))
    print(f"ready={'true' if ready else 'false'}")


if __name__ == "__main__":
    main()
