"""Acceptance tests for Phase 2 — src/routes.py.

Every test here is offline: the Overpass response is a fixture. The reference
lengths in conftest.py were computed independently with a standalone haversine
implementation, so a bug in routes.py cannot make them agree by accident.
"""

from __future__ import annotations

import math
import re

import pytest
from conftest import (
    LOOP_POLYLINE_KM,
    MAIN_POLYLINE_KM,
    SEGMENT_LONG_KM,
    require,
)

routes = require(
    "src.routes",
    "build_overpass_query", "parse_element", "parse_response",
    "haversine_km", "polyline_length_km", "stitch_members",
    "store_routes", "load_routes", "tile_bbox",
)
models = require("src.models", "BBox", "OBERBAYERN_BBOX")
errors = require("src.errors", "DataSourceError")

FETCHED_AT = "2026-09-08T10:00:00+00:00"


@pytest.fixture
def parsed(overpass_payload):
    """All parseable routes from the fixture, keyed by osm_id."""
    return {r.osm_id: r for r in routes.parse_response(overpass_payload, fetched_at=FETCHED_AT)}


# ─────────────────────────── geometry maths ────────────────────────────


def test_haversine_one_degree_of_latitude():
    assert routes.haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.195, abs=0.01)


def test_haversine_is_symmetric():
    a = routes.haversine_km(47.7, 11.8, 48.1, 11.5)
    b = routes.haversine_km(48.1, 11.5, 47.7, 11.8)
    assert a == pytest.approx(b, abs=1e-9)


def test_haversine_zero_distance():
    assert routes.haversine_km(47.7, 11.8, 47.7, 11.8) == pytest.approx(0.0, abs=1e-9)


def test_haversine_munich_to_schliersee():
    """Sanity anchor against a real-world distance (~50 km)."""
    d = routes.haversine_km(48.1372, 11.5755, 47.7256, 11.8583)
    assert d == pytest.approx(50.385, abs=0.05)


@pytest.mark.parametrize("points", [[], [(47.7, 11.8)]])
def test_polyline_length_of_degenerate_input_is_zero(points):
    assert routes.polyline_length_km(points) == 0.0


def test_polyline_length_matches_reference():
    pts = [(47.70, 11.80), (47.71, 11.81), (47.72, 11.82)]
    assert routes.polyline_length_km(pts) == pytest.approx(MAIN_POLYLINE_KM, abs=1e-3)


# ─────────────────────────── query building ────────────────────────────


def test_query_uses_overpass_bbox_order():
    """Overpass wants (south, west, north, east). Reversing this returns zero
    results silently, which is the single most likely bug in this phase."""
    q = routes.build_overpass_query(models.BBox(47.27, 10.75, 48.55, 13.10))
    match = re.search(r"\(\s*47\.27\d*\s*,\s*10\.75\d*\s*,\s*48\.55\d*\s*,\s*13\.10\d*\s*\)", q)
    assert match, f"bbox not emitted as (min_lat,min_lon,max_lat,max_lon):\n{q}"


def test_query_requests_json_and_geometry():
    q = routes.build_overpass_query(models.OBERBAYERN_BBOX)
    assert "[out:json]" in q
    assert "out geom" in q, "out tags/body give no coordinates"
    assert 'relation' in q
    assert '"route"="hiking"' in q.replace(" ", "")


def test_query_honours_network_filter():
    q = routes.build_overpass_query(models.OBERBAYERN_BBOX, networks=("rwn", "lwn"))
    assert "rwn" in q and "lwn" in q
    assert "iwn" not in q and "nwn" not in q


# ──────────────────────────── tiling ───────────────────────────────────


def test_tile_bbox_covers_the_whole_region():
    bbox = models.BBox(47.0, 11.0, 48.0, 12.0)
    tiles = routes.tile_bbox(bbox, step_deg=0.25)
    assert len(tiles) == 16
    assert min(t.min_lat for t in tiles) == pytest.approx(47.0)
    assert max(t.max_lat for t in tiles) == pytest.approx(48.0)
    assert min(t.min_lon for t in tiles) == pytest.approx(11.0)
    assert max(t.max_lon for t in tiles) == pytest.approx(12.0)


def test_tile_bbox_handles_non_multiple_extent():
    """The last row/column may be narrower, but must not overshoot."""
    bbox = models.BBox(47.0, 11.0, 47.6, 11.6)
    tiles = routes.tile_bbox(bbox, step_deg=0.25)
    assert max(t.max_lat for t in tiles) == pytest.approx(47.6)
    assert max(t.max_lon for t in tiles) == pytest.approx(11.6)
    assert all(t.max_lat <= 47.6 + 1e-9 for t in tiles)


def test_tile_bbox_smaller_than_step_returns_one_tile():
    tiles = routes.tile_bbox(models.BBox(47.0, 11.0, 47.1, 11.1), step_deg=0.25)
    assert len(tiles) == 1


# ──────────────────────────── stitching ────────────────────────────────


def test_stitches_two_connected_ways(overpass_payload):
    element = next(e for e in overpass_payload["elements"] if e["id"] == 1001)
    segments = routes.stitch_members(element)
    assert len(segments) == 1
    assert segments[0] == [
        pytest.approx((47.70, 11.80)),
        pytest.approx((47.71, 11.81)),
        pytest.approx((47.72, 11.82)),
    ]


def test_reversed_way_is_flipped_before_joining(overpass_payload):
    """Relation 1002 holds the second way in reverse order. OSM does this all
    the time; a stitcher that only matches head-to-tail produces two fragments
    and silently halves the route length."""
    element = next(e for e in overpass_payload["elements"] if e["id"] == 1002)
    segments = routes.stitch_members(element)
    assert len(segments) == 1, "reversed way was not recognised as connecting"
    assert len(segments[0]) == 3


def test_node_members_are_ignored(overpass_payload):
    """Relation 1001 includes a guidepost node; it must not enter the geometry."""
    element = next(e for e in overpass_payload["elements"] if e["id"] == 1001)
    flat = [p for seg in routes.stitch_members(element) for p in seg]
    assert not any(math.isclose(p[0], 47.705) for p in flat)


def test_segments_sorted_longest_first(overpass_payload):
    element = next(e for e in overpass_payload["elements"] if e["id"] == 1003)
    segments = routes.stitch_members(element)
    assert len(segments) == 2
    lengths = [routes.polyline_length_km(s) for s in segments]
    assert lengths == sorted(lengths, reverse=True)


def test_relation_without_way_members_yields_no_segments(overpass_payload):
    element = next(e for e in overpass_payload["elements"] if e["id"] == 1004)
    assert routes.stitch_members(element) == []


# ──────────────────────────── parsing ──────────────────────────────────


def test_parses_expected_relations(parsed):
    """1004 (nodes only) and 1005 (no name, no ref) must be skipped."""
    assert set(parsed) == {1001, 1002, 1003, 1006, 1007, 1008}


def test_length_is_computed_not_taken_from_the_distance_tag(parsed):
    """Relation 1001 carries distance="12,5 km" while its geometry is ~2.7 km.
    The OSM tag is free-text and often wrong; the geometry is the truth."""
    assert parsed[1001].length_km == pytest.approx(MAIN_POLYLINE_KM, abs=1e-3)


def test_longest_segment_wins_and_discards_are_recorded(parsed):
    route = parsed[1003]
    assert route.length_km == pytest.approx(SEGMENT_LONG_KM, abs=1e-3)
    assert route.tags["_discarded_segments"] == "1"


def test_no_discarded_segments_recorded_as_zero(parsed):
    assert parsed[1001].tags["_discarded_segments"] == "0"


def test_endpoints_come_from_the_kept_geometry(parsed):
    route = parsed[1001]
    assert (route.start_lat, route.start_lon) == pytest.approx((47.70, 11.80))
    assert (route.end_lat, route.end_lon) == pytest.approx((47.72, 11.82))


def test_bbox_excludes_discarded_segments(parsed):
    """Relation 1003's element bounds reach 48.0/12.0 because of the discarded
    fragment. The stored bbox must describe the geometry we actually kept."""
    bbox = parsed[1003].bbox
    assert bbox.max_lat == pytest.approx(47.64)
    assert bbox.max_lon == pytest.approx(11.64)


def test_ref_is_used_as_name_when_name_is_missing(parsed):
    assert parsed[1006].name == "M1"
    assert parsed[1006].ref == "M1"


def test_ascent_and_descent_parsed_with_and_without_unit(parsed):
    assert parsed[1001].ascent_m == pytest.approx(620.0)
    assert parsed[1001].descent_m == pytest.approx(620.0)


def test_unparseable_ascent_becomes_none(parsed):
    """ascent="ca. 600" is not a number. Guessing 600 would put an invented
    figure into a duration estimate the user relies on."""
    assert parsed[1008].ascent_m is None
    assert parsed[1008].descent_m is None


def test_roundtrip_tag_respected(parsed):
    assert parsed[1001].roundtrip is False


def test_closed_loop_detected_without_a_tag(parsed):
    assert parsed[1007].roundtrip is True
    assert parsed[1007].length_km == pytest.approx(LOOP_POLYLINE_KM, abs=1e-3)


def test_raw_tags_preserved(parsed):
    assert parsed[1001].tags["network"] == "lwn"
    assert parsed[1001].tags["distance"] == "12,5 km"


def test_fetched_at_propagated(parsed):
    assert all(r.fetched_at == FETCHED_AT for r in parsed.values())


def test_parse_response_rejects_payload_without_elements():
    with pytest.raises(errors.DataSourceError):
        routes.parse_response({"version": 0.6, "remark": "runtime error"})


# ──────────────────────────── persistence ──────────────────────────────


def test_store_and_load_roundtrip(db_conn, overpass_payload):
    parsed_routes = routes.parse_response(overpass_payload, fetched_at=FETCHED_AT)
    written = routes.store_routes(db_conn, parsed_routes)
    assert written == len(parsed_routes)

    loaded = {r.osm_id: r for r in routes.load_routes(db_conn)}
    assert set(loaded) == {r.osm_id for r in parsed_routes}

    original = next(r for r in parsed_routes if r.osm_id == 1001)
    restored = loaded[1001]
    assert restored.geometry == [pytest.approx(p) for p in original.geometry]
    assert restored.tags == original.tags
    assert restored.length_km == pytest.approx(original.length_km)
    assert restored.roundtrip is False
    assert restored.bbox.min_lat == pytest.approx(original.bbox.min_lat)


def test_store_routes_is_idempotent(db_conn, overpass_payload):
    """Adjacent tiles both return a relation that straddles their border.
    A second write must update, not raise."""
    parsed_routes = routes.parse_response(overpass_payload, fetched_at=FETCHED_AT)
    routes.store_routes(db_conn, parsed_routes)
    routes.store_routes(db_conn, parsed_routes)
    assert db_conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == len(parsed_routes)


def test_load_routes_length_filters(db_conn, overpass_payload):
    routes.store_routes(db_conn, routes.parse_response(overpass_payload, fetched_at=FETCHED_AT))
    short = routes.load_routes(db_conn, max_length_km=3.0)
    assert all(r.length_km <= 3.0 for r in short)
    assert 1003 not in {r.osm_id for r in short}   # 5.36 km
    assert 1001 in {r.osm_id for r in short}       # 2.68 km

    long_ = routes.load_routes(db_conn, min_length_km=4.0)
    assert {r.osm_id for r in long_} == {1003}


def test_load_routes_bbox_filter_is_an_overlap_test(db_conn, overpass_payload):
    routes.store_routes(db_conn, routes.parse_response(overpass_payload, fetched_at=FETCHED_AT))
    near_schliersee = routes.load_routes(db_conn, bbox=models.BBox(47.69, 11.79, 47.73, 11.83))
    ids = {r.osm_id for r in near_schliersee}
    assert 1001 in ids
    assert 1003 not in ids, "route at 47.60–47.64 must not match a 47.69+ query box"


def test_load_routes_returns_empty_list_not_an_error(db_conn):
    """An empty result set is a legitimate answer, not NotFoundError."""
    assert routes.load_routes(db_conn, max_length_km=0.001) == []


# ──────────────────────────── live smoke test ──────────────────────────


@pytest.mark.live
def test_live_overpass_query_returns_routes():
    """Deselected in CI (`-m "not live"`). Run locally to verify the query
    still matches the real Overpass API."""
    payload = routes.fetch_raw(models.BBox(47.70, 11.80, 47.80, 11.90))
    parsed_routes = routes.parse_response(payload)
    assert len(parsed_routes) > 5
    assert all(r.length_km > 0 for r in parsed_routes)
