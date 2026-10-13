# Flood calibration

**Status: collecting** — 0/3 tides with a fully observed flood (onset and clearing both bracketed by dry captures).

| | bot.py now | fitted |
|---|---|---|
| `FLOOD_LEVEL_FT` | 5.9 | —  |
| `DRAIN_LAG` | 60 min | —  |

Ready when ≥3 tides are fully observed, onset levels agree within 0.4ft and drain lags within 45 min.

## Tides

| peak | peak ft | captures | flooded | onset | onset level | clear | drain lag |
|---|---|---|---|---|---|---|---|
| 2026-10-13 13:24 | 6.59 | 12 | no | — | — | — | — |

Levels are modeled Sausalito ft MLLW (NOAA prediction + anomaly), the units bot.py uses. Haiku's `flooded` labels in log.csv can be corrected by hand; this report is rebuilt on every capture.
