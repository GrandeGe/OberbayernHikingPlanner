# Delegation Brief — Phase 2: OSM Route Database

**Assignee:** GPT6
**Deliverables:** `src/errors.py`, `src/models.py`, `src/db.py`, `src/routes.py`,
plus the `routes` subcommands in `main.py`, plus the Phase-1 refactor listed in §6.
**Acceptance gate:** `pytest tests/test_models.py tests/test_db.py tests/test_routes.py`
passes with zero failures and zero network access.

Read `docs/ARCHITECTURE.md` first. It defines the dataclasses, the SQL schema,
and the error classes. Do not redesign them.

---

## 1. Goal

Populate a local SQLite database with every hiking route relation in Upper
Bavaria from OpenStreetMap, with geometry, computed length, and endpoints, so
that later phases can query it offline and instantly.

---

## 2. The Overpass query

`build_overpass_query(bbox, networks)` returns exactly this shape (whitespace
may differ; the test normalises it):

```
[out:json][timeout:180];
(
  relation["type"="route"]["route"="hiking"]["network"~"^(iwn|nwn|rwn|lwn)$"]
    (47.27,10.75,48.55,13.10);
);
out geom;
```

- The bbox order in Overpass is `(south, west, north, east)` =
  `(min_lat, min_lon, max_lat, max_lon)`. Getting this backwards silently
  returns zero results — it is the single most likely bug in this phase.
- `networks` is interpolated into the regex alternation, in the order given.
- Coordinates are formatted to 5 decimal places.
- `out geom;` is required — `out tags;` gives no coordinates and `out body;`
  gives member IDs you would then have to resolve in a second request.

### Fetching at scale

A single `out geom` query over all of Upper Bavaria will return on the order of
100 MB and will very likely be rejected by the public endpoint (429 / 504).
Therefore:

- `tile_bbox(bbox, step_deg=0.25)` splits the region into a grid of sub-boxes.
  Tiles must cover the whole bbox; the last row/column may be narrower. For
  `OBERBAYERN_BBOX` at 0.25° this is 6 rows × 10 columns = 60 tiles.
- `main.py routes build` iterates tiles, calling `fetch_raw` per tile.
- **Sleep 2 seconds between tile requests.** The public Overpass instance is a
  donated resource; hammering it gets the IP banned.
- Cache each tile's raw JSON to `data/cache/overpass_<minlat>_<minlon>.json` and
  skip the request if the cache file exists and is younger than 30 days. Make
  the cache directory configurable and add `data/cache/` to `.gitignore`.
- A relation straddling two tiles is returned by both. `store_routes` must
  upsert on `osm_id` (`INSERT ... ON CONFLICT(osm_id) DO UPDATE`), so
  duplicates collapse rather than crash.
- Retry policy: on HTTP 429 or 504, sleep 30 s and retry, at most 3 times, then
  raise `DataSourceError` naming the tile. Do not retry 400 (bad query — that is
  our bug).

---

## 3. Parsing: `stitch_members` and `parse_element`

With `out geom`, each relation element looks like:

```json
{
  "type": "relation",
  "id": 123456,
  "bounds": {"minlat": 47.6, "minlon": 11.8, "maxlat": 47.7, "maxlon": 11.9},
  "members": [
    {"type": "way", "ref": 111, "role": "",
     "geometry": [{"lat": 47.60, "lon": 11.80}, {"lat": 47.61, "lon": 11.81}]},
    {"type": "node", "ref": 222, "role": "guidepost", "lat": 47.62, "lon": 11.82}
  ],
  "tags": {"type": "route", "route": "hiking", "name": "Wanderweg X", "network": "lwn"}
}
```

`stitch_members(element)` must:

1. Keep only members with `"type": "way"` **and** a non-empty `"geometry"`.
   Ignore node members entirely (guideposts, viewpoints — not part of the path).
   Ignore members whose `role` is `"forward"`/`"backward"` duplicates only if
   they duplicate an already-consumed way id; otherwise keep them.
2. Convert each way's geometry to `[(lat, lon), ...]`.
3. Stitch ways into connected segments. Greedy algorithm is fine:
   - Start a segment with the first unused way.
   - Repeatedly look for an unused way whose first or last point equals
     (within `1e-7` degrees) the current segment's last point; append it,
     reversing it if it matched on its last point.
   - Also try to extend backwards from the segment's first point.
   - When nothing connects, close the segment and start a new one with the next
     unused way.
4. Return the list of segments, **sorted by length descending**.
   `stitch_members` returns `[]` if there are no usable way members.

Points that are exactly equal at a join must not be duplicated in the output
(append the joining way's points from index 1).

`parse_element(element, fetched_at)` then:

- Returns `None` (a skip, not an error) when:
  - `stitch_members` returned `[]`, or
  - the longest segment has fewer than 2 points, or
  - `tags.get("name")` is missing **and** `tags.get("ref")` is missing — an
    unnamed, unreferenced route is not presentable to a user.
    If `name` is missing but `ref` exists, use `ref` as the name.
- `geometry` = the **longest** segment only. Multi-segment relations are common
  (variants, gaps in the data); recording the longest connected component is an
  honest simplification. Record the number of discarded segments in
  `tags["_discarded_segments"]` as a decimal string. **This key is always
  present**, `"0"` included — a key that only appears sometimes is a key every
  consumer has to guard against.
- `length_km` = `polyline_length_km(geometry)`. **Do not use the OSM `distance`
  tag** — it is free-text (`"12,5"`, `"12.5 km"`, `"ca. 12"`), frequently absent,
  and frequently wrong. Compute it.
- `ascent_m` / `descent_m` = parsed from tags `ascent` / `descent` if they are a
  plain number (optionally with `m` suffix); otherwise `None`. Do not invent
  elevation data in this phase.
- `roundtrip` = `True` if `tags.get("roundtrip") == "yes"`, `False` if `"no"`,
  else `None`. Also treat `start == end` (within 100 m) as `True` when the tag
  is absent.
- `start_*` / `end_*` = first and last point of `geometry`.
- `bbox` = computed from `geometry`, **not** from `element["bounds"]`
  (bounds covers discarded segments too).
- `tags` = the raw tag dict, plus the `_discarded_segments` key.
- `fetched_at` = the passed-in ISO-8601 UTC string.

`parse_response(payload, fetched_at=None)` maps `parse_element` over
`payload["elements"]`, drops `None`s, defaults `fetched_at` to
`datetime.now(timezone.utc).isoformat()`, and raises `DataSourceError` if
`"elements"` is absent from the payload.

---

## 4. Geometry maths

```python
EARTH_RADIUS_KM = 6371.0088

def haversine_km(lat1, lon1, lat2, lon2) -> float
```

Standard haversine. Must be exact enough that the tests' reference values match
to 3 decimal places.

```python
def polyline_length_km(points) -> float
```

Sum of haversine over consecutive pairs. Returns `0.0` for fewer than 2 points
(not an error — an empty polyline has zero length).

---

## 5. Persistence

`store_routes(conn, routes) -> int` returns the number of rows written
(inserted + updated). One transaction for the whole batch — 3000 individual
commits is the difference between 1 second and 3 minutes.

`load_routes(conn, *, bbox=None, max_length_km=None, min_length_km=None)`
returns `list[Route]`, reconstructing `geometry` and `tags` from JSON.
Returns `[]` when nothing matches — this is **not** a `NotFoundError`.
The `bbox` filter is a bbox *overlap* test (route bbox intersects the query
bbox), not containment.

---

## 6. Required Phase-1 refactor (do this in the same PR)

- Create `src/errors.py` with the four exception classes from
  `docs/ARCHITECTURE.md` §2.
- `weather.fetch_weather`: wrap `requests` failures in `DataSourceError`.
- `weather.print_forecast` → rename to `format_forecast(...) -> str`,
  returning the text instead of printing. `main.py` prints the result.
  `summarize_day` with all-`None` values must not crash the formatter — print
  `—` for missing figures.
- `geocoder.geocode`: raise `NotFoundError` on empty results; enforce a 1 s
  minimum interval between Nominatim calls with a module-level timestamp and
  `time.sleep` (import the `time` module, not `from time import sleep` — the
  test monkeypatches `geocoder.time.sleep`). Raise `ValueError` on a blank query.

`tests/test_weather.py` and `tests/test_geocoder.py` already encode all of the
above and currently fail. They are part of this phase's acceptance gate.
- Delete the stray zero-byte file `2.31.0` from the repository root.

---

## 7. CLI additions

```
python main.py routes build [--bbox MINLAT,MINLON,MAXLAT,MAXLON] [--step 0.25]
                            [--db PATH] [--no-cache]
python main.py routes list  [--max-km N] [--min-km N] [--limit 20] [--db PATH]
```

`build` prints tile-by-tile progress (`[12/60] 47.77,11.50 → 143 routes`) and a
final summary. `list` prints a table: name, network, length, start coordinates.

---

## 8. Explicitly out of scope for this phase

- Elevation enrichment from any external DEM/elevation API.
- Anything to do with transit, GTFS, or stops.
- Scoring, ranking, recommendation.
- Any change to the `weather` subcommand's user-visible behaviour beyond §6.

---

## 9. Definition of done

1. `pytest tests/test_models.py tests/test_db.py tests/test_routes.py
   tests/test_weather.py tests/test_geocoder.py` — all pass, **0 skipped**
   (a skip means the module still lacks a contract function and the
   `require()` guard in `conftest.py` disabled the file).
2. `ruff check src/ main.py` — clean.
3. Manual live smoke test (run locally, network required, paste the output back):
   ```
   python main.py routes build --bbox 47.60,11.60,47.85,11.90
   python main.py routes list --max-km 15 --limit 10
   ```
   Expect: a few hundred routes stored, the listing showing plausible Bavarian
   route names and lengths between 1 and 15 km.
4. Commit messages in English, one commit per logical unit.
