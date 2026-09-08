"""Contract tests for src/db.py — schema, pragmas, idempotency."""

from __future__ import annotations

import sqlite3

import pytest
from conftest import require

db = require("src.db", "connect", "init_db", "check_schema", "SCHEMA_VERSION")
errors = require("src.errors", "DatabaseError")

EXPECTED_TABLES = {"meta", "routes", "stops", "route_access"}

EXPECTED_ROUTE_COLUMNS = {
    "osm_id", "name", "ref", "network", "length_km", "ascent_m", "descent_m",
    "roundtrip", "start_lat", "start_lon", "end_lat", "end_lon",
    "bbox_min_lat", "bbox_min_lon", "bbox_max_lat", "bbox_max_lon",
    "geometry_json", "tags_json", "fetched_at",
}


def table_names(conn) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r[0] for r in rows if not r[0].startswith("sqlite_")}


def test_init_db_creates_all_tables(db_conn):
    assert table_names(db_conn) >= EXPECTED_TABLES


def test_routes_columns_match_contract(db_conn):
    cols = {r[1] for r in db_conn.execute("PRAGMA table_info(routes)")}
    assert cols == EXPECTED_ROUTE_COLUMNS


def test_schema_version_recorded(db_conn):
    row = db_conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert row is not None, "init_db must write schema_version into meta"
    assert int(row[0]) == db.SCHEMA_VERSION


def test_init_db_is_idempotent(db_conn):
    db.init_db(db_conn)
    db.init_db(db_conn)
    assert table_names(db_conn) >= EXPECTED_TABLES


def test_foreign_keys_are_enabled(db_conn):
    """SQLite defaults foreign_keys OFF; without the pragma the route_access
    foreign keys are decorative."""
    assert db_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_row_factory_is_sqlite3_row(db_conn):
    assert db_conn.row_factory is sqlite3.Row


def test_route_access_rejects_unknown_route(db_conn):
    db_conn.execute(
        "INSERT INTO stops VALUES (?, ?, ?, ?)", ("S1", "Test", 47.7, 11.8)
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO route_access VALUES (?, ?, ?, ?, ?)",
            (999999, "start", "S1", 0.5, 10),
        )


def test_route_access_rejects_bad_endpoint(db_conn):
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO route_access VALUES (?, ?, ?, ?, ?)",
            (1, "middle", "S1", 0.5, 10),
        )


def test_connect_creates_parent_directories(tmp_path):
    nested = tmp_path / "a" / "b" / "hiking.db"
    conn = db.connect(nested)
    try:
        assert nested.parent.is_dir()
    finally:
        conn.close()


def test_check_schema_raises_on_empty_database(tmp_path):
    conn = db.connect(tmp_path / "empty.db")
    try:
        with pytest.raises(errors.DatabaseError):
            db.check_schema(conn)
    finally:
        conn.close()


def test_check_schema_passes_after_init(db_conn):
    db.check_schema(db_conn)  # must not raise
