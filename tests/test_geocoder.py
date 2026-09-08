"""Tests for src/geocoder.py.

Nominatim is never contacted: `requests.get` is monkeypatched. The rate-limit
test matters more than it looks — Nominatim's usage policy is one request per
second, and getting this project's User-Agent banned would break the demo for
everyone who clones it.
"""

from __future__ import annotations

import pytest
from conftest import require

geocoder = require("src.geocoder", "geocode")
errors = require("src.errors", "NotFoundError")


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def result(name, lat, lon, **address):
    return {
        "lat": str(lat),
        "lon": str(lon),
        "display_name": name,
        "address": address,
    }


class CallLog(list):
    """A list of captured requests that also carries the scripted payload."""

    payload: list = []


@pytest.fixture
def calls(monkeypatch):
    """Capture outgoing requests and serve a scripted payload."""
    recorded = CallLog()
    recorded.payload = []

    def fake_get(url, params=None, headers=None, timeout=None):
        recorded.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse(recorded.payload)

    monkeypatch.setattr(geocoder.requests, "get", fake_get)
    return recorded


def test_prefers_upper_bavaria_over_a_same_named_place(calls):
    """'Neuhaus' exists all over Germany. The Oberbayern one must win."""
    calls.payload = [
        result("Neuhaus, Thüringen", 50.51, 11.14, state="Thüringen"),
        result("Neuhaus, Oberbayern, Bayern", 47.70, 11.80,
               state="Bayern", state_district="Oberbayern"),
        result("Neuhaus, Niedersachsen", 53.80, 8.98, state="Niedersachsen"),
    ]
    lat, lon, name = geocoder.geocode("Neuhaus")
    assert (lat, lon) == pytest.approx((47.70, 11.80))
    assert "Oberbayern" in name


def test_prefers_bavaria_when_no_upper_bavaria_match(calls):
    calls.payload = [
        result("Ort, Sachsen", 51.0, 13.0, state="Sachsen"),
        result("Ort, Bayern", 49.0, 11.0, state="Bayern"),
    ]
    lat, _, _ = geocoder.geocode("Ort")
    assert lat == pytest.approx(49.0)


def test_returns_floats_not_strings(calls):
    calls.payload = [result("Schliersee", 47.7256, 11.8583, state="Bayern")]
    lat, lon, _ = geocoder.geocode("Schliersee")
    assert isinstance(lat, float) and isinstance(lon, float)


def test_empty_result_raises_not_found(calls):
    calls.payload = []
    with pytest.raises(errors.NotFoundError):
        geocoder.geocode("Nichtvorhandenerortsname")


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_rejected(query, calls):
    with pytest.raises((ValueError, errors.NotFoundError)):
        geocoder.geocode(query)


def test_sends_a_user_agent_and_a_timeout(calls):
    """Nominatim blocks requests without an identifying User-Agent, and a
    request without a timeout can hang the CLI forever."""
    calls.payload = [result("Schliersee", 47.72, 11.85, state="Bayern")]
    geocoder.geocode("Schliersee")
    assert calls[0]["headers"].get("User-Agent")
    assert calls[0]["timeout"] is not None


def test_restricts_search_to_germany(calls):
    calls.payload = [result("Schliersee", 47.72, 11.85, state="Bayern")]
    geocoder.geocode("Schliersee")
    assert calls[0]["params"].get("countrycodes") == "de"


def test_respects_the_one_request_per_second_policy(calls, monkeypatch):
    calls.payload = [result("Schliersee", 47.72, 11.85, state="Bayern")]
    slept = []
    monkeypatch.setattr(geocoder.time, "sleep", lambda s: slept.append(s))

    geocoder.geocode("Schliersee")
    geocoder.geocode("Tegernsee")

    assert slept, "a second immediate call must wait before hitting Nominatim"
    assert sum(slept) > 0


@pytest.mark.live
def test_live_geocode_schliersee():
    """Deselected in CI."""
    lat, lon, name = geocoder.geocode("Schliersee")
    assert 47.5 < lat < 48.0
    assert 11.5 < lon < 12.2
