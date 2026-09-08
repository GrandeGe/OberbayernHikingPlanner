# Phase 3 Implementation Task — Transit Accessibility

Same project, same contract (`docs/ARCHITECTURE.md`), same rules as Phase 2:
do not change signatures, schema columns, or test files; no new runtime
dependencies; library modules never `print()` or `sys.exit()`; complete files,
not fragments; run the live smoke test and paste its real output.

Phase 2 is merged. `src/errors.py`, `src/models.py`, `src/db.py` and
`src/routes.py` are in place — reuse `routes.haversine_km` rather than writing
a second distance function.

`tests/test_transit.py` already exists and is currently skipped by the
`require()` guard in `conftest.py`. Phase 3's gate is that file passing with
**0 skipped**.

One lesson carried over from Phase 2: `overpass-api.de` returned HTTP 406 for
requests with no `User-Agent`. Send an identifying `User-Agent` on the MVV
download too.

---

# PART 1 — Task brief


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



---

# PART 2 — Acceptance tests (already in the repo, do not modify)


### `tests/test_transit.py`

```python
"""Acceptance tests for Phase 3 — src/transit.py.

Driven entirely by tests/fixtures/gtfs_mini/, a hand-built GTFS feed whose
calendar deliberately exercises the cases that break naive implementations:
a BOM on stops.txt, service exceptions that both add and remove, an expired
calendar window, times past 24:00, and a final stop with no departure_time.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import require

transit = require(
    "src.transit",
    "load_stops", "count_departures", "store_stops",
    "nearest_stop", "build_route_access", "transit_score",
)
models = require("src.models", "BBox", "Stop")


# ──────────────────────────── stops.txt ────────────────────────────────


@pytest.fixture
def stops(gtfs_zip):
    return transit.load_stops(gtfs_zip)


def test_bom_does_not_corrupt_the_first_column(stops):
    """stops.txt carries a UTF-8 BOM. Read as plain utf-8, the first field name
    becomes '\\ufeffstop_id' and every stop_id lookup returns None — the
    classic GTFS bug. Read it as utf-8-sig."""
    assert all(s.stop_id for s in stops)
    assert "DE:09182:1" in {s.stop_id for s in stops}


def test_skips_unboardable_location_types(stops):
    """location_type=2 is a station entrance, not a place a trip departs."""
    assert "DE:09182:5" not in {s.stop_id for s in stops}


def test_keeps_stations_and_blank_location_type(stops):
    ids = {s.stop_id for s in stops}
    assert "DE:09182:7" in ids, "location_type=1 (station) must be kept"
    assert "DE:09182:3" in ids, "blank location_type must be treated as a stop"


def test_skips_rows_with_unparseable_coordinates(stops):
    assert "DE:09182:6" not in {s.stop_id for s in stops}


def test_expected_stop_count(stops):
    assert len(stops) == 5


def test_coordinates_are_floats(stops):
    stop = next(s for s in stops if s.stop_id == "DE:09182:1")
    assert stop.stop_lat == pytest.approx(47.7345)
    assert stop.stop_lon == pytest.approx(11.8556)
    assert stop.stop_name == "Schliersee Bahnhof"


def test_bbox_filter(gtfs_zip):
    filtered = transit.load_stops(gtfs_zip, bbox=models.BBox(47.6, 11.7, 47.8, 11.9))
    ids = {s.stop_id for s in filtered}
    assert "DE:09182:4" not in ids, "48.4/12.5 is outside the box"
    assert "DE:09182:7" not in ids, "11.64 is outside the box"
    assert "DE:09182:1" in ids


# ──────────────────────────── calendar ─────────────────────────────────


def test_departures_on_a_saturday_with_exceptions(gtfs_zip):
    """2026-09-12 is a Saturday. calendar.txt says SATURDAY runs and WEEKDAY
    does not; calendar_dates.txt reverses both. Exceptions win."""
    counts = transit.count_departures(gtfs_zip, date(2026, 9, 12))
    assert counts == {"DE:09182:1": 2, "DE:09182:2": 2}


def test_final_stop_without_departure_time_is_not_counted(gtfs_zip):
    """T_WD_1's last stop has an empty departure_time — nobody departs from it."""
    counts = transit.count_departures(gtfs_zip, date(2026, 9, 12))
    assert "DE:09182:3" not in counts


def test_service_present_only_in_calendar_dates(gtfs_zip):
    """SPECIAL has no calendar.txt row at all — it exists only as an added
    exception on 2026-09-13. Feeds that use calendar_dates exclusively are
    common and must not be dropped."""
    counts = transit.count_departures(gtfs_zip, date(2026, 9, 13))
    assert counts == {"DE:09182:4": 1}


def test_expired_calendar_window_is_inactive(gtfs_zip):
    """EXPIRED runs every day of the week but its window closed in 2020. Its
    trip T_EX_1 departs from stop 1; if the date range were ignored, stop 1
    would show 3 departures instead of 2."""
    assert transit.count_departures(gtfs_zip, date(2026, 9, 12))["DE:09182:1"] == 2
    assert transit.count_departures(gtfs_zip, date(2026, 9, 13)) == {"DE:09182:4": 1}


def test_times_past_midnight_do_not_crash(gtfs_zip):
    """T_WD_2 departs at 25:10:00. datetime.strptime('%H:%M:%S') raises on
    this; GTFS times are strings that may exceed 24 hours."""
    counts = transit.count_departures(gtfs_zip, date(2026, 9, 12))
    assert counts["DE:09182:2"] == 2


def test_date_with_no_service_returns_empty_dict(gtfs_zip):
    assert transit.count_departures(gtfs_zip, date(2019, 1, 1)) == {}


# ──────────────────────────── nearest stop ─────────────────────────────


def test_nearest_stop_exact_hit(stops):
    result = transit.nearest_stop(47.7000, 11.8000, stops)
    assert result is not None
    stop, dist = result
    assert stop.stop_id == "DE:09182:2"
    assert dist == pytest.approx(0.0, abs=1e-6)


def test_nearest_stop_picks_the_closest_not_the_first(stops):
    stop, dist = transit.nearest_stop(47.7200, 11.8200, stops)
    assert stop.stop_id == "DE:09182:3"
    assert dist == pytest.approx(0.0, abs=1e-6)


def test_nearest_stop_respects_max_km(stops):
    assert transit.nearest_stop(48.4000, 12.5000, stops, max_km=3.0) is not None
    assert transit.nearest_stop(48.0000, 12.2000, stops, max_km=3.0) is None


def test_nearest_stop_on_empty_list_returns_none():
    assert transit.nearest_stop(47.7, 11.8, [], max_km=3.0) is None


# ──────────────────────────── persistence ──────────────────────────────


def test_store_stops_is_idempotent(db_conn, stops):
    transit.store_stops(db_conn, stops)
    transit.store_stops(db_conn, stops)
    assert db_conn.execute("SELECT COUNT(*) FROM stops").fetchone()[0] == len(stops)


def test_build_route_access(db_conn, stops, overpass_payload):
    src_routes = require("src.routes", "parse_response", "store_routes")
    src_routes.store_routes(db_conn, src_routes.parse_response(overpass_payload))
    transit.store_stops(db_conn, stops)

    written = transit.build_route_access(
        db_conn, max_walk_km=3.0, departures={"DE:09182:2": 48, "DE:09182:3": 12}
    )
    assert written > 0

    rows = {
        (r["osm_id"], r["endpoint"]): r
        for r in db_conn.execute("SELECT * FROM route_access")
    }
    start = rows[(1001, "start")]
    assert start["stop_id"] == "DE:09182:2"
    assert start["walk_km"] == pytest.approx(0.0, abs=1e-6)
    assert start["departures_per_day"] == 48

    end = rows[(1001, "end")]
    assert end["stop_id"] == "DE:09182:3"
    assert end["departures_per_day"] == 12


def test_build_route_access_is_idempotent(db_conn, stops, overpass_payload):
    src_routes = require("src.routes", "parse_response", "store_routes")
    src_routes.store_routes(db_conn, src_routes.parse_response(overpass_payload))
    transit.store_stops(db_conn, stops)
    transit.build_route_access(db_conn)
    first = db_conn.execute("SELECT COUNT(*) FROM route_access").fetchone()[0]
    transit.build_route_access(db_conn)
    assert db_conn.execute("SELECT COUNT(*) FROM route_access").fetchone()[0] == first


def test_route_without_a_nearby_stop_gets_no_access_row(db_conn, stops, overpass_payload):
    """Relation 1003 sits at 47.60–47.64 / 11.60–11.64. Only stop 7 is within
    reach, at the route's *end*; the start has nothing within 3 km."""
    src_routes = require("src.routes", "parse_response", "store_routes")
    src_routes.store_routes(db_conn, src_routes.parse_response(overpass_payload))
    transit.store_stops(db_conn, stops)
    transit.build_route_access(db_conn, max_walk_km=3.0)
    endpoints = {
        r["endpoint"]
        for r in db_conn.execute("SELECT endpoint FROM route_access WHERE osm_id = 1003")
    }
    assert "start" not in endpoints
    assert "end" in endpoints


# ──────────────────────────── scoring ──────────────────────────────────


def test_transit_score_bounds():
    assert transit.transit_score(0.0, 60) == pytest.approx(1.0, abs=1e-6)
    assert transit.transit_score(3.5, 60) == 0.0


def test_transit_score_zero_departures_keeps_only_the_walk_component():
    assert transit.transit_score(0.0, 0) == pytest.approx(0.6, abs=1e-6)


def test_transit_score_unknown_frequency_is_a_neutral_half():
    assert transit.transit_score(1.5, None) == pytest.approx(0.5, abs=1e-6)


def test_transit_score_decreases_with_distance():
    scores = [transit.transit_score(km, 30) for km in (0.0, 0.5, 1.0, 2.0, 2.9)]
    assert scores == sorted(scores, reverse=True)


def test_transit_score_frequency_is_logarithmic():
    """Going from 2 to 8 departures should matter more than 40 to 60."""
    low = transit.transit_score(0.0, 8) - transit.transit_score(0.0, 2)
    high = transit.transit_score(0.0, 60) - transit.transit_score(0.0, 40)
    assert low > high


@pytest.mark.parametrize("walk,dep", [(0.0, 100), (2.99, 1), (0.0, None), (3.0, 0)])
def test_transit_score_always_in_range(walk, dep):
    assert 0.0 <= transit.transit_score(walk, dep) <= 1.0


# ──────────────────────────── live smoke test ──────────────────────────


@pytest.mark.live
def test_live_gtfs_url_is_reachable(tmp_path):
    """Deselected in CI. Confirms the MVV feed URL has not moved."""
    path = transit.download_gtfs(tmp_path / "gesamt_gtfs.zip")
    assert path.exists() and path.stat().st_size > 1_000_000
    assert len(transit.load_stops(path)) > 5000

```


---

# PART 3 — The GTFS fixture the tests run against


These loose files are zipped into a real `.zip` by the `gtfs_zip`
fixture in `conftest.py`. `stops.txt` carries a UTF-8 BOM.


### `tests/fixtures/gtfs_mini/agency.txt`

```text
agency_id,agency_name,agency_url,agency_timezone
MVV,Muenchner Verkehrs- und Tarifverbund GmbH,https://www.mvv-muenchen.de,Europe/Berlin

```

### `tests/fixtures/gtfs_mini/calendar.txt`

```text
service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
WEEKDAY,1,1,1,1,1,0,0,20260101,20261231
SATURDAY,0,0,0,0,0,1,0,20260101,20261231
EXPIRED,1,1,1,1,1,1,1,20200101,20201231

```

### `tests/fixtures/gtfs_mini/calendar_dates.txt`

```text
service_id,date,exception_type
SATURDAY,20260912,2
WEEKDAY,20260912,1
SPECIAL,20260913,1

```

### `tests/fixtures/gtfs_mini/routes.txt`

```text
route_id,route_short_name,route_long_name,route_type
R1,9562,Schliersee - Neuhaus,3
R2,S2,Muenchen - Schliersee,2
R3,X1,Sonderfahrt,3
R4,999,Alt,3

```

### `tests/fixtures/gtfs_mini/stop_times.txt`

```text
trip_id,arrival_time,departure_time,stop_id,stop_sequence
T_WD_1,08:00:00,08:00:00,DE:09182:1,1
T_WD_1,08:10:00,08:10:00,DE:09182:2,2
T_WD_1,08:20:00,,DE:09182:3,3
T_WD_2,09:00:00,09:00:00,DE:09182:1,1
T_WD_2,25:10:00,25:10:00,DE:09182:2,2
T_SA_1,10:00:00,10:00:00,DE:09182:1,1
T_SP_1,11:00:00,11:00:00,DE:09182:4,1
T_EX_1,12:00:00,12:00:00,DE:09182:1,1

```

### `tests/fixtures/gtfs_mini/stops.txt`

```text
﻿stop_id,stop_name,stop_lat,stop_lon,location_type
DE:09182:1,Schliersee Bahnhof,47.7345,11.8556,0
DE:09182:2,Neuhaus (Schliersee),47.7000,11.8000,0
DE:09182:3,Fischhausen-Neuhaus,47.7200,11.8200,
DE:09182:4,Far Away Stop,48.4000,12.5000,0
DE:09182:5,Bahnhof Eingang Nord,47.7345,11.8556,2
DE:09182:6,Broken Row,,,0
DE:09182:7,Bahnhof Gap,47.6400,11.6400,1

```

### `tests/fixtures/gtfs_mini/trips.txt`

```text
route_id,service_id,trip_id
R1,WEEKDAY,T_WD_1
R1,WEEKDAY,T_WD_2
R2,SATURDAY,T_SA_1
R3,SPECIAL,T_SP_1
R4,EXPIRED,T_EX_1

```


---

# PART 4 — Working out the expected calendar results by hand

2026-09-12 is a Saturday, 2026-09-13 a Sunday.

| service_id | calendar.txt | calendar_dates.txt | active 09-12 | active 09-13 |
|---|---|---|---|---|
| WEEKDAY  | Mon-Fri, 2026 | added on 20260912 | **yes** (exception) | no |
| SATURDAY | Sat, 2026 | removed on 20260912 | no (exception) | no |
| SPECIAL  | absent | added on 20260913 | no | **yes** |
| EXPIRED  | every day, 2020 only | — | no (window closed) | no |

So 09-12 counts only T_WD_1 and T_WD_2, and the last stop of T_WD_1 has an
empty `departure_time`, giving `{"DE:09182:1": 2, "DE:09182:2": 2}`.
