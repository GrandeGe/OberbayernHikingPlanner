from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BBox:
    """Geographic bounds in latitude/longitude order."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


@dataclass(frozen=True)
class Route:
    """An OSM hiking route with geometry-derived length in kilometres."""

    osm_id: int
    name: str
    ref: str | None
    network: str | None
    length_km: float
    ascent_m: float | None
    descent_m: float | None
    roundtrip: bool | None
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    bbox: BBox
    geometry: list[tuple[float, float]]
    tags: dict[str, str]
    fetched_at: str


@dataclass(frozen=True)
class Stop:
    """A public transport stop and its coordinates."""

    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float


@dataclass(frozen=True)
class RouteAccess:
    """Access from a transport stop to a route endpoint."""

    osm_id: int
    endpoint: str
    stop_id: str
    walk_km: float
    departures_per_day: int | None


@dataclass(frozen=True)
class ScoredRoute:
    """A route recommendation with component scores and explanations."""

    route: Route
    total_score: float
    weather_score: float
    transit_score: float
    fit_score: float
    estimated_hours: float
    access: RouteAccess | None
    reasons: list[str]


OBERBAYERN_BBOX = BBox(min_lat=47.27, min_lon=10.75, max_lat=48.55, max_lon=13.10)
