# MCP Postgres Playbook (Read-Only First)

## Goal
Use MCP Postgres servers to inspect schema and data safely during API implementation and debugging.

## Environment aliases
- `postgres` / `learn_public_db` -> local/dev `Learn`. Never use these aliases as proof of production state.
- `learn_prod_db` -> production LMS database.
- `content_backbone_prod_db` -> production ContentBackbone database.
- `content_hub_readonly` -> production ContentBackbone with `content_hub` search path, read-only use.

Before any data-based conclusion, write down the alias and environment. If a task asks about prod and the prod alias is unavailable, state `prod not checked` instead of inferring from dev.

## Standard Checks
1. List relations:
```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name;
```

2. Inspect columns:
```sql
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
```

3. Verify constraints:
```sql
SELECT conrelid::regclass AS table_name, conname, pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
WHERE connamespace = 'public'::regnamespace
ORDER BY conrelid::regclass::text, conname;
```

4. Spot-check target rows with small limits:
```sql
SELECT * FROM <table_name> ORDER BY 1 DESC LIMIT 20;
```

## Rules
- Use `SELECT` only unless task explicitly requests write path checks.
- Always include small limits for exploratory reads.
- For bugfixes, capture before/after evidence query snippets.
- Reports must keep `dev/local`, `prod`, live browser/API, and local export evidence in separate buckets.
