# Delegation Brief — Phase 4: Recommendation Engine & GPX Export

**Assignee:** GPT6
**Prerequisite:** Phases 2 and 3 merged.
**Deliverables:** `src/recommend.py`, the `recommend` subcommand in `main.py`.
**Acceptance gate:** `pytest tests/test_recommend.py` passes, offline.

This is the phase that makes the project *a product* rather than three
independent API wrappers. It is also the phase where a reviewer looks hardest,
so the scoring must be defensible, not hand-waved.

---

## 1. Goal

`"I have 6 hours this Saturday and I'm starting from Munich"` → a ranked list of
five hikes, each with a reason.

---

## 2. `estimate_duration_h` — DIN 33466 / SAC walking time

Do **not** invent a formula. Use the German Alpine Club's standard, which is
what every Bavarian trail sign uses:

```
horizontal_h = length_km / 4.0                  # 4 km/h on the flat
vertical_h   = ascent_m / 300.0 + descent_m / 500.0   # 300 m up, 500 m down per hour
duration_h   = max(horizontal_h, vertical_h) + min(horizontal_h, vertical_h) / 2
```

- `ascent_m` / `descent_m` may be `None`. When both are `None`, fall back to
  `length_km / 3.5` (a slower flat-equivalent pace that implicitly allows for
  unknown terrain) and record the assumption in the `ScoredRoute.reasons` list.
- When only one of the two is known, use it and treat the other as equal to it
  (a hike that climbs generally descends).
- Add no break time. Users add their own; a tool that pads silently is a tool
  users stop trusting.

Cite DIN 33466 in the docstring.

---

## 3. `weather_score(summary) -> float`

`summary` is a `weather.summarize_day()` dict. Returns 0.0–1.0.
Components, all as module constants:

| Component | Weight | Formula (all clamped to `[0, 1]`) |
|---|---|---|
| Rain | 0.40 | `1 - total_rain / 5.0` |
| Temperature | 0.25 | `1.0` for `12 <= temp_max <= 24`; `temp_max / 12` below 12; `(34 - temp_max) / 10` above 24 |
| Wind | 0.20 | `1.0` for `max_wind <= 15`; `(50 - max_wind) / 35` above 15 |
| Cloud | 0.15 | `1 - 0.007 * avg_cloud_cover` — an overcast hike is still a hike, so the floor at 100 % cover is 0.3, not 0 |

The tests assert `weather_score` to 3 decimal places on a reference day, so use
these expressions literally rather than an equivalent-looking rewrite.

Missing components (`None`) are dropped and the remaining weights renormalised.
If every component is missing, return `0.5` and record the assumption.

**Hard veto → return `0.0`:** `dominant_condition == "thunderstorm"`, or
`total_rain > 10.0` mm. Exposed Bavarian ridges in a thunderstorm are a genuine
safety matter, and a recommender that ranks them at 0.3 instead of 0 is wrong.

---

## 4. `fit_score(estimated_hours, available_hours) -> float`

Rewards using the available time well without exceeding it.

```
ratio = estimated_hours / available_hours
ratio > 1.0        -> 0.0        (does not fit; hard veto)
0.6 <= ratio <= 1.0 -> 1.0       (a good use of the day)
ratio < 0.6         -> ratio / 0.6   (too short, linearly less interesting)
```

`available_hours <= 0` raises `ValueError`.

---

## 5. `score_route` and `recommend`

`score_route(route, access, summary, available_hours) -> ScoredRoute`

- `transit_score` = `transit.transit_score(access.walk_km, access.departures_per_day)`,
  or `0.0` with a veto when `access is None`.
- `total_score` = weighted sum using `WEIGHTS` from `docs/ARCHITECTURE.md` §5.
- Any hard veto sets `total_score = 0.0` but the component scores are still
  reported honestly — do not zero them out, the user should see *why*.
- `reasons`: short Chinese strings, at most four, e.g.
  `"预计 5.2 小时，正好填满 6 小时"`, `"起点距 Schliersee Bahnhof 0.4 km"`,
  `"当日累计雨量 0.2 mm"`, `"⚠ 未知爬升，时间为粗估"`.

`recommend(conn, *, origin, day_summary, available_hours, top_n=5, max_walk_km=3.0)`

1. `load_routes(conn, ...)` with a `max_length_km` pre-filter of
   `available_hours * 5.0` — a cheap SQL-level cut that avoids scoring thousands
   of multi-day routes. Do not pre-filter on anything else; the ranking should
   see the real field.
2. Join each route to its `route_access` rows; keep the endpoint with the better
   `transit_score`.
3. Score everything, drop `total_score == 0.0`, sort descending, return `top_n`.
4. Returns `[]` when nothing survives — **not** an error. The CLI then prints a
   suggestion to widen `--hours` or `--max-walk-km`.

`origin` is currently used only to break ties by proximity to the user's start
point. Keep the parameter — Phase 5 and any future journey-time work need it.

---

## 6. `to_gpx(route) -> str`

A GPX 1.1 document with one `<trk>` containing one `<trkseg>` of `<trkpt>`
elements. Build it with `xml.etree.ElementTree`, not string concatenation —
route names contain `&`, `ä`, and quotation marks, and hand-built XML will
produce files that Garmin and Komoot reject.

Required: `version="1.1"`, `creator="OberbayernHikingPlanner"`,
`xmlns="http://www.topografix.com/GPX/1/1"`, `<metadata><name>` and `<trk><name>`
set to the route name, `<desc>` carrying length, estimated duration, and the
OSM relation URL (`https://www.openstreetmap.org/relation/<osm_id>`).

Include the OSM attribution string `"© OpenStreetMap contributors (ODbL)"` in
`<metadata><desc>`. ODbL requires it.

`main.py recommend --gpx DIR` writes one file per recommendation, named
`<rank>_<slugified_name>.gpx`.

---

## 7. CLI

```
python main.py recommend --location NAME --hours N [--date YYYY-MM-DD]
                         [--top 5] [--max-walk-km 3.0] [--gpx DIR] [--db PATH]
```

Flow: geocode `--location` → `weather.fetch_weather` → `group_by_day` →
`summarize_day` for `--date` (default: next Saturday) → `recommend()` → print.

If the requested date is outside the Bright Sky forecast horizon, raise a clear
`NotFoundError` naming the available date range rather than scoring against an
empty summary.

Output format, one block per recommendation:

```
#1  Bodenschneid-Runde                                    ★ 0.82
    12.4 km · ↑ 620 m · 预计 4.8 h · lwn
    🚉 Neuhaus (Schliersee), 步行 0.6 km · 每日 48 班
    ☀️ 15–22°C · 雨 0.0 mm · 风 12 km/h
    → https://www.openstreetmap.org/relation/1234567
```

---

## 8. Out of scope

- Multi-day routes, hut bookings, difficulty/SAC-scale grading.
- Elevation profile charts.
- Any change to the scoring weights without updating
  `docs/ARCHITECTURE.md` in the same commit.

---

## 9. Definition of done

1. `pytest tests/test_recommend.py` passes, including the DIN 33466 reference
   values and every hard-veto case.
2. `ruff check` clean.
3. Live smoke test, output pasted back:
   ```
   python main.py recommend --location "Schliersee" --hours 6 --gpx out/
   ```
   Expect five plausible hikes and five valid GPX files. Open one in
   <https://gpx.studio> to confirm it renders as a connected track.
