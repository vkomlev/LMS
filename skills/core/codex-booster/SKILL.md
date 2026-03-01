---
name: codex-booster
description: "Operate and improve Codex within the Cursor-based booster environment: select Codex role usage, apply local skills, manage rollout to projects, and enforce answer/code quality loops. Use when configuring, auditing, or scaling Codex workflows across your project fleet."
---

# Codex Booster

## Workflow
1. Identify requested operation mode:
- `use`: run Codex on a concrete task with correct tier and skills.
- `configure`: adjust Codex instructions/skills/routing.
- `rollout`: distribute skills and docs to project fleet.
- `audit`: review consistency and quality loops.
2. Load project inventory from [references/fleet-map.md](references/fleet-map.md).
3. Select skills and routing strategy from [references/skill-catalog.md](references/skill-catalog.md).
4. For rollout, use [references/rollout-ops.md](references/rollout-ops.md).
5. Produce explicit commands, expected artifacts, and verification checklist.
6. If request depends on latest Codex/Cursor capabilities, verify with official docs before final guidance.

## Input Contract
- `Mode` (`use|configure|rollout|audit`)
- `Project` or `Fleet Scope`
- `Objective`
- `Constraints`

## Output Contract
- `Selected Mode`
- `Tier and Skill Routing`
- `Execution Steps`
- `Commands`
- `Expected Artifacts`
- `Verification`
- `Follow-up Improvements`

## Quality Rules
- Keep steps deterministic and project-scoped.
- Use dry-run first for bulk rollout.
- Do not claim "latest feature" without verifying source/date.
- Prefer minimal change set with clear rollback path.
