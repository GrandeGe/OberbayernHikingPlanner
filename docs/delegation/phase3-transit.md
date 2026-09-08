# Delegation Brief — Phase 3: Transit Accessibility

**Assignee:** GPT6
**Prerequisite:** Phase 2 merged (`routes` table populated).
**Deliverables:** `src/transit.py`, the `transit` subcommands in `main.py`.
**Acceptance gate:** `pytest tests/test_transit.py` passes, offline.

Read `docs/ARCHITECTURE.md` §5 for the exact signatures.

---

## 1. Goal

For every route in the database, answer: *can I get to the start (or end) of
this hike by public transport from Munich, and how often does that service run?*

---

## 2. Data source

MVV whole-network GTFS feed:

```
https://www.mvv-muenchen.de/fileadmin/mediapool/developer/opendata/gesamt_gtfs.zip
```

- Licence **CC-BY 4.0**. Attribution is mandatory and must name
  "Münchner Verkehrs- und Tarifverbund GmbH (MVV)" together with the retrieval
  date and the feed version. Put this in `README.md` and in `--version` output.
  This is a licence obligation, not a nicety.
- Updated irregularly, roughly every 4–8 weeks.
- Uncompressed the feed is on the order of hundreds of MB, dominated by
  `stop_times.txt` (tens of millions of rows).

**Never extract the zip to disk.** Read members as streams through
`zipfile.ZipFile.open()` wrapped in `io.TextIOWrapper(..., encoding="utf-8-sig")`
and `csv.DictReader`. The `utf-8-sig` matters: GTFS feeds commonly carry a BOM,
and with plain `utf-8` the first column name silently becomes `"﻿stop_id"`,
so every lookup of `stop_id` returns `None`. This is the classic GTFS bug.

`download_gtfs(dest, url=MVV_GTFS_URL)` streams to disk with
`requests.get(..., stream=True)` and `iter_content(chunk_size=1 << 20)`; it
skips the download if `dest` exists and is younger than 14 days. Wrap failures
in `DataSourceError`.

---

## 3. `load_stops`

From `stops.txt`, yield `Stop(stop_id, stop_name, stop_lat, stop_lon)`.

- Skip rows where `stop_lat`/`stop_lon` are empty or unparseable.
- If `location_type` is present, keep only `""`, `"0"` (a boardable stop) and
  `"1"` (a station). Skip `"2"`/`"3"`/`"4"` (entrances, generic nodes, boarding
  areas) — they are not places a trip departs from.
- Apply the optional `bbox` filter during iteration, not afterwards.
- Return a list; the MVV bbox-filtered stop count is in the low tens of
  thousands, which fits in memory comfortably.

`store_stops(conn, stops) -> int` upserts on `stop_id`, one transaction.

---

## 4. `count_departures`

`count_departures(gtfs_zip, service_date) -> dict[stop_id, int]`

How many trips depart from each stop on a given calendar date. This is the
frequency signal that separates "a bus twice a day on schooldays" from "S-Bahn
every 20 minutes".

Algorithm:

1. **Resolve active services for `service_date`.**
   - From `calendar.txt`: a `service_id` is active if
     `start_date <= service_date <= end_date` **and** the weekday column for
     that date is `"1"`. Dates are `YYYYMMDD` strings.
   - From `calendar_dates.txt`: `exception_type == "1"` adds the service on that
     date, `exception_type == "2"` removes it. Exceptions override `calendar.txt`.
   - `calendar.txt` may be absent from a feed that uses only `calendar_dates.txt`.
     Handle both; a missing file is not an error.
2. **Collect trip_ids** from `trips.txt` whose `service_id` is active.
3. **Stream `stop_times.txt`** and count rows whose `trip_id` is active and whose
   `departure_time` is non-empty. Do not build a list of stop_times — increment
   a `collections.Counter` as you stream. Peak memory must stay bounded by the
   number of distinct trips and stops, not by the number of stop_times rows.
   Log progress every 1,000,000 rows.

Note GTFS times may exceed 24 hours (`"25:10:00"` = 01:10 the following day).
For a departure count this needs no special handling, but do not parse these
with `datetime.strptime("%H:%M:%S")` — it will raise. Keep them as strings.

---

## 5. `nearest_stop` and `build_route_access`

```python
def nearest_stop(lat, lon, stops, *, max_km=3.0) -> tuple[Stop, float] | None
```

Straight-line (haversine, reuse `routes.haversine_km`) nearest stop within
`max_km`, else `None`.

Naive scanning is O(n) per query and with ~3000 routes × 2 endpoints × ~20 000
stops that is 120 M haversine calls — slow but survivable. **Do better:**
pre-bucket stops into a dict keyed by `(round(lat, 2), round(lon, 2))` (≈1.1 km
cells) and scan only the 3×3 neighbourhood of cells, widening the ring until the
`max_km` radius is covered. Document the chosen cell size in a comment.

`build_route_access(conn, *, max_walk_km=3.0, departures=None) -> int`

For each route in `routes`, find the nearest stop to `start` and to `end`,
and write a `route_access` row per endpoint that has one. `departures_per_day`
comes from the passed-in dict (`None` when not supplied). Delete existing
`route_access` rows for a route before writing new ones so re-runs are
idempotent. Returns the number of rows written.

Warn — do not fail — when a route has no stop within `max_walk_km`; that is a
legitimate answer (many Alpine routes are car-only).

---

## 6. `transit_score`

```python
def transit_score(walk_km: float, departures_per_day: int | None) -> float
```

Returns 0.0–1.0. Composition, weights as module constants:

- **Walk component** (weight `WALK_WEIGHT = 0.6`): `max(0.0, 1 - walk_km / 3.0)`
  with `MAX_WALK_KM = 3.0`.
- **Frequency component** (weight `FREQ_WEIGHT = 0.4`):
  `min(1.0, log10(1 + departures_per_day) / log10(61))` — so ~60 departures
  a day (roughly half-hourly service over an 18-hour day, both directions)
  saturates the score, and the log shape means the jump from 2 to 8 departures
  matters much more than 40 to 60. When `departures_per_day is None`, use 0.5
  for this component and record it as an assumption.

Return exactly `0.0` when `walk_km > MAX_WALK_KM`.

---

## 7. CLI additions

```
python main.py transit build [--gtfs PATH] [--download] [--date YYYY-MM-DD]
                             [--max-walk-km 3.0] [--db PATH]
python main.py transit stops [--near LAT,LON] [--limit 20] [--db PATH]
```

`--date` defaults to the next Saturday (this is a hiking tool; Saturday service
is the realistic case, and weekday-only school buses would flatter the scores).
Print a summary: stops loaded, routes with access, routes without.

---

## 8. Out of scope

- Real door-to-door journey planning or travel-time computation from a given
  origin. We score *accessibility of the trailhead*, not the trip. A future
  phase may add MVV's EFA journey API; do not start it here.
- Transfers, fare zones, real-time delays.
- Walking-network routing (we use straight-line distance and say so).

---

## 9. Definition of done

1. `pytest tests/test_transit.py` passes offline against the miniature GTFS
   fixture in `tests/fixtures/gtfs_mini/`.
2. `ruff check src/transit.py` clean.
3. Live smoke test locally, output pasted back:
   ```
   python main.py transit build --download --date 2026-09-12
   python main.py transit stops --near 47.72,11.86 --limit 5
   ```
   Expect: tens of thousands of stops, a majority of routes gaining access rows,
   and stops near Schliersee appearing in the `--near` query.
4. MVV attribution present in `README.md`.
