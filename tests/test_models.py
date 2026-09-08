"""Contract tests for src/models.py and src/errors.py.

These assert the interface in docs/ARCHITECTURE.md §3. They are deliberately
picky about field names: every later phase reads these attributes by name, and
a rename here breaks the whole chain silently.
"""

from __future__ import annotations

import dataclasses

from conftest import require

models = require("src.models", "BBox", "Route", "Stop", "RouteAccess", "ScoredRoute")
errors = require(
    "src.errors",
    "HikingPlannerError",
    "DataSourceError",
    "NotFoundError",
    "DatabaseError",
)


def field_names(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_bbox_fields():
    assert field_names(models.BBox) == {"min_lat", "min_lon", "max_lat", "max_lon"}


def test_route_fields():
    assert field_names(models.Route) == {
        "osm_id", "name", "ref", "network", "length_km",
        "ascent_m", "descent_m", "roundtrip",
        "start_lat", "start_lon", "end_lat", "end_lon",
        "bbox", "geometry", "tags", "fetched_at",
    }


def test_stop_fields():
    assert field_names(models.Stop) == {"stop_id", "stop_name", "stop_lat", "stop_lon"}


def test_route_access_fields():
    assert field_names(models.RouteAccess) == {
        "osm_id", "endpoint", "stop_id", "walk_km", "departures_per_day",
    }


def test_scored_route_fields():
    assert field_names(models.ScoredRoute) == {
        "route", "total_score", "weather_score", "transit_score",
        "fit_score", "estimated_hours", "access", "reasons",
    }


def test_dataclasses_are_frozen():
    for cls in (models.BBox, models.Route, models.Stop, models.RouteAccess):
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} must be frozen"


def test_oberbayern_bbox_covers_the_region():
    bbox = models.OBERBAYERN_BBOX
    # Munich, Schliersee and Berchtesgaden must all fall inside.
    for lat, lon in [(48.1372, 11.5755), (47.7256, 11.8583), (47.6300, 13.0000)]:
        assert bbox.min_lat <= lat <= bbox.max_lat
        assert bbox.min_lon <= lon <= bbox.max_lon
    # Nuremberg must not.
    assert not (bbox.min_lat <= 49.4521 <= bbox.max_lat)


def test_error_hierarchy():
    for cls in (errors.DataSourceError, errors.NotFoundError, errors.DatabaseError):
        assert issubclass(cls, errors.HikingPlannerError)
    assert issubclass(errors.HikingPlannerError, Exception)
