"""Geocoding helpers backed by the OpenStreetMap Nominatim API."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

import requests

from src.errors import DataSourceError, NotFoundError

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "OberbayernHikingPlanner/1.0"
_last_request_at: float | None = None
_request_lock = Lock()


def geocode(query: str) -> tuple[float, float, str]:
    """
    把地名转换为经纬度。
    返回 (lat, lon, display_name)

    空白查询抛 ValueError；无结果抛 NotFoundError；上游错误抛 DataSourceError。
    """
    global _last_request_at

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Geocoding query must not be empty.")

    try:
        with _request_lock:
            if _last_request_at is not None:
                remaining = 1.0 - (time.monotonic() - _last_request_at)
                if remaining > 0:
                    time.sleep(remaining)
            _last_request_at = time.monotonic()
            response = requests.get(
                NOMINATIM_SEARCH_URL,
                params={
                    "q": normalized_query,
                    "format": "json",
                    "limit": 5,
                    "countrycodes": "de",
                    "addressdetails": 1,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list):
            raise ValueError("Expected a list of geocoding results.")
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"Nominatim request failed: {exc}") from exc

    if not results:
        raise NotFoundError(f"No geocoding result found for {query!r}.")

    try:
        best_result = max(results, key=_bavaria_priority_score)
        return (
            float(best_result["lat"]),
            float(best_result["lon"]),
            best_result["display_name"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataSourceError(f"Invalid Nominatim result: {exc}") from exc


def _bavaria_priority_score(result: dict[str, Any]) -> int:
    """Prefer Nominatim results located in Bavaria, especially Upper Bavaria."""
    address = result.get("address") or {}
    searchable_parts = [
        result.get("display_name", ""),
        address.get("state", ""),
        address.get("state_district", ""),
        address.get("region", ""),
        address.get("county", ""),
    ]
    searchable_text = " ".join(str(part).lower() for part in searchable_parts)

    score = 0
    if "bayern" in searchable_text or "bavaria" in searchable_text:
        score += 10
    if "oberbayern" in searchable_text or "upper bavaria" in searchable_text:
        score += 20
    return score
