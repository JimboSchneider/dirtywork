# Local Dashboard Implementation Plan

**Goal:** Ship the approved themed dashboard using local dirtywork evidence.

**Architecture:** A stdlib HTTP server exposes normalized run snapshots; packaged browser assets render and poll those snapshots. No runner or transcript-writing changes.

**Tech Stack:** Python >=3.9 stdlib, browser-native HTML/CSS/JavaScript.

**Spec:** docs/superpowers/specs/2026-10-18-dashboard-design.md

## Global Constraints

- Runtime stays stdlib-only, Python >=3.9.
- Read-only loopback service; no mutation of run evidence.
- Unknown metrics remain unknown; recorded review outcomes stay separate from worker statuses.

## Execution (in this session)

- [x] Add tests for normalized metadata/transcript data, accurate outcomes, partial append recovery, inactive records and invalid paths. Run to confirm the missing module fails.
- [x] Implement `dirtywork/dashboard_data.py`: cached transcript reader, summaries, details and aggregates. Run the data tests.
- [x] Add HTTP tests for asset delivery, API refresh, foreign hosts/origins, symlinks and route traversal. Implement `dirtywork/dashboard.py` and CLI dispatch. Run those tests.
- [x] Build packaged `dirtywork/dashboard_assets/{index.html,style.css,app.js}` with activity, outcomes, selectable runs, persistent themes/appearance, loading/empty/error states and polling. Test with the real local service.
- [x] Update package-data, README, operating guide and machine contract. Verify wheel contains assets and browser interactions work. Independent review findings fixed with regressions; final unit suite verification recorded below.

## Verification record

- Initial full suite: 2399 passed, 9 skipped, 38 deselected.
- Final full suite: 2405 passed, 9 skipped, 38 deselected (110.30 seconds).
- Final dashboard and contract checks: 66 passed, including overflow, deeply nested JSON and stable rolling activity identity regressions.
- Wheel smoke test: importing the built wheel directly served all three assets and the JSON API successfully.
- Browser checks: actual 480-run history, search, run selection, themes and appearance persisted across reload, narrow layout without horizontal overflow, automatic fixture completion/verdict/token updates, and preserved expanded result/focus as the 60-event tail advanced.
- No existing run records were changed. No model run was necessary; runner/model-facing behavior is unchanged.
