# How to Read METAR and TAF Reports

## METAR example
`METAR EGLL 121150Z 24015G25KT 9999 -RA BKN012 OVC025 08/06 Q1004 TEMPO 4000 RA`

| Part | Meaning |
|---|---|
| EGLL | Airport (London Heathrow) |
| 121150Z | Day 12, 11:50 UTC (Z = UTC) |
| 24015G25KT | Wind from 240 degrees at 15 knots, gusting 25 knots |
| 9999 | Visibility 10 km or more |
| -RA | Light rain (+ = heavy, - = light). Other codes: TS thunderstorm, FG fog, BR mist, HZ haze, SN snow, DU dust, SA sand |
| BKN012 | Broken cloud (5-7 eighths of sky) at 1,200 ft. FEW 1-2, SCT 3-4, BKN 5-7, OVC 8 eighths |
| OVC025 | Overcast at 2,500 ft |
| 08/06 | Temperature 8 C, dew point 6 C (close values = fog or low cloud risk) |
| Q1004 | Pressure 1004 hPa |
| TEMPO 4000 RA | Temporarily visibility 4 km in rain |
| CAVOK | Ceiling and visibility OK - good weather |
| NOSIG | No significant change expected |

## TAF keywords
- **FM1800** - from 18:00 UTC conditions change to what follows.
- **BECMG 1214/1216** - becoming, gradually between 14:00 and 16:00 UTC on the 12th.
- **TEMPO** - temporary fluctuations lasting under an hour each.
- **PROB30 / PROB40** - 30% or 40% probability.

## What matters for delays (plain-language rules)
- Thunderstorms (TS, CB clouds) near the airport: high delay and diversion risk; ground operations may pause.
- Fog (FG) or visibility under 1,500 m, or cloud below 200-300 ft: low-visibility procedures reduce runway capacity and cause delays, especially at busy hubs.
- Strong crosswinds or gusts above about 30-35 knots: possible go-arounds, delays and diversions.
- Snow (SN) and freezing conditions (FZRA, FZFG): de-icing adds 15-45 minutes; runway closures possible.
- Dust or sand (DU, SA, HZ) common in West Africa during Harmattan (roughly December to February) can reduce visibility and delay flights.
