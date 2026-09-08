"""Shared pytest fixtures.

Every fixture here is offline. No test in this suite may touch the network
unless it is marked ``@pytest.mark.live``, which CI deselects.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Reference values computed independently of the implementation.
# See docs/delegation/phase2-routes.md §4.
MAIN_POLYLINE_KM = 2.680491
SEGMENT_LONG_KM = 5.363866
SEGMENT_SHORT_KM = 0.133792
LOOP_POLYLINE_KM = 3.200449


def require(module_name: str, *attrs: str):
    """Import a project module, skipping the whole test file if the phase
    that implements it has not landed yet.

    ``src/routes.py`` and friends already exist as empty placeholder files, so
    a plain ``importorskip`` would not skip. We check for the attributes the
    contract requires instead.
    """
    module = pytest.importorskip(module_name)
    missing = [a for a in attrs if not hasattr(module, a)]
    if missing:
        pytest.skip(
            f"{module_name} does not yet provide {', '.join(missing)} "
            "— phase not implemented",
            allow_module_level=True,
        )
    return module


@pytest.fixture(scope="session")
def overpass_payload() -> dict:
    """Raw Overpass `out geom` response covering every parsing edge case."""
    with (FIXTURE_DIR / "overpass_sample.json").open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def gtfs_zip(tmp_path_factory) -> Path:
    """Zip the loose GTFS fixture files into a real .zip.

    They are kept loose in the repository so they stay diffable; the code under
    test expects a zip, so we build one per session.
    """
    src = FIXTURE_DIR / "gtfs_mini"
    dest = tmp_path_factory.mktemp("gtfs") / "mini_gtfs.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for txt in sorted(src.glob("*.txt")):
            zf.write(txt, arcname=txt.name)
    return dest


@pytest.fixture
def db_conn(tmp_path):
    """A connected, initialised, empty database."""
    db = require("src.db", "connect", "init_db")
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_summary() -> dict:
    """A `weather.summarize_day()` dict describing a good hiking day."""
    return {
        "temp_min": 11.0,
        "temp_max": 19.0,
        "total_rain": 0.0,
        "max_wind": 10.0,
        "dominant_condition": "dry",
        "avg_cloud_cover": 20.0,
        "record_count": 24,
    }
