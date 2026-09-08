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
