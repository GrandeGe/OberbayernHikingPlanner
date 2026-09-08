"""Tests for src/weather.py.

Phase 1 shipped without any tests. These lock in the behaviour that phases 3
and 4 depend on, and enforce the refactor required by
docs/delegation/phase2-routes.md §6: no printing below main.py, and network
failures surfacing as DataSourceError.
"""

from __future__ import annotations

import json

import pytest
import requests
from conftest import FIXTURE_DIR, require

weather = require("src.weather", "group_by_day", "summarize_day", "format_forecast")
errors = require("src.errors", "DataSourceError")


@pytest.fixture(scope="module")
def records() -> list[dict]:
    with (FIXTURE_DIR / "brightsky_sample.json").open(encoding="utf-8") as fh:
        return json.load(fh)["weather"]


# ──────────────────────────── grouping ─────────────────────────────────


def test_group_by_day_splits_on_calendar_date(records):
    grouped = weather.group_by_day(records)
    assert set(grouped) == {"2026-09-12", "2026-09-13"}
    assert len(grouped["2026-09-12"]) == 3
    assert len(grouped["2026-09-13"]) == 1


def test_group_by_day_of_empty_input(records):
    assert weather.group_by_day([]) == {}


# ──────────────────────────── summarising ──────────────────────────────


def test_summarize_day_reference_values(records):
    day = weather.group_by_day(records)["2026-09-12"]
    summary = weather.summarize_day(day)
    assert summary["temp_min"] == pytest.approx(10.0)
    assert summary["temp_max"] == pytest.approx(20.0)
    assert summary["total_rain"] == pytest.approx(0.5)
    assert summary["max_wind"] == pytest.approx(12.0)
    assert summary["avg_cloud_cover"] == pytest.approx(50.0)
    assert summary["dominant_condition"] == "dry"
    assert summary["record_count"] == 3


def test_summarize_day_ignores_nulls_rather_than_treating_them_as_zero(records):
    """The 22:00 record has precipitation=null. Counting it as 0.0 would be
    harmless here but wrong for wind, where null must not become max_wind=0."""
    day = weather.group_by_day(records)["2026-09-12"]
    assert weather.summarize_day(day)["max_wind"] == pytest.approx(12.0)


def test_summarize_day_with_all_null_fields(records):
    """A forecast horizon edge can return records with every field null.
    summarize_day must return the dict, not raise."""
    day = weather.group_by_day(records)["2026-09-13"]
    summary = weather.summarize_day(day)
    assert summary["temp_min"] is None
    assert summary["max_wind"] is None
    assert summary["avg_cloud_cover"] is None
    assert summary["record_count"] == 1


def test_summarize_day_of_empty_list():
    summary = weather.summarize_day([])
    assert summary["record_count"] == 0
    assert summary["temp_min"] is None


# ──────────────────────────── formatting ───────────────────────────────


def test_format_forecast_returns_a_string(records, capsys):
    """Library modules must not print. main.py owns all output."""
    text = weather.format_forecast(47.72, 11.86, location_name="Schliersee", records=records)
    assert isinstance(text, str) and text
    assert "Schliersee" in text
    assert capsys.readouterr().out == "", "format_forecast must not print"


def test_format_forecast_survives_missing_values(records):
    """The 2026-09-13 record is entirely null. The original print_forecast
    crashed here on `f\"{None:.0f}\"`."""
    text = weather.format_forecast(47.72, 11.86, records=records)
    assert "2026" in text or "13.09" in text


def test_format_forecast_daytime_filter(records):
    """22:00 is outside the 06:00–20:00 daytime window."""
    daytime = weather.format_forecast(47.72, 11.86, records=records, daytime_only=True)
    all_hours = weather.format_forecast(47.72, 11.86, records=records, daytime_only=False)
    assert len(all_hours) > len(daytime)
    assert "22:00" in all_hours
    assert "22:00" not in daytime


# ──────────────────────────── error handling ───────────────────────────


def test_network_failure_becomes_a_project_error(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(weather.requests, "get", boom)
    with pytest.raises(errors.DataSourceError):
        weather.fetch_weather(47.72, 11.86)


def test_unexpected_payload_shape_is_reported(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"remark": "query timed out"}

    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: FakeResponse())
    with pytest.raises((errors.DataSourceError, ValueError)):
        weather.fetch_weather(47.72, 11.86)


@pytest.mark.live
def test_live_brightsky_forecast():
    """Deselected in CI. Confirms the Bright Sky contract has not changed."""
    records = weather.fetch_weather(47.7256, 11.8583, days=2)
    assert len(records) > 24
    assert "temperature" in records[0] and "timestamp" in records[0]
