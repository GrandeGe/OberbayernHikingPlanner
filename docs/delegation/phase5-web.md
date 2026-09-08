# Delegation Brief — Phase 5: Flask + Leaflet Web Interface

**Assignee:** GPT6
**Prerequisite:** Phase 4 merged.
**Deliverables:** `src/web/__init__.py`, `src/web/routes.py`,
`src/web/templates/index.html`, `src/web/static/app.js`, `src/web/static/style.css`,
the `serve` subcommand.
**Acceptance gate:** `pytest tests/test_web.py` passes (Flask test client, no
browser, no network).

---

## 1. Goal

The same recommendation engine, on a map. A visitor to the GitHub repo should be
able to look at one screenshot and understand the whole project.

---

## 2. Non-negotiable constraints

- **The web layer is read-only over the local database.** It calls
  `recommend()` and `load_routes()`. It never calls Overpass or downloads GTFS.
  The only outbound call is Bright Sky, via the existing `weather` module.
- **App factory pattern**: `create_app(db_path=None, testing=False) -> Flask`.
  No module-level `app = Flask(__name__)` — that makes the app untestable and is
  the reason most student Flask projects have no tests.
- One SQLite connection per request, opened in `before_request`, closed in
  `teardown_appcontext`. SQLite connections are not thread-safe across requests.
- No build step. No npm, no bundler. Leaflet from CDN with SRI, everything else
  hand-written. This project's value is the data pipeline; a toolchain here adds
  risk and nothing else.

---

## 3. HTTP API

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/` | — | the single page |
| GET | `/api/recommend` | `location`, `hours`, `date?`, `top?`, `max_walk_km?` | `{"query": {...}, "results": [...]}` |
| GET | `/api/route/<int:osm_id>` | — | one route incl. full `geometry` |
| GET | `/api/health` | — | `{"status": "ok", "routes": N, "stops": N, "schema_version": 1}` |

`/api/recommend` result objects mirror `ScoredRoute`, with `geometry` **omitted**
(a 3000-point polyline per result × 5 results is a slow response). The client
fetches geometry lazily from `/api/route/<osm_id>` when a result is clicked.

Error responses: `{"error": "<message>", "type": "<ExceptionClassName>"}` with
status 400 for bad input, 404 for `NotFoundError`, 502 for `DataSourceError`,
503 for `DatabaseError`. Never return a 200 with an error body.

Validate `hours` as a float in `(0, 24]` and `top` as an int in `[1, 20]`
**before** touching the database, and return 400 on failure. Unvalidated query
parameters going into a scoring loop is how a demo page becomes a denial of
service.

---

## 4. Front end

Single page, three regions:

- **Left panel (360 px):** location input, hours slider (1–12), date picker,
  "查找路线" button, then the result cards. Each card mirrors the CLI block —
  score, length, ascent, duration, transit stop, weather line.
- **Map (fills the rest):** Leaflet, OpenStreetMap tiles, initial view centred
  on Munich (48.137, 11.575) at zoom 9.
- **Attribution bar:** OSM (ODbL), DWD/Bright Sky, and MVV (CC-BY 4.0, with
  retrieval date). All three are legal obligations, not decoration.

Behaviour:
- Submitting the form calls `/api/recommend`, renders the cards, and drops a
  numbered marker at each route's start point.
- Hovering a card highlights its marker; clicking a card fetches the geometry,
  draws the polyline, and fits the map to its bounds.
- Loading and error states are visible in the panel — never a silent no-op.
- Responsive down to 380 px: below 720 px the panel stacks above the map.
- Respect `prefers-color-scheme`.

Use `fetch` and plain DOM APIs. No framework.

---

## 5. `serve` subcommand

```
python main.py serve [--host 127.0.0.1] [--port 5000] [--db PATH] [--debug]
```

Default host is `127.0.0.1`, not `0.0.0.0`. Binding a debug-mode Flask server to
all interfaces exposes the Werkzeug debugger console to the local network, which
is remote code execution. If a deployment needs `0.0.0.0`, that is Phase 6's
gunicorn config, not this.

---

## 6. Tests (`tests/test_web.py`)

Unlike phases 2–4, this file does not exist yet: it is written and committed to
the `phase5-web` branch when that branch opens, because its shape depends on
what phases 3 and 4 actually landed. The cases below are fixed in advance.

Using a temporary DB seeded from the same fixtures as `test_recommend.py`:

- `/api/health` returns 200 with the expected counts.
- `/api/recommend` with valid params returns 200 and a well-formed body;
  `weather.fetch_weather` is monkeypatched, so no network.
- `hours=0`, `hours=abc`, `hours=99`, `top=0`, `top=100` each return 400.
- `/api/route/999999999` returns 404.
- A recommend response contains no `geometry` key.
- `/api/route/<id>` for a seeded route returns a geometry with ≥ 2 points.

---

## 7. Out of scope

- User accounts, saved routes, any writes to the database.
- Server-side rendering of results (the API + JS split is deliberate).
- Deployment, Docker, gunicorn — that is Phase 6.

---

## 8. Definition of done

1. `pytest tests/test_web.py` passes.
2. `python main.py serve` then a manual check: search Schliersee / 6 hours,
   click a result, confirm the polyline draws and the map fits it.
3. A screenshot at `docs/screenshot.png`, referenced from `README.md`.
   This is the single highest-leverage artefact in the repository for anyone
   skimming it.
