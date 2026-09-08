"""Acceptance tests for Phase 4 — src/recommend.py.

The duration model is DIN 33466 (the German Alpine Club standard used on
Bavarian trail signs); its reference values below were computed by hand from
the published formula, not from this codebase.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from conftest import require

recommend = require(
    "src.recommend",
    "estimate_duration_h", "weather_score", "fit_score",
    "score_route", "recommend", "to_gpx", "WEIGHTS",
)
models = require("src.models", "BBox", "Route", "RouteAccess")

GPX_NS = "http://www.topografix.com/GPX/1/1"


def make_route(**overrides):
    base = dict(
        osm_id=1001,
        name="Bodenschneid-Runde",
        ref="BR1",
        network="lwn",
        length_km=12.4,
        ascent_m=620.0,
        descent_m=620.0,
        roundtrip=True,
        start_lat=47.70, start_lon=11.80,
        end_lat=47.70, end_lon=11.80,
        bbox=models.BBox(47.70, 11.80, 47.75, 11.85),
        geometry=[(47.70, 11.80), (47.72, 11.82), (47.70, 11.80)],
        tags={"network": "lwn", "_discarded_segments": "0"},
        fetched_at="2026-09-08T10:00:00+00:00",
    )
    base.update(overrides)
    return models.Route(**base)


def make_access(**overrides):
    base = dict(
        osm_id=1001, endpoint="start", stop_id="DE:09182:2",
        walk_km=0.4, departures_per_day=48,
    )
    base.update(overrides)
    return models.RouteAccess(**base)


# ──────────────────────── DIN 33466 duration ───────────────────────────


def test_din33466_reference_case():
    """12.4 km, 620 m up, 620 m down.
    horizontal = 12.4/4 = 3.100 h
    vertical   = 620/300 + 620/500 = 3.30667 h
    total      = max + min/2 = 3.30667 + 1.550 = 4.85667 h
    """
    assert recommend.estimate_duration_h(12.4, 620.0, 620.0) == pytest.approx(4.856667, abs=1e-4)


def test_flat_route_uses_the_horizontal_term_only():
    assert recommend.estimate_duration_h(12.0, 0.0, 0.0) == pytest.approx(3.0, abs=1e-6)


def test_unknown_elevation_falls_back_to_a_slower_flat_pace():
    """No invented ascent figure. 3.5 km/h implicitly allows for unknown terrain."""
    assert recommend.estimate_duration_h(10.0, None, None) == pytest.approx(10 / 3.5, abs=1e-6)


def test_half_known_elevation_mirrors_the_known_half():
    """h = 2.5, v = 600/300 + 600/500 = 3.2, total = 3.2 + 1.25 = 4.45"""
    assert recommend.estimate_duration_h(10.0, 600.0, None) == pytest.approx(4.45, abs=1e-4)
    assert recommend.estimate_duration_h(10.0, None, 600.0) == pytest.approx(4.45, abs=1e-4)


def test_duration_is_monotonic_in_length_and_ascent():
    assert recommend.estimate_duration_h(20.0, 500, 500) > recommend.estimate_duration_h(10.0, 500, 500)
    assert recommend.estimate_duration_h(10.0, 1500, 500) > recommend.estimate_duration_h(10.0, 500, 500)


def test_zero_length_is_zero_hours():
    assert recommend.estimate_duration_h(0.0, 0.0, 0.0) == pytest.approx(0.0)


# ──────────────────────────── weather score ────────────────────────────


def test_good_day_scores_high(sample_summary):
    """rain 1.0×0.40 + temp 1.0×0.25 + wind 1.0×0.20 + cloud 0.86×0.15 = 0.979"""
    assert recommend.weather_score(sample_summary) == pytest.approx(0.979, abs=1e-3)


def test_thunderstorm_is_a_hard_veto(sample_summary):
    """Exposed Bavarian ridges in a thunderstorm are a safety matter, not a
    0.3 penalty."""
    summary = {**sample_summary, "dominant_condition": "thunderstorm"}
    assert recommend.weather_score(summary) == 0.0


def test_heavy_rain_is_a_hard_veto(sample_summary):
    assert recommend.weather_score({**sample_summary, "total_rain": 10.5}) == 0.0


def test_moderate_rain_halves_the_rain_component(sample_summary):
    """2.5 mm → rain component 0.5, costing 0.20 of the total."""
    scored = recommend.weather_score({**sample_summary, "total_rain": 2.5})
    assert scored == pytest.approx(0.979 - 0.20, abs=1e-3)


def test_cold_and_hot_days_score_lower(sample_summary):
    baseline = recommend.weather_score(sample_summary)
    assert recommend.weather_score({**sample_summary, "temp_max": 2.0}) < baseline
    assert recommend.weather_score({**sample_summary, "temp_max": 32.0}) < baseline


def test_strong_wind_scores_lower(sample_summary):
    assert recommend.weather_score({**sample_summary, "max_wind": 45.0}) < \
        recommend.weather_score(sample_summary)


def test_missing_components_are_renormalised_not_treated_as_zero(sample_summary):
    """A forecast missing cloud cover must not be punished for the gap."""
    partial = {**sample_summary, "avg_cloud_cover": None}
    assert recommend.weather_score(partial) == pytest.approx(1.0, abs=1e-6)


def test_completely_empty_summary_is_neutral():
    empty = {
        "temp_min": None, "temp_max": None, "total_rain": None,
        "max_wind": None, "dominant_condition": "unknown",
        "avg_cloud_cover": None, "record_count": 0,
    }
    assert recommend.weather_score(empty) == pytest.approx(0.5, abs=1e-6)


@pytest.mark.parametrize("rain,temp,wind,cloud", [
    (0, 18, 5, 0), (4.9, 30, 49, 100), (0, -10, 0, 50), (0, 40, 60, 100),
])
def test_weather_score_always_in_range(rain, temp, wind, cloud):
    summary = {
        "temp_min": temp - 5, "temp_max": temp, "total_rain": rain,
        "max_wind": wind, "dominant_condition": "dry",
        "avg_cloud_cover": cloud, "record_count": 24,
    }
    assert 0.0 <= recommend.weather_score(summary) <= 1.0


# ──────────────────────────── fit score ────────────────────────────────


def test_route_that_fills_the_day_scores_full():
    assert recommend.fit_score(5.0, 6.0) == pytest.approx(1.0)
    assert recommend.fit_score(6.0, 6.0) == pytest.approx(1.0)
    assert recommend.fit_score(3.6, 6.0) == pytest.approx(1.0)


def test_route_longer_than_available_time_is_vetoed():
    assert recommend.fit_score(6.5, 6.0) == 0.0


def test_short_route_scores_proportionally():
    """ratio 2/6 = 0.3333 → 0.3333/0.6 = 0.5556"""
    assert recommend.fit_score(2.0, 6.0) == pytest.approx(0.5556, abs=1e-3)


def test_non_positive_available_hours_raises():
    with pytest.raises(ValueError):
        recommend.fit_score(3.0, 0.0)


# ──────────────────────────── score_route ──────────────────────────────


def test_weights_sum_to_one():
    assert sum(recommend.WEIGHTS.values()) == pytest.approx(1.0)
    assert set(recommend.WEIGHTS) == {"weather", "transit", "fit"}


def test_score_route_combines_components(sample_summary):
    scored = recommend.score_route(make_route(), make_access(), sample_summary, 6.0)
    expected = (
        recommend.WEIGHTS["weather"] * scored.weather_score
        + recommend.WEIGHTS["transit"] * scored.transit_score
        + recommend.WEIGHTS["fit"] * scored.fit_score
    )
    assert scored.total_score == pytest.approx(expected, abs=1e-6)
    assert 0.0 <= scored.total_score <= 1.0
    assert scored.estimated_hours == pytest.approx(4.856667, abs=1e-4)


def test_no_transit_access_is_a_veto(sample_summary):
    scored = recommend.score_route(make_route(), None, sample_summary, 6.0)
    assert scored.total_score == 0.0
    assert scored.transit_score == 0.0


def test_veto_preserves_the_component_scores(sample_summary):
    """The user should be able to see *why* a route was rejected. Zeroing the
    components as well throws that information away."""
    scored = recommend.score_route(make_route(), make_access(), sample_summary, 2.0)
    assert scored.total_score == 0.0
    assert scored.fit_score == 0.0
    assert scored.weather_score > 0.5, "weather was fine; only the fit failed"


def test_reasons_are_populated(sample_summary):
    scored = recommend.score_route(make_route(), make_access(), sample_summary, 6.0)
    assert 1 <= len(scored.reasons) <= 4
    assert all(isinstance(r, str) and r for r in scored.reasons)


def test_unknown_elevation_is_flagged_in_the_reasons(sample_summary):
    route = make_route(ascent_m=None, descent_m=None)
    scored = recommend.score_route(route, make_access(), sample_summary, 6.0)
    assert any("估" in r or "未知" in r or "estimat" in r.lower() for r in scored.reasons), \
        "an estimate built on missing elevation data must say so"


# ──────────────────────────── recommend() ──────────────────────────────


@pytest.fixture
def seeded_db(db_conn, overpass_payload, gtfs_zip):
    src_routes = require("src.routes", "parse_response", "store_routes")
    transit = require("src.transit", "load_stops", "store_stops", "build_route_access")
    src_routes.store_routes(db_conn, src_routes.parse_response(overpass_payload))
    transit.store_stops(db_conn, transit.load_stops(gtfs_zip))
    transit.build_route_access(db_conn, departures={"DE:09182:2": 48, "DE:09182:3": 12})
    return db_conn


def test_recommend_returns_ranked_results(seeded_db, sample_summary):
    results = recommend.recommend(
        seeded_db, origin=(48.1372, 11.5755), day_summary=sample_summary,
        available_hours=6.0, top_n=5,
    )
    scores = [r.total_score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert all(s > 0 for s in scores), "vetoed routes must not appear"
    assert len(results) <= 5


def test_recommend_respects_top_n(seeded_db, sample_summary):
    results = recommend.recommend(
        seeded_db, origin=(48.1372, 11.5755), day_summary=sample_summary,
        available_hours=6.0, top_n=1,
    )
    assert len(results) <= 1


def test_recommend_returns_empty_list_when_nothing_fits(seeded_db, sample_summary):
    """Not an error. The CLI then suggests widening --hours."""
    assert recommend.recommend(
        seeded_db, origin=(48.1372, 11.5755), day_summary=sample_summary,
        available_hours=0.05,
    ) == []


def test_recommend_returns_empty_list_in_a_thunderstorm(seeded_db, sample_summary):
    results = recommend.recommend(
        seeded_db, origin=(48.1372, 11.5755),
        day_summary={**sample_summary, "dominant_condition": "thunderstorm"},
        available_hours=6.0,
    )
    assert results == []


# ──────────────────────────── GPX export ───────────────────────────────


def test_gpx_is_well_formed_and_namespaced():
    root = ET.fromstring(recommend.to_gpx(make_route()))
    assert root.tag == f"{{{GPX_NS}}}gpx"
    assert root.get("version") == "1.1"
    assert root.get("creator")


def test_gpx_contains_every_geometry_point():
    route = make_route()
    root = ET.fromstring(recommend.to_gpx(route))
    points = root.findall(f".//{{{GPX_NS}}}trkpt")
    assert len(points) == len(route.geometry)
    assert points[0].get("lat") is not None and points[0].get("lon") is not None
    assert float(points[0].get("lat")) == pytest.approx(route.geometry[0][0])


def test_gpx_carries_osm_attribution():
    """ODbL requires it."""
    gpx = recommend.to_gpx(make_route())
    assert "OpenStreetMap" in gpx


def test_gpx_escapes_special_characters_in_names():
    """Route names contain & and umlauts. Hand-built XML produces files that
    Garmin and Komoot reject."""
    gpx = recommend.to_gpx(make_route(name='Kreuzeck & "Alpspitze" <Höhenweg>'))
    root = ET.fromstring(gpx)   # would raise on malformed XML
    names = [e.text for e in root.iter(f"{{{GPX_NS}}}name")]
    assert 'Kreuzeck & "Alpspitze" <Höhenweg>' in names
