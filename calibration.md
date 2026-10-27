# Shoreline Hwy flood calibration

**Status: collecting** — 2/3 tides with a fully observed flood (onset and clearing both bracketed by dry captures).

| | road fit |
|---|---|
| flood level | 6.93ft (spread 0.28ft, n=3) |
| drain lag | 106 min (spread 9 min, n=2) |

Ready when ≥3 tides are fully observed, onset levels agree within 0.4ft and drain lags within 45 min.

## Tides

| peak | peak ft | captures | flooded | onset | onset level | clear | drain lag |
|---|---|---|---|---|---|---|---|
| 2026-10-13 13:24 | 6.59 | 28 | no | — | — | — | — |
| 2026-10-14 03:29 | 5.12 | 10 | no | — | — | — | — |
| 2026-10-14 13:56 | 6.28 | 35 | no | — | — | — | — |
| 2026-10-15 04:31 | 4.95 | 7 | no | — | — | — | — |
| 2026-10-15 14:34 | 6.06 | 31 | no | — | — | — | — |
| 2026-10-16 05:42 | 4.94 | 11 | no | — | — | — | — |
| 2026-10-16 15:21 | 5.91 | 27 | no | — | — | — | — |
| 2026-10-17 06:58 | 5.03 | 15 | no | — | — | — | — |
| 2026-10-17 16:20 | 5.71 | 22 | no | — | — | — | — |
| 2026-10-18 07:57 | 5.23 | 19 | no | — | — | — | — |
| 2026-10-18 17:31 | 5.65 | 18 | no | — | — | — | — |
| 2026-10-19 08:37 | 5.42 | 21 | no | — | — | — | — |
| 2026-10-19 18:45 | 5.59 | 14 | no | — | — | — | — |
| 2026-10-20 09:07 | 5.68 | 25 | no | — | — | — | — |
| 2026-10-20 19:54 | 5.66 | 11 | no | — | — | — | — |
| 2026-10-21 09:33 | 5.99 | 27 | no | — | — | — | — |
| 2026-10-22 09:57 | 6.33 | 31 | no | — | — | — | — |
| 2026-10-22 21:53 | 5.96 | 5 | no | — | — | — | — |
| 2026-10-23 10:22 | 6.59 | 27 | no | — | — | — | — |
| 2026-10-24 10:49 | 6.77 | 29 | yes | 10:56am | 6.76 | 11:33am | — |
| 2026-10-25 11:17 | 7.11 | 30 | yes | 10:26am | 6.93 | 1:41pm | 102 min |
| 2026-10-26 11:49 | 7.37 | 27 | yes | 10:42am | 7.04 | 2:42pm | 111 min |

## Bike path (rider reports)

bot.py assumes the path floods at 5.9ft and takes 60 min to drain.

- Lowest level seen flooded: 4.88ft
- Highest level seen dry: —
- Reports: 22 captures labeled

Levels are modeled Sausalito ft MLLW (NOAA prediction + anomaly), the units bot.py uses. Haiku's road labels (`flooded`) can be corrected by hand (set `label_source` to `reviewed`); path reports go in `path_flooded`. This report is rebuilt on every capture.
