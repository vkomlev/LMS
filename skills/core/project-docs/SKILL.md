---
name: project-docs
description: "Create or update two-layer project documentation: AI-facing AGENTS.md/docs/ai and human-facing README/docs. Use for new projects, stale docs, handoff readiness, or reducing repeated repository rediscovery."
---

# Project Docs

## Role
Documentation architect: maintain separate AI and human documentation layers without duplication.

## Modes
- `full`: create missing documentation from scratch.
- `update`: incrementally update existing documentation.

Default to `update` when `AGENTS.md` or `README.md` already exists.

## Workflow
1. Discover the project:
- stack files (`package.json`, `pyproject.toml`, `requirements.txt`, `go.mod`, etc.);
- top-level structure;
- entry points;
- config and env examples;
- DB/migrations;
- existing docs;
- common commands;
- recent `git log` when available.
2. Decide AI-facing vs human-facing facts:
- AI layer: `AGENTS.md`, `docs/ai/architecture.md`, `docs/ai/workflows.md`, `docs/ai/errors.md`, `docs/ai/glossary.md`.
- Human layer: `README.md`, `docs/install.md`, `docs/usage.md`, `docs/configuration.md`, `docs/troubleshooting.md`.
3. In `full` mode, create only useful missing artifacts.
4. In `update` mode, patch existing docs minimally and create missing files only when they clearly reduce future ambiguity.
5. Keep `AGENTS.md` short and operational: facts that prevent agent mistakes, not broad prose.
6. Keep `README.md` human-readable: purpose, quick start, usage, config, docs links.
7. Verify links, commands, line counts, secrets, and UTF-8.

## Output Contract
- `Mode`
- `Created Files`
- `Changed Files`
- `Key Facts Added`
- `Line Counts`
- `Gaps`
- `Verification`
- `Next Steps`

## Quality Rules
- Do not invent facts. Mark unknowns as gaps.
- Do not duplicate long content between AI and human docs.
- `AGENTS.md` should answer: "Would removing this line make an AI likely to err?"
- Never include secret values.
- Prefer update with minimal diffs over rewriting existing docs.
- All docs must be UTF-8.

