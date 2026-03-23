---
name: ai-orchestrator
description: Orchestrate multi-agent execution across the booster setup using three work contours and three AI tiers (L/M/H), with project-aware routing, gates, and handoff artifacts. Use when planning or coordinating end-to-end task delivery across Cursor agents, Codex, and Claude.
---

# AI Orchestrator

## Shared Runtime Contract
Apply the booster-wide contract from [booster-runtime-contract.md](/d:/Work/IDE_booster/Docs/ai-booster/booster-runtime-contract.md).

## Operating Modes
- Orchestration mode:
  - `standard` by default.
  - `paranoid` for release, risky deploy, schema or data changes, auth/security-sensitive work, external side effects, or when the user explicitly asks for maximum skepticism.
- Execution posture:
  - `report-only` unless the user explicitly asks for execution planning that should immediately drive agent work.

## Workflow
1. Declare the orchestration mode (`standard` or `paranoid`) and keep execution posture explicit.
2. Parse the request into objective, risk level, and affected projects.
3. Select work contour and task type using [references/operating-model.md](references/operating-model.md).
4. Route subtasks by tier:
- Tier L -> Cursor agents
- Tier M -> Codex
- Tier H -> Claude
5. For feature planning, redesign, or high-ambiguity product work, insert:
- `product-review` when user value, acceptance, or scope justification must be pressure-tested;
- `eng-review` when implementation boundaries, trust boundaries, rollback, or validation strategy must be pressure-tested.
Legacy note:
- if an older workflow explicitly names `ceo-review`, treat it as `product-review` aliasing rather than a separate planning branch.
6. In `paranoid` mode:
- bias risky review, gate, and cutover decisions toward Tier M/H;
- insert `qa-report` before final gate for release, user-facing, operator-facing, or regression-sensitive changes;
- insert `release-prep` before release or risky deployment decisions;
- require `techlead-code-reviewer` in `paranoid` mode before final `review-gate`;
- require `review-gate` in `paranoid` mode for integration, release, or risky deployment decisions;
- treat missing validation evidence, docs/config/runtime drift, and unresolved contract ambiguity as blockers, not follow-up notes.
7. Build a stage plan with explicit handoffs and artifacts per stage.
8. For product-review stages, require these handoff artifacts where relevant:
- `Review Mode`
- `Execution Posture`
- `Problem and User Value Assessment`
- `Scope Assessment`
- `Acceptance Path Assessment`
- `Operator Burden Assessment`
- `Tradeoff Assessment`
- `Required Changes`
- `Go/No-Go`
- `Recommended Next Step`
9. For eng-review stages, require these handoff artifacts where relevant:
- `Review Mode`
- `Execution Posture`
- `Current-State Assessment`
- `Architecture Assessment`
- `Trust Boundary Assessment`
- `Failure Mode Assessment`
- `Test Strategy Assessment`
- `Rollback and Compatibility Assessment`
- `Required Changes`
- `Go/No-Go`
- `Recommended Next Step`
10. For UX, bugfix, release, or regression-sensitive work, include a `qa-report` stage when behavior confidence matters more than code inspection alone.
11. For QA stages, require these handoff artifacts where relevant:
- `QA Mode`
- `Execution Posture`
- `Scope`
- `Environment`
- `Current-State Assessment`
- `Blocking Issues`
- `Untested or Blocked Areas`
- `Evidence`
- `Recommended Next Step`
12. If remediation is explicitly requested after QA or QA finds blocking issues that are safe to fix, route to `qa-fix` before final gate.
13. For QA fix stages, require these handoff artifacts where relevant:
- `QA Fix Mode`
- `Severity Scope`
- `Execution Posture`
- `Consumed QA Artifacts`
- `Current-State Assessment`
- `Fixed Issues`
- `Validation Results`
- `Residual Risks`
- `Recommended Next Step`
14. For release-prep stages, require these handoff artifacts where relevant:
- `Release Mode`
- `Execution Posture`
- `Release Scope`
- `Preflight Assessment`
- `Validation Assessment`
- `Blocked or Untested Critical Paths`
- `Docs/Config/Runtime Drift Assessment`
- `Rollback Assessment`
- `Operator Readiness Assessment`
- `Post-Release Follow-Ups`
- `Go/No-Go`
- `Recommended Next Step`
15. For review and gate stages, require these handoff artifacts where relevant:
- `Review Mode` or `Gate Mode`
- `Execution Posture`
- `Current-State Assessment`
- `Blocking Findings` or `Blocking Issues`
- `Docs/Config/Runtime Drift Assessment`
- `Required Validation Commands`
- `Residual Risks`
16. Apply mandatory gates (`spec-gate`, `execution-gate`, `review-gate`, `merge/commit-gate`).
17. Define escalation and fallback conditions.
18. Return an execution card with owners, commands, and exit criteria.

## Project Context
Read [references/project-context.md](references/project-context.md) and include project-specific constraints in the orchestration plan.

## Input Contract
- `Orchestration Mode` (`standard` or `paranoid`, optional)
- `Objective`
- `Project(s)`
- `Task Type` (feature, bugfix, db-change, pipeline, ux)
- `Risk Notes` (optional)
- `Deadline` (optional)

## Output Contract
- `Orchestration Mode`
- `Execution Posture`
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
- In `paranoid` mode, prefer a stronger gate over a faster stage.
