# AI Collaboration Workflow

How this project gets built from here, with the smallest possible amount of
Solon's time in the loop.

---

## Roles

| Who | Owns | Does not do |
|---|---|---|
| **Claude** | Architecture, interface contracts, acceptance tests, code review, branch/PR management | Write the feature implementations |
| **GPT6** | Implementation against a brief until its acceptance tests pass; live smoke tests against the real APIs | Change interfaces, schema, or tests |
| **Solon** | Merge decisions, credentials, taste calls, anything with a licence or safety implication | Debugging by hand |

The division exists for one reason: **the tests are written before the
implementation, by a party that is not the implementer.** That is what makes the
handoff verifiable instead of a matter of trust. If GPT6 is stuck, the correct
move is to change the brief and the test — deliberately, in a commit — not to
quietly relax the test.

---

## The loop, per phase

1. **Claude** opens a branch `phaseN-<topic>` containing the brief
   (`docs/delegation/phaseN-*.md`) and the failing acceptance tests, and opens a
   draft PR.
2. **Solon** pastes the brief into GPT6 together with the hand-off prompt below.
3. **GPT6** implements, runs `pytest -m "not live"` until green, runs `ruff`,
   runs the live smoke test from the brief's §Definition of done, and returns
   the files plus the smoke-test output.
4. **Solon** drops the files into the working tree and pushes to the branch (or
   hands them to Claude to push).
5. **Claude** reviews the diff against the brief, files any findings, and marks
   the PR ready.
6. **Solon** merges. Claude updates `docs/ARCHITECTURE.md` if anything changed,
   and opens the next phase's branch.

Steps 1, 5 and 7 are the ones that used to eat the time. They are now the
automated ones.

---

## Hand-off prompt for GPT6

Paste this verbatim above the brief.

> You are implementing one phase of an existing Python project. The complete
> interface contract is in `docs/ARCHITECTURE.md` and the task is in the brief
> below. Constraints, in priority order:
>
> 1. **Do not change any public function signature, dataclass field, SQL column,
>    or test file.** They are the contract with the rest of the codebase. If you
>    believe one is wrong, say so in your reply and stop — do not work around it.
> 2. The acceptance tests already exist and currently fail. Your job is done when
>    `pytest -m "not live"` is green and `ruff check src/ main.py` is clean.
>    Do not edit the tests to make them pass.
> 3. No new runtime dependencies beyond those named in the brief.
> 4. Library modules must not `print()` or `sys.exit()`. Raise the project's
>    exception classes; only `main.py` produces output.
> 5. Every public function gets a type-hinted signature and a docstring.
>    Identifiers and commit messages in English; user-facing CLI strings may be
>    Chinese, matching the existing code.
> 6. Return complete files, not diffs or fragments.
>
> After the tests pass, run the live smoke test in the brief's "Definition of
> done" section and paste its real output. If you did not actually run it, say
> so — an invented smoke test output is worse than none.

That last sentence matters more than it looks. Fabricated verification is the
main failure mode of this kind of delegation.

---

## Review checklist (Claude, step 5)

- Signatures and schema match `docs/ARCHITECTURE.md` exactly.
- No `print`/`sys.exit` below `main.py`.
- Every `except` either handles or re-raises as a project exception; no bare
  `except:`, no silent `pass`.
- Network calls have a timeout, and rate limits are respected (Nominatim 1 rps,
  Overpass 2 s between tiles).
- No unbounded memory growth on the GTFS path.
- Tests were not modified. `git diff --stat origin/main -- tests/` is empty
  unless the brief said otherwise.
- Attribution obligations satisfied for any data source touched.

---

## Guardrails worth keeping

- **`main` is protected**: everything lands through a PR, even one-line fixes.
  Branch history is the artefact a reviewer reads.
- **One phase per branch.** A PR that touches both `transit.py` and the scoring
  weights cannot be reviewed properly.
- **The contract document changes first.** If a phase reveals that the interface
  was wrong, the fix is a commit to `docs/ARCHITECTURE.md` plus the test update,
  then the implementation — in that order, visibly.
- **Live tests never run in CI.** They are marked `@pytest.mark.live` and
  deselected by default. CI that depends on a donated public API endpoint is CI
  that goes red for reasons that have nothing to do with the code.
