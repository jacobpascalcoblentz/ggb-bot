# Flood calibration

**Status: collecting** — 1/3 tides with a fully observed flood (onset and clearing both bracketed by dry captures).

| | bot.py now | fitted |
|---|---|---|
| `FLOOD_LEVEL_FT` | 5.9 | 4.59 (spread 0.52ft, n=3) |
| `DRAIN_LAG` | 60 min | 28 min (spread 15 min, n=2) |

Ready when ≥3 tides are fully observed, onset levels agree within 0.4ft and drain lags within 45 min.

## Tides

| peak | peak ft | captures | flooded | onset | onset level | clear | drain lag |
|---|---|---|---|---|---|---|---|
| 2026-10-13 13:24 | 6.59 | 28 | yes | — | — | 4:26pm | 20 min |
| 2026-10-14 03:29 | 5.12 | 10 | no | — | — | — | — |
| 2026-10-14 13:56 | 6.28 | 35 | yes | — | — | — | — |
| 2026-10-15 04:31 | 4.95 | 7 | yes | 7:57am | 4.26 | 8:34am | — |
| 2026-10-15 14:34 | 6.06 | 31 | yes | — | — | — | — |
| 2026-10-16 05:42 | 4.94 | 11 | yes | — | — | — | — |
| 2026-10-16 15:21 | 5.91 | 27 | yes | — | — | — | — |
| 2026-10-17 06:58 | 5.03 | 15 | no | — | — | — | — |
| 2026-10-17 16:20 | 5.71 | 22 | yes | — | — | — | — |
| 2026-10-18 07:57 | 5.23 | 19 | yes | 12:11pm | 4.59 | — | — |
| 2026-10-18 17:31 | 5.65 | 18 | yes | — | — | — | — |
| 2026-10-19 08:37 | 5.42 | 21 | yes | 11:18am | 4.78 | 12:27pm | 35 min |
| 2026-10-19 18:45 | 5.59 | 14 | yes | — | — | — | — |
| 2026-10-20 09:07 | 5.68 | 25 | no | — | — | — | — |
| 2026-10-20 19:54 | 5.66 | 11 | no | — | — | — | — |
| 2026-10-21 09:33 | 5.99 | 27 | yes | — | — | — | — |
| 2026-10-22 09:57 | 6.33 | 31 | yes | — | — | — | — |
| 2026-10-22 21:53 | 5.96 | 5 | no | — | — | — | — |
| 2026-10-23 10:22 | 6.59 | 27 | yes | — | — | — | — |
| 2026-10-24 10:49 | 6.77 | 8 | no | — | — | — | — |

Levels are modeled Sausalito ft MLLW (NOAA prediction + anomaly), the units bot.py uses. Haiku's `flooded` labels in log.csv can be corrected by hand; this report is rebuilt on every capture.
