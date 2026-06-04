---
name: session-digest
description: "Summarize Codex or Claude session history for a date range into a structured markdown digest with machine-readable YAML. Use for retrospectives, handoff, weekly summaries, or content inputs."
---

# Session Digest

## Role
Session analyst: extract useful work signals from local AI session logs without leaking secrets.

## Inputs
- Time range: `today`, `yesterday`, `last-Nd`, `this-week`, `YYYY-MM-DD`, or `YYYY-MM-DD..YYYY-MM-DD`.
- Project filter: `all`, a project slug, or a known project path.

Default to `today all` when the user does not specify a range.

## Workflow
1. Resolve date range using the user's local timezone.
2. Locate session sources:
- Codex: `C:/Users/user/.codex/sessions`, `C:/Users/user/.codex/archived_sessions`;
- Claude: `C:/Users/user/.claude/projects`;
- project memory files when present.
3. For Claude JSONL imports, load [references/claude-jsonl-format.md](references/claude-jsonl-format.md). Treat it as Claude-only parsing guidance; do not assume Codex sessions use the same event schema.
4. Filter by mtime first, then by event timestamps where the format supports it.
5. Extract signal:
- user tasks;
- assistant summaries;
- tool actions;
- changed files;
- validation commands;
- outcomes and blockers.
6. Cluster related prompts into task themes. Do not over-split tiny follow-ups.
7. Redact secrets, tokens, credentials, and private keys.
8. Produce a markdown digest plus a YAML block.
9. Save only when the target output directory exists or the user asked for a saved artifact; otherwise return the digest in chat.

## Output Contract
- `Digest`
- `Period`
- `Projects`
- `Sessions`
- `Topics`
- `Tools Used`
- `Files Mentioned`
- `Outcomes`
- `YAML Block`
- `Saved Path` (if saved)

## Quality Rules
- Facts must come from logs or local artifacts, not memory.
- If no events exist for the period, say so directly.
- Mask secrets even if they appear in logs.
- For large ranges, summarize by clusters and avoid dumping raw logs.
- Treat session stores as read-only.
- Write output as UTF-8.
