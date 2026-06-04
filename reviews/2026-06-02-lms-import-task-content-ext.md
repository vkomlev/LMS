# TechLead review: LMS import task_content_json extensions

Date: 2026-06-02

## Review Mode

`paranoid`

## Execution Posture

`report-only`

## Decision

`PASS` for integration to `main`.

The implemented microstep is correct, the repository pytest baseline is green,
and the required reproducible `mypy` contour is green.

## Blocking Findings

None.

## Non-Blocking Findings

### S3: unrelated dirty drift must be excluded from integration

Paths: LMS and ContentBackbone working trees.

Both repositories contain pre-existing unrelated edits and untracked files.
Stage only the files listed in `reviews/2026-06-02-lms-import-task-content-ext.diff`.

### S3: historical encoding warnings remain outside this patch

Paths: `docs/openapi.json`, ContentBackbone cross-project docs, older reviews.

New LMS docs and new review artifacts are UTF-8 clean. Existing warnings were
not expanded into an unrelated recovery task.

## Current-State Assessment

- Microstep implemented: `PASS`.
- Full repository pytest baseline: `PASS` (`413 passed, 11 skipped`).
- Current repository integration-safe: `PASS` with task-specific staging.
- Phase complete: `PASS`.

## Architecture Assessment

`PASS`. Parsing stays in `SheetsParserService`; persistence stays in
`TasksService.bulk_upsert`. No DB schema change is introduced.

## Security Assessment

`PASS` with accepted tradeoff. `TaskContent` now preserves unknown top-level
keys via `extra="allow"` as required by the future-proof passthrough contract.
Consumers still read specific keys. No secret was added to tracked artifacts.

## Public API Contract Assessment

`PASS`. URL, request body shape, and response shape are unchanged. The optional
Google Sheets column is documented in LMS docs, OpenAPI description, LMS
CHANGELOG, and the ContentBackbone mirror.

## Test Adequacy Assessment

Focused coverage is sufficient for the microstep:

- T-A1..T-A7 parser behavior;
- endpoint `dry_run` and apply branches with one invalid row;
- real DB `bulk_upsert` and direct jsonb `SELECT`;
- live HTTP backward-compatible Google Sheets dry-run.

The dedicated-sheet live apply passed on 2026-06-02:

- dry-run: `18/19` accepted, one expected invalid JSON row;
- apply: `18` updated, one expected invalid JSON row;
- direct DB verification: `TEST-SC-002` stores
  `stem_images=["graph.png"]` and `hints_video=["url"]`.

## Required Validation Commands

```powershell
.venv\Scripts\python.exe -m pytest tests\test_tasks_import_task_content_json.py -q
.venv\Scripts\python.exe -m pytest tests\ -k "import" -v
.venv\Scripts\python.exe -m pytest tests\ -q --tb=no
.venv\Scripts\python.exe scripts\run_mypy.py
.venv\Scripts\python.exe -m mypy --config-file mypy.ini app\services\tasks_service.py app\api\v1\tasks_extra.py
```

## Residual Risks

- Arbitrary future top-level keys can increase jsonb row size up to the Google
  Sheets cell-size limit. This is intentional and documented.
- The reproducible `mypy` contour is intentionally scoped to the task import
  path. Expanding it to the full application requires a separate baseline
  extension.
