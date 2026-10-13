#!/usr/bin/env python3
"""Capture Club Evexia Shoreline Hwy cam snapshots around big high tides.

Builds a dataset for calibrating FLOOD_LEVEL_FT and DRAIN_LAG in bot.py:
each snapshot is logged alongside the modeled Sausalito water level and the
live observed level at the Golden Gate, so we can see what level the water
was at when the path started flooding and when it was clear again.

Haiku labels each snapshot by comparing the path against a reference
picture of it dry, taken automatically at a daylight low tide. Until that
reference exists, labels are left blank and filled in once it does. Labels
set by hand (label_source "rider") are never overwritten.

--check uses only the standard library, so the workflow can run it on the
bare runner (it runs every 15 minutes and usually exits right away).
Capturing needs Pillow to crop the path out of the frame.

Usage:
  flood_cam.py --check     print "capture=true|false" (for $GITHUB_OUTPUT)
  flood_cam.py OUT_DIR     save snapshot + update OUT_DIR/log.csv
"""

import base64
import csv
import io
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
# The path was flooded at a modeled 4.9ft on 2026-10-13, so this is well
# under bot.py's alert threshold.
MIN_PEAK_FT = 4.5
# Capture while the modeled level is above this, so we see the water arrive,
# and for a while after it drops back below, to see how long the path takes
# to drain.
CAPTURE_LEVEL_FT = 3.0
DRAIN_WATCH = timedelta(hours=2)
# At or below this level in daylight, the path should be dry: capture a
# reference picture. The lowest one seen is kept.
DRY_REF_LEVEL_FT = 2.5
# Unlabeled rows to label per run once the dry reference exists
RELABEL_BATCH = 40
FIELDS = [
    "captured_at", "image", "model_sausalito_ft", "observed_sf_ft",
    "observed_sf_at", "anomaly_ft", "peak_at", "peak_ft", "flooded",
    "label_source", "notes",
]
DAYLIGHT = (6, 19)  # the cam is too dark to judge flooding at night

NOAA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
VISION_MODEL = "claude-haiku-5-5"
# Where the Mill Valley-Sausalito path shows in the 1280x720 frame: the strip
# behind the garden statuary yard along Shoreline Hwy, left of the big tree
# (cells D4:G4 of a 16x9 grid). It's too small for Haiku to judge in the full
# frame, so we also send an enlarged crop.
PATH_BOX = (240, 240, 560, 320)
CROP_SCALE = 3


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


def capture_mode(extremes, now):
    """What to capture now: ("tide", (peak_time, peak_ft)), ("dry", None) or None."""
    if not DAYLIGHT[0] <= now.hour < DAYLIGHT[1]:
        return None
    level = level_at(extremes, now)
    if level is not None and level <= DRY_REF_LEVEL_FT:
        return "dry", None
    levels = [level, level_at(extremes, now - DRAIN_WATCH)]
    if not any(lv is not None and lv >= CAPTURE_LEVEL_FT for lv in levels):
        return None
    # The nearest high tide is the one we're watching
    t, h, _ = min((e for e in extremes if e[2] == "H"), key=lambda e: abs(e[0] - now))
    return ("tide", (t, h)) if h >= MIN_PEAK_FT else None


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


def path_crop_b64(image_path):
    """The path area of a snapshot, enlarged, as base64 JPEG."""
    from PIL import Image  # only needed when capturing, not for --check

    with Image.open(image_path) as im:
        crop = im.crop(PATH_BOX)
        crop = crop.resize((crop.width * CROP_SCALE, crop.height * CROP_SCALE), Image.LANCZOS)
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def jpeg_block(b64):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def classify_flooding(image_path, reference):
    """Ask Haiku whether the path in the snapshot is flooded.

    `reference` is (image_path, level_ft) of the path known dry. Returns
    (flooded, notes): flooded is "yes", "no" or "" if the call failed or
    ANTHROPIC_API_KEY isn't set.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "", "no ANTHROPIC_API_KEY"
    ref_path, ref_level = reference
    with open(image_path, "rb") as f:
        full_b64 = base64.b64encode(f.read()).decode()
    prompt = (
        "These are from a webcam at Club Evexia, 100 Shoreline Hwy, Mill Valley, "
        "CA, looking across Shoreline Hwy toward US-101 beside Richardson Bay. "
        "Images 1 and 2 are enlarged crops of the same spot: the strip behind "
        "the garden statuary yard along Shoreline Hwy, left of a large tree, "
        "where the Mill Valley-Sausalito bike path runs.\n"
        f"- Image 1: the path known DRY, at low tide ({ref_level:.1f}ft).\n"
        "- Image 2: the same spot now.\n"
        "- Image 3: the full frame now, for context.\n\n"
        "At high tide bay water floods the path. Compare image 2 against image "
        "1: water hides ground features visible in image 1 under a smooth, "
        "uniform or reflective surface. Ignore differences from lighting, "
        "shadows, people, cars and garden items being moved. Is the path in "
        "image 2 flooded?\n\n"
        'Respond with JSON only: {"flooded": true|false, "notes": "<one short '
        'sentence on how the path in image 2 differs from image 1>"}'
    )
    body = json.dumps({
        "model": VISION_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": [
            jpeg_block(path_crop_b64(ref_path)),
            jpeg_block(path_crop_b64(image_path)),
            jpeg_block(full_b64),
            {"type": "text", "text": prompt},
        ]}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            content = json.load(resp)["content"]
        # Skip any thinking blocks ahead of the answer
        text = next(b["text"] for b in content if b["type"] == "text").strip()
        # Strip markdown code fencing if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return ("yes" if result["flooded"] else "no"), result.get("notes", "")
    except Exception as e:
        print(f"Flood classification failed: {e}", file=sys.stderr)
        return "", f"classification failed: {e}"[:200]


def snapshot(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(SNAPSHOT_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(path, "wb") as f:
        f.write(resp.read())


def load_reference(out_dir):
    """(image_path, level_ft) of the dry reference, or None."""
    meta = os.path.join(out_dir, "reference", "dry.json")
    if not os.path.exists(meta):
        return None
    with open(meta) as f:
        level = json.load(f)["model_ft"]
    return os.path.join(out_dir, "reference", "dry.jpg"), level


def update_reference(out_dir, now, level):
    """Keep this low-tide snapshot as the dry reference if it's the lowest yet."""
    ref = load_reference(out_dir)
    if ref and ref[1] <= level:
        print(f"Dry reference at {ref[1]:.2f}ft is already lower than {level:.2f}ft.")
        return
    ref_dir = os.path.join(out_dir, "reference")
    snapshot(os.path.join(ref_dir, "dry.jpg"))
    with open(os.path.join(ref_dir, "dry.json"), "w") as f:
        json.dump({"captured_at": f"{now:%Y-%m-%d %H:%M}", "model_ft": round(level, 2)}, f)
    print(f"Saved dry reference at {level:.2f}ft.")


def load_log(out_dir):
    log = os.path.join(out_dir, "log.csv")
    if not os.path.exists(log):
        return []
    with open(log, newline="") as f:
        return list(csv.DictReader(f))


def save_log(out_dir, rows):
    with open(os.path.join(out_dir, "log.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, restval="")
        writer.writeheader()
        writer.writerows(rows)


def label_pending(out_dir, rows, reference):
    """Label rows captured before the dry reference existed (or that failed)."""
    pending = [r for r in rows if not r.get("flooded")][:RELABEL_BATCH]
    for r in pending:
        r["flooded"], r["notes"] = classify_flooding(os.path.join(out_dir, r["image"]), reference)
        r["label_source"] = "haiku" if r["flooded"] else ""
    if pending:
        print(f"Labeled {sum(1 for r in pending if r['flooded'])}/{len(pending)} pending rows.")


def main():
    now = datetime.now(TZ).replace(tzinfo=None)
    anomaly = fetch_anomaly()
    extremes = fetch_extremes(now.date(), anomaly)
    mode = capture_mode(extremes, now)

    if sys.argv[1:] == ["--check"]:
        print(f"capture={'true' if mode else 'false'}")
        return
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if not mode:
        print("Not near a big high tide or a daylight low; nothing to capture.")
        return

    out_dir = sys.argv[1]
    kind, peak = mode
    model_ft = level_at(extremes, now)
    rows = load_log(out_dir)

    if kind == "dry":
        update_reference(out_dir, now, model_ft)
    else:
        image = os.path.join(out_dir, f"{now:%Y-%m-%d}", f"{now:%H%M}.jpg")
        snapshot(image)
        obs_time, obs_ft = observed_now()
        peak_time, peak_ft = peak
        rows.append({
            "captured_at": f"{now:%Y-%m-%d %H:%M}",
            "image": os.path.relpath(image, out_dir),
            "model_sausalito_ft": f"{model_ft:.2f}" if model_ft is not None else "",
            "observed_sf_ft": f"{obs_ft:.2f}" if obs_ft is not None else "",
            "observed_sf_at": obs_time or "",
            "anomaly_ft": f"{anomaly:.2f}",
            "peak_at": f"{peak_time:%Y-%m-%d %H:%M}",
            "peak_ft": f"{peak_ft:.2f}",
            "flooded": "",
            "label_source": "",
            "notes": "",
        })
        print(f"Captured {rows[-1]['image']}: model {rows[-1]['model_sausalito_ft']}ft, "
              f"observed SF {rows[-1]['observed_sf_ft']}ft (peak {peak_ft:.1f}ft at {peak_time:%H:%M})")

    reference = load_reference(out_dir)
    if reference:
        label_pending(out_dir, rows, reference)
    else:
        print("No dry reference yet; leaving labels blank.")
    save_log(out_dir, rows)


if __name__ == "__main__":
    main()
