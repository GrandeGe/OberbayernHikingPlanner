# Delegation Brief — Phase 6: Documentation, Packaging, Deployment

**Assignee:** GPT6 (documentation drafting), Solon (final review, deployment
credentials)
**Prerequisite:** Phase 5 merged.

This phase decides what a recruiter or an admissions reviewer concludes in the
forty seconds they spend on the repository. Treat it as engineering work, not
paperwork.

---

## 1. README rewrite

Current README is a Phase-0 stub with two defects: it tells the reader to
`cd munich-hiking-planner` (wrong repository name) and its status checkboxes are
all unchecked despite Phase 1 being complete. Replace it entirely.

Required structure:

1. **One-sentence description** and the screenshot from Phase 5, above the fold.
2. **The problem** — two sentences. Existing tools give city-level forecasts for
   mountain valleys, and hiking discovery is decoupled from transit planning.
3. **Quick start** — verified copy-pasteable commands, in order:
   `venv` → `pip install -r requirements.txt` → `routes build` →
   `transit build --download` → `recommend` → `serve`. Include the expected
   runtime of the two build steps (they are minutes, not seconds — say so).
4. **How it works** — the architecture diagram from `docs/ARCHITECTURE.md`, plus
   a paragraph on the scoring model with the DIN 33466 citation. This is the
   section that demonstrates engineering judgement rather than API plumbing.
5. **Data sources & licences** — a table: Bright Sky/DWD, Nominatim, Overpass,
   MVV GTFS, with licence and required attribution for each.
6. **Limitations** — stated plainly and in the reader's favour:
   straight-line stop distance rather than walking-network routing; OSM `ascent`
   tags are sparse; the longest connected segment is used when a relation has
   gaps; no journey-time computation from the user's actual origin. A README
   that names its own limitations reads as more competent than one that does not.
7. **Roadmap** and **Licence**.

Write it in English. Keep the CLI's Chinese output as-is — a bilingual project is
an accurate reflection of who built it and where.

---

## 2. Packaging

- `pyproject.toml` with `[project]` metadata, `requires-python = ">=3.10"`,
  runtime deps (`requests`, `flask`) and a `[project.optional-dependencies] dev`
  group (`pytest`, `pytest-cov`, `ruff`).
- `[project.scripts] oberbayern-hiking = "main:main"` so the tool installs as a
  real command.
- Split `requirements.txt` into `requirements.txt` (runtime, uncommented — the
  current file has Phase 2/3 deps commented out) and `requirements-dev.txt`.
- Add `LICENSE`. **MIT**, unless you want otherwise. Note in the README that the
  *code* is MIT while the *data* carries ODbL and CC-BY obligations; those are
  separate and the distinction is worth showing you understand.

---

## 3. CI

`.github/workflows/ci.yml` — already added in the scaffolding commit. Extend it
to a matrix over Python 3.10/3.11/3.12 once the suite is green, and add a
coverage badge. CI must remain fully offline; any test that needs the network is
marked `@pytest.mark.live` and deselected by default (`-m "not live"`).

---

## 4. Deployment (optional but high value)

The bottleneck is the database: `hiking.db` after both build steps is on the
order of 50–200 MB, too large for a free tier's ephemeral disk to rebuild on
every cold start.

Two workable options:

- **A — Static demo (recommended).** Deploy the Flask app to Fly.io or Render
  with a *pruned* database: routes within 60 km of Munich only, geometry
  simplified with Ramer–Douglas–Peucker at ~20 m tolerance. Target under 25 MB,
  committed via Git LFS or fetched from a GitHub Release on boot. Add
  `main.py db prune --radius-km 60 --simplify 20` to produce it reproducibly.
- **B — No deployment.** A screenshot plus a 30-second screen recording in the
  README. Cheaper, zero maintenance, and honestly most readers never click a
  demo link.

Pick B if time is short. Do not leave a broken demo link in the README — a dead
link costs more credibility than no link.

---

## 5. Definition of done

1. A reader who has never seen the project can go from `git clone` to a working
   `recommend` output using only the README, on a clean machine.
2. `pytest` green in CI on a clean checkout.
3. All four data-source attributions present.
4. No dead links, no wrong repository names, no unchecked boxes for completed
   phases.
