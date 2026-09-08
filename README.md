# 🥾 OberbayernHikingPlanner

A command-line tool that recommends hiking trails near Munich
based on weather forecasts, public transit accessibility,
and your available time.

## Features (Planned)

- **Weather Integration**: Real-time forecasts via DWD/Bright Sky API
- **Trail Database**: Hiking routes from OpenStreetMap
- **Transit Scoring**: MVV public transport travel times to trailheads
- **Smart Recommendations**: "I have 6 hours on Saturday" → Top 5 trails

## Tech Stack

- Python 3.10+
- Bright Sky API (weather)
- Overpass API (hiking trails)
- MVV GTFS (public transit)
- SQLite (local database)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/GrandeGe/OberbayernHikingPlanner.git
cd OberbayernHikingPlanner

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
.\venv\Scripts\Activate   # Windows

# Install dependencies
pip install -e ".[dev]"

# Run
python main.py weather --location "Schliersee"
```

## Development

Interface contract: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
Phase briefs: [`docs/delegation/`](docs/delegation/) ·
Workflow: [`docs/AI_WORKFLOW.md`](docs/AI_WORKFLOW.md)

```bash
pytest              # offline suite; live API tests are deselected by default
pytest -m live      # hits Overpass / Bright Sky / MVV — run manually
ruff check src/ main.py tests/
```

## Project Status

🚧 Under active development

- [x] Phase 0: Project setup
- [x] Phase 1: Weather module (Bright Sky forecast + Nominatim geocoding)
- [ ] Phase 2: Trail database
- [ ] Phase 3: Transit accessibility
- [ ] Phase 4: Recommendation engine
- [ ] Phase 5: Web interface
- [ ] Phase 6: Documentation & deployment

## Data sources & attribution

| Source | Used for | Licence / required attribution |
|---|---|---|
| [Bright Sky](https://brightsky.dev) / DWD | Hourly forecasts | Data by Deutscher Wetterdienst |
| [Nominatim](https://nominatim.openstreetmap.org) | Geocoding | © OpenStreetMap contributors (ODbL) |
| [Overpass API](https://overpass-api.de) | Hiking route geometry | © OpenStreetMap contributors (ODbL) |
| [MVV GTFS](https://opendata.muenchen.de/dataset/2a1058ed-ff65-4142-b4f0-b779facf504d) | Transit stops & frequency | CC-BY 4.0 — Münchner Verkehrs- und Tarifverbund GmbH (MVV) |
