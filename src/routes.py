"""Fetch, stitch and persist OpenStreetMap hiking route relations."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests

from src.errors import DatabaseError, DataSourceError
from src.models import BBox, Route

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_HEADERS = {
    "User-Agent": (
        "OberbayernHikingPlanner/1.0 "
        "(+https://github.com/GrandeGe/OberbayernHikingPlanner)"
    ),
    "Referer": "https://github.com/GrandeGe/OberbayernHikingPlanner",
}
EARTH_RADIUS_KM = 6371.0088
_COLUMNS = (
    "osm_id", "name", "ref", "network", "length_km", "ascent_m", "descent_m",
    "roundtrip", "start_lat", "start_lon", "end_lat", "end_lon",
    "bbox_min_lat", "bbox_min_lon", "bbox_max_lat", "bbox_max_lon",
    "geometry_json", "tags_json", "fetched_at",
)


def _validate_bbox(bbox: BBox) -> None:
    values = (bbox.min_lat, bbox.min_lon, bbox.max_lat, bbox.max_lon)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Bounding box coordinates must be finite.")
    if not (-90 <= bbox.min_lat < bbox.max_lat <= 90):
        raise ValueError("Bounding box must have -90 <= south < north <= 90.")
    if not (-180 <= bbox.min_lon < bbox.max_lon <= 180):
        raise ValueError("Bounding box must have -180 <= west < east <= 180.")


def build_overpass_query(
    bbox: BBox, networks: tuple[str, ...] = ("iwn", "nwn", "rwn", "lwn")
) -> str:
    """Build a geometry query with coordinates ordered south, west, north, east."""
    _validate_bbox(bbox)
    if not networks or any(n not in {"iwn", "nwn", "rwn", "lwn"} for n in networks):
        raise ValueError("Choose at least one hiking network: iwn, nwn, rwn, lwn.")
    return (
        '[out:json][timeout:180];\n(\n'
        '  relation["type"="route"]["route"="hiking"]'
        f'["network"~"^({"|".join(networks)})$"]\n'
        f"    ({bbox.min_lat:.5f},{bbox.min_lon:.5f},"
        f"{bbox.max_lat:.5f},{bbox.max_lon:.5f});\n);\nout geom;"
    )


def fetch_raw(
    bbox: BBox, *, endpoint: str = OVERPASS_ENDPOINT, timeout: int = 180
) -> dict:
    """Fetch one tile; retry HTTP 429/504 up to three times, 30 seconds apart."""
    query = build_overpass_query(bbox)
    if timeout <= 0:
        raise ValueError("Timeout must be positive.")
    for attempt in range(4):
        try:
            response = requests.post(
                endpoint, data={"data": query}, headers=OVERPASS_HEADERS, timeout=timeout
            )
            if response.status_code in (429, 504) and attempt < 3:
                time.sleep(30)
                continue
            response.raise_for_status()
            payload = response.json()
            _validate_payload(payload)
            return payload
        except (requests.RequestException, ValueError) as exc:
            raise DataSourceError(f"Overpass tile {bbox}: {exc}") from exc
    raise AssertionError("Overpass retry loop ended unexpectedly")


def _validate_payload(payload: dict) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("Overpass response must contain an elements list.")
    if payload.get("remark"):
        raise ValueError(f"Overpass reported an incomplete query: {payload['remark']}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two coordinates in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def polyline_length_km(points: list[tuple[float, float]]) -> float:
    """Sum distances between consecutive points; degenerate polylines have length zero."""
    return sum((haversine_km(*a, *b) for a, b in zip(points, points[1:], strict=False)), 0.0)


def _same_point(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= 1e-7 and abs(a[1] - b[1]) <= 1e-7


def stitch_members(element: dict) -> list[list[tuple[float, float]]]:
    """Join ways at either endpoint, reversing as needed; sort components by length."""
    unused = []
    seen = set()
    for member in element.get("members", []):
        if member.get("type") != "way" or not member.get("geometry"):
            continue
        ref = member.get("ref")
        if ref is not None and ref in seen and member.get("role") in ("forward", "backward"):
            continue
        points = [(float(p["lat"]), float(p["lon"])) for p in member["geometry"]]
        if any(not (-90 <= lat <= 90 and -180 <= lon <= 180) for lat, lon in points):
            raise DataSourceError("OSM way contains invalid coordinates.")
        unused.append(points)
        if ref is not None:
            seen.add(ref)

    segments = []
    while unused:
        segment = unused.pop(0)
        while unused:
            for index, way in enumerate(unused):
                if _same_point(segment[-1], way[0]):
                    segment.extend(way[1:])
                elif _same_point(segment[-1], way[-1]):
                    segment.extend(way[-2::-1])
                elif _same_point(segment[0], way[-1]):
                    segment = way[:-1] + segment
                elif _same_point(segment[0], way[0]):
                    segment = way[:0:-1] + segment
                else:
                    continue
                unused.pop(index)
                break
            else:
                break
        segments.append(segment)
    return sorted(segments, key=polyline_length_km, reverse=True)


def _elevation(value: str | None) -> float | None:
    if value is None or not re.fullmatch(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*m?\s*", value):
        return None
    number = float(value.strip().removesuffix("m").strip())
    return number if math.isfinite(number) else None


def parse_element(element: dict, *, fetched_at: str) -> Route | None:
    """Keep the longest connected component of a named or referenced relation."""
    try:
        if element.get("type") != "relation":
            return None
        tags = dict(element.get("tags") or {})
        name = tags.get("name") or tags.get("ref")
        if not name:
            return None
        segments = stitch_members(element)
        if not segments or len(segments[0]) < 2:
            return None
        geometry = segments[0]
        tags["_discarded_segments"] = str(len(segments) - 1)
        start, end = geometry[0], geometry[-1]
        roundtrip = {"yes": True, "no": False}.get(tags.get("roundtrip"))
        if "roundtrip" not in tags and haversine_km(*start, *end) <= 0.1:
            roundtrip = True
        return Route(
            osm_id=int(element["id"]), name=name, ref=tags.get("ref"),
            network=tags.get("network"), length_km=polyline_length_km(geometry),
            ascent_m=_elevation(tags.get("ascent")), descent_m=_elevation(tags.get("descent")),
            roundtrip=roundtrip, start_lat=start[0], start_lon=start[1],
            end_lat=end[0], end_lon=end[1],
            bbox=BBox(min(p[0] for p in geometry), min(p[1] for p in geometry),
                      max(p[0] for p in geometry), max(p[1] for p in geometry)),
            geometry=geometry, tags=tags, fetched_at=fetched_at,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataSourceError(f"Invalid OSM relation: {exc}") from exc


def parse_response(payload: dict, *, fetched_at: str | None = None) -> list[Route]:
    """Parse usable relations, defaulting the fetch timestamp to the current UTC time."""
    try:
        _validate_payload(payload)
    except ValueError as exc:
        raise DataSourceError(str(exc)) from exc
    timestamp = fetched_at if fetched_at is not None else datetime.now(timezone.utc).isoformat()
    result = []
    for element in payload["elements"]:
        route = parse_element(element, fetched_at=timestamp)
        if route is not None:
            result.append(route)
    return result


def _route_values(route: Route) -> tuple:
    return (
        route.osm_id, route.name, route.ref, route.network, route.length_km,
        route.ascent_m, route.descent_m, route.roundtrip, route.start_lat, route.start_lon,
        route.end_lat, route.end_lon, route.bbox.min_lat, route.bbox.min_lon,
        route.bbox.max_lat, route.bbox.max_lon,
        json.dumps(route.geometry, allow_nan=False),
        json.dumps(route.tags, ensure_ascii=False, allow_nan=False), route.fetched_at,
    )


def store_routes(conn: sqlite3.Connection, routes: list[Route]) -> int:
    """Upsert a batch in one transaction, returning the number inserted or updated."""
    sql = (
        f"INSERT INTO routes ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _COLUMNS)}) "
        "ON CONFLICT(osm_id) DO UPDATE SET "
        + ", ".join(f"{column} = excluded.{column}" for column in _COLUMNS[1:])
    )
    try:
        with conn:
            conn.executemany(sql, (_route_values(route) for route in routes))
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise DatabaseError(f"Cannot store routes: {exc}") from exc
    return len(routes)


def load_routes(
    conn: sqlite3.Connection, *, bbox: BBox | None = None,
    max_length_km: float | None = None, min_length_km: float | None = None,
) -> list[Route]:
    """Load routes with inclusive length and bbox-overlap filters; no matches return []."""
    clauses, params = [], []
    if bbox is not None:
        clauses.append("bbox_min_lat <= ? AND bbox_max_lat >= ? AND bbox_min_lon <= ? AND bbox_max_lon >= ?")
        params.extend((bbox.max_lat, bbox.min_lat, bbox.max_lon, bbox.min_lon))
    if max_length_km is not None:
        clauses.append("length_km <= ?")
        params.append(max_length_km)
    if min_length_km is not None:
        clauses.append("length_km >= ?")
        params.append(min_length_km)
    sql = "SELECT " + ", ".join(_COLUMNS) + " FROM routes"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY osm_id"
    result = []
    try:
        for row in conn.execute(sql, params):
            values = dict(zip(_COLUMNS, row, strict=True))
            values["bbox"] = BBox(*(values.pop(c) for c in _COLUMNS[12:16]))
            values["geometry"] = [tuple(p) for p in json.loads(values.pop("geometry_json"))]
            values["tags"] = json.loads(values.pop("tags_json"))
            if values["roundtrip"] is not None:
                values["roundtrip"] = bool(values["roundtrip"])
            result.append(Route(**values))
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise DatabaseError(f"Cannot load routes: {exc}") from exc
    return result


def tile_bbox(bbox: BBox, step_deg: float = 0.25) -> list[BBox]:
    """Cover a region with a south-to-north grid, clipping the final row and column."""
    _validate_bbox(bbox)
    if not math.isfinite(step_deg) or step_deg <= 0:
        raise ValueError("Tile step must be finite and positive.")
    tiles = []
    for row in range(math.ceil((bbox.max_lat - bbox.min_lat) / step_deg)):
        south = bbox.min_lat + row * step_deg
        if south >= bbox.max_lat:
            continue
        for column in range(math.ceil((bbox.max_lon - bbox.min_lon) / step_deg)):
            west = bbox.min_lon + column * step_deg
            if west < bbox.max_lon:
                tiles.append(BBox(south, west, min(south + step_deg, bbox.max_lat),
                                  min(west + step_deg, bbox.max_lon)))
    return tiles
