---
name: db-check
description: Validate database assumptions, schema state, and data invariants safely through read-only checks before and after implementation. Use for migrations, data-sensitive features, and incident triage.
---

# DB Check

## Safety Policy
- Default to read-only checks.
- Run write queries only when explicitly required by the task contract.
- Prefer migration files over ad-hoc structural SQL.

## Workflow
1. Confirm target database and environment.
2. Run connectivity sanity check (`SELECT 1` equivalent).
3. Verify schema objects involved by the task.
4. Validate data invariants with focused queries.
5. Record query results needed for audit/review.

## Output Contract
- `DB Target`
- `Checks Executed`
- `Invariant Results`
- `Risks`
- `Recommended Next Action`
