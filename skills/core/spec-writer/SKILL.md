---
name: spec-writer
description: Convert ambiguous requests into an implementation-ready specification with scope, constraints, risks, acceptance criteria, and execution checkpoints. Use when a task is unclear, under-specified, or likely to cause rework without a clear plan.
---

# Spec Writer

## Workflow
1. Collect context anchors using [references/context-and-routing.md](references/context-and-routing.md).
2. Restate the requested outcome in one sentence.
3. Define in-scope and out-of-scope items.
4. List explicit constraints (tech, security, timeline, dependencies).
5. Convert the request into numbered implementation steps.
6. Assign one responsible skill to each implementation step.
7. Define measurable acceptance criteria.
8. Add risks and mitigation per risk.
9. Produce a short execution checklist.
10. Verify that every context anchor is reflected in the spec or explicitly excluded with a reason.

## Output Contract
- `Objective`
- `Scope`
- `Constraints`
- `Plan`
- `Acceptance Criteria`
- `Risks`
- `Execution Checklist`
- `Skill Routing`
- `Context Anchors`

## Quality Rules
- Avoid vague words (fast, robust, better) without metrics.
- Make each acceptance criterion testable.
- Prefer short, deterministic steps over broad guidance.
- A spec without `Skill Routing` is incomplete.
- Do not invent context when a local file, project memory, or cross-project contract can be checked.
