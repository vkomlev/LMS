---
name: spec-writer
description: Convert ambiguous requests into an implementation-ready specification with scope, constraints, risks, acceptance criteria, and execution checkpoints. Use when a task is unclear, under-specified, or likely to cause rework without a clear plan.
---

# Spec Writer

## Workflow
1. Restate the requested outcome in one sentence.
2. Define in-scope and out-of-scope items.
3. List explicit constraints (tech, security, timeline, dependencies).
4. Convert the request into numbered implementation steps.
5. Define measurable acceptance criteria.
6. Add risks and mitigation per risk.
7. Produce a short execution checklist.

## Output Contract
- `Objective`
- `Scope`
- `Constraints`
- `Plan`
- `Acceptance Criteria`
- `Risks`
- `Execution Checklist`

## Quality Rules
- Avoid vague words (fast, robust, better) without metrics.
- Make each acceptance criterion testable.
- Prefer short, deterministic steps over broad guidance.
