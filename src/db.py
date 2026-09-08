from __future__ import annotations

import sqlite3
from pathlib import Path

from src.errors import DatabaseError

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path("data/hiking.db")

_SCHEMA = """
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
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a database, create parent dirs, enable foreign keys and Row results.

    Tables are created separately by init_db. Failures raise DatabaseError.
    """
    conn = None
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        raise DatabaseError(f"Cannot open database {db_path!s}: {exc}") from exc


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotently create all contract tables/indexes and upsert schema_version."""
    try:
        with conn:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
    except sqlite3.Error as exc:
        raise DatabaseError(f"Cannot initialize database: {exc}") from exc


def check_schema(conn: sqlite3.Connection) -> None:
    """Raise DatabaseError if required tables are missing or the version differs."""
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = {"meta", "routes", "stops", "route_access"} - tables
        if missing:
            raise DatabaseError(f"Database tables missing: {', '.join(sorted(missing))}")
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row[0] != str(SCHEMA_VERSION):
            actual = row[0] if row is not None else "missing"
            raise DatabaseError(
                f"Database schema version mismatch: expected {SCHEMA_VERSION}, got {actual}"
            )
    except sqlite3.Error as exc:
        raise DatabaseError(f"Cannot check database schema: {exc}") from exc
