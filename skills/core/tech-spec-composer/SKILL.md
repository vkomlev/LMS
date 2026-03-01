---
name: tech-spec-composer
description: Produce a developer-ready technical assignment for Tier L executors with explicit context, constraints, stack rules, required skills, plugins/tools, acceptance criteria, and handoff artifacts. Use when preparing implementation tasks for Cursor agents, Codex executors, API work, bot work, parsers, and publishers.
---

# Tech Spec Composer

## Workflow
1. Restate business goal and user-visible outcome in one sentence.
2. Capture implementation context: repo, modules, related services, current behavior.
3. Define strict scope: in-scope, out-of-scope, and no-touch zones.
4. Specify stack and framework constraints for the target project type.
5. Declare mandatory tools/skills/rules that Tier L must use.
6. Write a deterministic implementation sequence with file-level hints.
7. Define acceptance criteria, validation commands, and review artifacts.
8. Add rollback notes and risk controls for main/master direct commits.

## Domain Mode Selection
Read [references/domain-modes.md](references/domain-modes.md) and choose one mode:
- `telegram-bot`
- `api-service`
- `parser-pipeline`
- `publisher-integration`

Use only the selected mode checklist in the final assignment.

## Output Contract
- `Objective`
- `Context`
- `Scope`
- `Stack and Constraints`
- `Required Skills/Rules`
- `Implementation Steps`
- `Acceptance Criteria`
- `Validation Commands`
- `Handoff Artifacts`
- `Risks and Rollback`

## Quality Rules
- Write requirements so a Tier L agent can execute without guessing.
- Prefer measurable criteria over qualitative wording.
- Bind each validation command to a concrete expected result.
- Keep steps atomic; one action per step.

