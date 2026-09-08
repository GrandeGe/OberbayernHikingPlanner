# Architecture & Interface Contract

**This document is the single source of truth for all module boundaries.**
Every delegation brief in `docs/delegation/` refers back to it. If an
implementation and this document disagree, the document wins — change the
document first, then the code.

Schema version: **1**
Last revised: 2026-09-08

---

## 1. Layering

```
                        main.py  (argparse CLI)
                            │
        ┌───────────┬───────┴───────┬──────────────┐
        ▼           ▼               ▼              ▼
   recommend.py  routes.py     transit.py     weather.py
        │           │               │              │
        └───────────┴──────┬────────┴──────────────┘
                           ▼
                        db.py  (SQLite connection + schema)
                           │
                    data/hiking.db
```

Dependency rule: **arrows only point downward.** `db.py` imports nothing from
the project. `weather.py` and `geocoder.py` are leaves that do not touch the
database. `recommend.py` is the only module allowed to import both `routes` and
`transit`.

Nothing below `main.py` may call `print()`, `sys.exit()`, or `input()`.
Library modules raise; the CLI catches and formats. This is the rule that makes
the codebase testable, and it is the most common thing to get wrong.

---

## 2. Error contract

Defined once in `src/errors.py`:

```python
class HikingPlannerError(Exception):
    """Base for every error this project raises deliberately."""

class DataSourceError(HikingPlannerError):
    """An upstream API/file was unreachable or returned garbage."""

class NotFoundError(HikingPlannerError):
    """A lookup succeeded mechanically but produced no usable result."""

class DatabaseError(HikingPlannerError):
    """Local database missing, stale, or schema-mismatched."""
```

- Network failures (`requests.RequestException`) are caught at the module
  boundary and re-raised as `DataSourceError` with a readable message.
- Empty results are `NotFoundError`, never an empty list masquerading as
  success — *except* for query functions documented as "returns [] when nothing
  matches" (`load_routes`, `recommend`).
- `main.py` maps `HikingPlannerError` → friendly message + exit code 1.
  Unexpected exceptions propagate (we want the traceback during development).

---

## 3. Shared types

`src/models.py` — plain dataclasses, no behaviour, no I/O.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class BBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

@dataclass(frozen=True)
class Route:
    osm_id: int
    name: str
    ref: str | None
    network: str | None            # iwn | nwn | rwn | lwn | None
    length_km: float               # COMPUTED from geometry, never the OSM tag
    ascent_m: float | None
    descent_m: float | None
    roundtrip: bool | None
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    bbox: BBox
    geometry: list[tuple[float, float]]   # [(lat, lon), ...] ordered
    tags: dict[str, str]                  # raw OSM tags, unmodified
    fetched_at: str                       # ISO-8601 UTC, e.g. "2026-09-08T12:00:00+00:00"

@dataclass(frozen=True)
class Stop:
    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float

@dataclass(frozen=True)
class RouteAccess:
    osm_id: int
    endpoint: str                  # "start" | "end"
    stop_id: str
    walk_km: float                 # straight-line stop → route endpoint
    departures_per_day: int | None

@dataclass(frozen=True)
class ScoredRoute:
    route: Route
    total_score: float             # 0.0 – 1.0
    weather_score: float           # 0.0 – 1.0
    transit_score: float           # 0.0 – 1.0
    fit_score: float               # 0.0 – 1.0  (duration vs. available time)
    estimated_hours: float
    access: RouteAccess | None
    reasons: list[str]             # short human-readable justifications
```

**Region constant** (`src/models.py`):

```python
OBERBAYERN_BBOX = BBox(min_lat=47.27, min_lon=10.75, max_lat=48.55, max_lon=13.10)
```

---

## 4. SQLite schema

`src/db.py` owns this. Written exactly as below; the tests assert on it.

```sql
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- meta rows always present after init_db:
--   schema_version = "1"

CREATE TABLE IF NOT EXISTS routes (
    osm_id        INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    ref           TEXT,
    network       TEXT,
    length_km     REAL    NOT NULL,
    ascent_m      REAL,
    descent_m     REAL,
    roundtrip     INTEGER,            -- 0 | 1 | NULL
    start_lat     REAL    NOT NULL,
    start_lon     REAL    NOT NULL,
    end_lat       REAL    NOT NULL,
    end_lon       REAL    NOT NULL,
    bbox_min_lat  REAL    NOT NULL,
    bbox_min_lon  REAL    NOT NULL,
    bbox_max_lat  REAL    NOT NULL,
    bbox_max_lon  REAL    NOT NULL,
    geometry_json TEXT    NOT NULL,   -- JSON [[lat, lon], ...]
    tags_json     TEXT    NOT NULL,   -- JSON object, raw OSM tags
    fetched_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routes_length ON routes(length_km);
CREATE INDEX IF NOT EXISTS idx_routes_bbox
    ON routes(bbox_min_lat, bbox_max_lat, bbox_min_lon, bbox_max_lon);

CREATE TABLE IF NOT EXISTS stops (
    stop_id   TEXT PRIMARY KEY,
    stop_name TEXT NOT NULL,
    stop_lat  REAL NOT NULL,
    stop_lon  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stops_latlon ON stops(stop_lat, stop_lon);

CREATE TABLE IF NOT EXISTS route_access (
    osm_id             INTEGER NOT NULL,
    endpoint           TEXT    NOT NULL CHECK (endpoint IN ('start', 'end')),
    stop_id            TEXT    NOT NULL,
    walk_km            REAL    NOT NULL,
    departures_per_day INTEGER,
    PRIMARY KEY (osm_id, endpoint),
    FOREIGN KEY (osm_id)  REFERENCES routes(osm_id) ON DELETE CASCADE,
    FOREIGN KEY (stop_id) REFERENCES stops(stop_id)
);
```

`db.connect()` must execute `PRAGMA foreign_keys = ON` on every connection —
SQLite defaults it off, and without it the `route_access` foreign keys are
decoration.

### Public API of `src/db.py`

```python
SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path("data/hiking.db")

def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating parent dirs), set row_factory = sqlite3.Row,
    enable foreign keys. Does NOT create tables."""

def init_db(conn: sqlite3.Connection) -> None:
    """Idempotent: create all tables/indexes, upsert schema_version."""

def check_schema(conn: sqlite3.Connection) -> None:
    """Raise DatabaseError if tables missing or schema_version mismatched."""
```

---

## 5. Module contracts

### `src/weather.py` — **Phase 1, already implemented**

Existing public surface, treated as frozen:

```python
def fetch_weather(lat: float, lon: float, days: int = 3) -> list[dict]
def group_by_day(records: list[dict]) -> dict[str, list[dict]]
def summarize_day(records: list[dict]) -> dict
def print_forecast(lat, lon, location_name=None, records=None, daytime_only=True) -> None
```

`summarize_day` returns keys:
`temp_min, temp_max, total_rain, max_wind, dominant_condition, avg_cloud_cover, record_count`.

**Required Phase-2 refactor (small, do it first):**
- `print_forecast` violates the no-print rule. Replace it with

  ```python
  def format_forecast(lat: float, lon: float, location_name: str | None = None,
                      records: list[dict] | None = None,
                      daytime_only: bool = True) -> str
  ```

  returning the text; `main.py` prints it. Same parameters as before, so the
  call site changes by one line.
- `fetch_weather` re-raises as `DataSourceError`.
- `summarize_day([])` currently crashes downstream on `None` formatting —
  it must return the dict with `None`s and callers must handle it.

### `src/geocoder.py` — **Phase 1, already implemented**

```python
def geocode(query: str) -> tuple[float, float, str]
```

Required change: raise `NotFoundError` instead of bare `ValueError` on no
result; add a module-level `time.sleep(1)` rate-limit guard, because Nominatim's
usage policy is 1 request/second and getting the project's User-Agent banned
would be a self-inflicted wound.

### `src/routes.py` — **Phase 2**

```python
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

def build_overpass_query(bbox: BBox, networks: tuple[str, ...] = ("iwn", "nwn", "rwn", "lwn")) -> str
def fetch_raw(bbox: BBox, *, endpoint: str = OVERPASS_ENDPOINT, timeout: int = 180) -> dict
def parse_element(element: dict, *, fetched_at: str) -> Route | None
def parse_response(payload: dict, *, fetched_at: str | None = None) -> list[Route]
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float
def polyline_length_km(points: list[tuple[float, float]]) -> float
def stitch_members(element: dict) -> list[list[tuple[float, float]]]
def store_routes(conn, routes: list[Route]) -> int
def load_routes(conn, *, bbox: BBox | None = None, max_length_km: float | None = None,
                min_length_km: float | None = None) -> list[Route]
def tile_bbox(bbox: BBox, step_deg: float = 0.25) -> list[BBox]
```

### `src/transit.py` — **Phase 3**

```python
MVV_GTFS_URL = "https://www.mvv-muenchen.de/fileadmin/mediapool/developer/opendata/gesamt_gtfs.zip"

def download_gtfs(dest: Path, *, url: str = MVV_GTFS_URL) -> Path
def load_stops(gtfs_zip: Path, *, bbox: BBox | None = None) -> list[Stop]
def count_departures(gtfs_zip: Path, service_date: date) -> dict[str, int]
def store_stops(conn, stops: list[Stop]) -> int
def nearest_stop(lat: float, lon: float, stops: list[Stop], *, max_km: float = 3.0) -> tuple[Stop, float] | None
def build_route_access(conn, *, max_walk_km: float = 3.0,
                       departures: dict[str, int] | None = None) -> int
def transit_score(walk_km: float, departures_per_day: int | None) -> float
```

### `src/recommend.py` — **Phase 4**

```python
def estimate_duration_h(length_km: float, ascent_m: float | None,
                        descent_m: float | None) -> float
def weather_score(summary: dict) -> float
def fit_score(estimated_hours: float, available_hours: float) -> float
def score_route(route: Route, access: RouteAccess | None, summary: dict,
                available_hours: float) -> ScoredRoute
def recommend(conn, *, origin: tuple[float, float], day_summary: dict,
              available_hours: float, top_n: int = 5,
              max_walk_km: float = 3.0) -> list[ScoredRoute]
def to_gpx(route: Route) -> str
```

**Scoring weights** (module constants, so they are tunable and testable):

```python
WEIGHTS = {"weather": 0.40, "transit": 0.30, "fit": 0.30}
total_score = sum(WEIGHTS[k] * component[k] for k in WEIGHTS)
```

Hard vetoes (score forced to 0.0, reason recorded):
`dominant_condition == "thunderstorm"`, `total_rain > 10.0` mm,
`estimated_hours > available_hours`, no `RouteAccess` within `max_walk_km`.

### `src/web/` — **Phase 5**

Flask app factory `create_app(db_path)` + Leaflet single page. Read-only: the
web layer queries the same DB and calls `recommend()`; it never fetches from
upstream APIs. Details in `docs/delegation/phase5-web.md`.

---

## 6. CLI surface (`main.py`)

```
python main.py weather   --location NAME | --lat LAT --lon LON  [--days N] [--all-hours]
python main.py routes    build [--bbox MINLAT,MINLON,MAXLAT,MAXLON] [--step 0.25] [--db PATH]
python main.py routes    list  [--max-km N] [--min-km N] [--limit N]
python main.py transit   build [--gtfs PATH | --download] [--max-walk-km N]
python main.py recommend --location NAME --hours N [--date YYYY-MM-DD] [--top N] [--gpx DIR]
python main.py serve     [--host H] [--port P]
```

Every subcommand takes `--db PATH` (default `data/hiking.db`).

---

## 7. Conventions

- Python ≥ 3.10 (the codebase already uses `X | None`).
- Type hints on every public function. `from __future__ import annotations` at
  the top of every module.
- Docstrings and user-facing CLI strings: Chinese is fine (it already is).
  **Code identifiers, commit messages, and this document: English only.**
- No new runtime dependency without a note in the delegation brief. Target
  runtime deps for the whole project: `requests`, `flask`. Everything else
  (zipfile, csv, sqlite3, math, json) is stdlib. `pandas` is **not** needed for
  GTFS — the stops/stop_times parsing is a `csv.DictReader` streaming job, and
  pulling in pandas for it would be the wrong call on a 200 MB feed.
- Dev deps: `pytest`, `pytest-cov`, `ruff`.
- Never commit `data/*.db`, `data/*.zip`, or `data/cache/` (already in
  `.gitignore`).
