"""Geocoding helpers backed by the OpenStreetMap Nominatim API."""

from __future__ import annotations

from typing import Any

import requests


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "OberbayernHikingPlanner/1.0"


def geocode(query: str) -> tuple[float, float, str]:
    """
    把地名转换为经纬度。
    返回 (lat, lon, display_name)
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Geocoding query must not be empty.")

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
    if not results:
        raise ValueError(f"No geocoding result found for {query!r}.")

    best_result = max(results, key=_bavaria_priority_score)
    return (
        float(best_result["lat"]),
        float(best_result["lon"]),
        best_result["display_name"],
    )


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