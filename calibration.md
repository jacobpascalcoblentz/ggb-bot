# Flood calibration

**Status: collecting** — 1/3 tides with a fully observed flood (onset and clearing both bracketed by dry captures).

| | bot.py now | fitted |
|---|---|---|
| `FLOOD_LEVEL_FT` | 5.9 | 4.26 (spread 0.00ft, n=1) |
| `DRAIN_LAG` | 60 min | 21 min (spread 32 min, n=2) |

Ready when ≥3 tides are fully observed, onset levels agree within 0.4ft and drain lags within 45 min.

## Tides

| peak | peak ft | captures | flooded | onset | onset level | clear | drain lag |
|---|---|---|---|---|---|---|---|
| 2026-10-13 13:24 | 6.59 | 28 | yes | — | — | 4:26pm | 5 min |
| 2026-10-14 03:29 | 5.12 | 10 | no | — | — | — | — |
| 2026-10-14 13:56 | 6.28 | 35 | yes | — | — | — | — |
| 2026-10-15 04:31 | 4.95 | 7 | yes | 7:57am | 4.26 | 8:34am | 37 min |
| 2026-10-15 14:34 | 6.06 | 22 | yes | — | — | — | — |

Levels are modeled Sausalito ft MLLW (NOAA prediction + anomaly), the units bot.py uses. Haiku's `flooded` labels in log.csv can be corrected by hand; this report is rebuilt on every capture.
