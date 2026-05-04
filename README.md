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
cd munich-hiking-planner

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
.\venv\Scripts\Activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

## Project Status

🚧 Under active development

- [x] Phase 0: Project setup
- [ ] Phase 1: Weather module
- [ ] Phase 2: Trail database
- [ ] Phase 3: Transit accessibility
- [ ] Phase 4: Recommendation engine
- [ ] Phase 5: Web interface