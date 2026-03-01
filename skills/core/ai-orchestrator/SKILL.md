---
name: ai-orchestrator
description: Orchestrate multi-agent execution across the booster setup using three work contours and three AI tiers (L/M/H), with project-aware routing, gates, and handoff artifacts. Use when planning or coordinating end-to-end task delivery across Cursor agents, Codex, and Claude.
---

# AI Orchestrator

## Workflow
1. Parse the request into objective, risk level, and affected projects.
2. Select work contour and task type using [references/operating-model.md](references/operating-model.md).
3. Route subtasks by tier:
- Tier L -> Cursor agents
- Tier M -> Codex
- Tier H -> Claude
4. Build a stage plan with explicit handoffs and artifacts per stage.
5. Apply mandatory gates (`spec-gate`, `execution-gate`, `review-gate`, `merge/commit-gate`).
6. Define escalation and fallback conditions.
7. Return an execution card with owners, commands, and exit criteria.

## Project Context
Read [references/project-context.md](references/project-context.md) and include project-specific constraints in the orchestration plan.

## Input Contract
- `Objective`
- `Project(s)`
- `Task Type` (feature, bugfix, db-change, pipeline, ux)
- `Risk Notes` (optional)
- `Deadline` (optional)

## Output Contract
- `Execution Topology`
- `Tier Routing`
- `Stage Plan`
- `Handoffs and Artifacts`
- `Quality Gates`
- `Escalation Rules`
- `Go/No-Go`

## Quality Rules
- Keep routing decisions explicit and justified.
- Never skip review-gate for integration to main/master.
- Prefer smallest viable stage with measurable completion criteria.
- Flag contract ambiguity as blocker instead of guessing.
