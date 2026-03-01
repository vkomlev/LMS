# MCP Postgres Playbook (Read-Only First)

## Goal
Use MCP server `postgresql` to inspect schema and data safely during API implementation and debugging.

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

