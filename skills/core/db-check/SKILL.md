---
name: db-check
description: Validate database assumptions, schema state, and data invariants safely through read-only checks before and after implementation. Use for migrations, data-sensitive features, and incident triage.
---

# DB Check

## Safety Policy
- Default to read-only checks.
- Run write queries only when explicitly required by the task contract.
- Prefer migration files over ad-hoc structural SQL.
- Never print credentials or connection strings containing secrets.
- Use transactions for every write path: `BEGIN` -> operation -> verification -> `COMMIT` or `ROLLBACK`.
- Treat MCP alias names as part of the evidence. `postgres` means local/dev unless proven otherwise; prod LMS is `learn_prod_db`, prod ContentBackbone is `content_backbone_prod_db`, and prod `content_hub` read-only is `content_hub_readonly`.
- Never report a prod conclusion from `postgres` / `learn_public_db`. If prod alias is unavailable, say `prod not checked`.

## Workflow
1. Confirm target database and environment (`dev/local`, `prod`, or `unknown`) and name the exact MCP alias used.
2. Prefer configured MCP PostgreSQL tools; use `psql` only as fallback.
3. Run connectivity sanity check (`SELECT 1` equivalent).
4. Verify schema objects involved by the task: tables, columns, indexes, FK/unique constraints.
5. Validate data invariants with focused queries: counts, NULLs, orphans, duplicates, and domain-specific states.
6. For performance incidents, inspect query plans or table statistics before recommending indexes.
7. Record query results needed for audit/review.
8. For write-mode tasks, first show the target row set with `SELECT ... LIMIT`, then perform the change in a transaction and verify with a post-query.

## Output Contract
- `DB Target`
- `MCP Alias / Environment`
- `Checks Executed`
- `Invariant Results`
- `Performance Findings`
- `Risks`
- `Recommended Next Action`

## Quality Rules
- Read-only is the default mode.
- `DROP`, `TRUNCATE`, and `DELETE` without `WHERE` require explicit task approval plus pre-audit evidence.
- Avoid unbounded `SELECT *` on large tables; use aggregation or `LIMIT`.
- Schema changes should go through migrations with rollback notes.
- Every query mentioned in the report needs either a result or a reason it was skipped.
- When checking LMS publication state, separate `LMS prod DB`, `LMS dev DB`, `WP live`, and `local export`; do not merge them into one verdict.
