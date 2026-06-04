# Rollback: LMS import task_content_json extensions

Date: 2026-06-02

## Scope

No DB migration is required. Rollback is an atomic revert of the LMS change and
the ContentBackbone contract mirror update.

## Steps

1. Revert the LMS merge commit that adds `task_content_json`.
2. Revert the matching ContentBackbone documentation commit.
3. Regenerate `docs/openapi.json`.
4. Run `pytest tests\test_tasks_import_task_content_json.py -q` after the revert
   only if the test file is intentionally retained; otherwise run the existing
   import smoke.

## Data note

Rows already imported with extension keys remain valid jsonb. Older LMS code
will ignore unknown keys when reading task content. Existing imports without
`task_content_json` keep their previous behavior.

