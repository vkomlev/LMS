# Booster Packaging Patterns

## Pattern A: Modular Skills First
Use when requirements are still evolving.
- Keep each role as an isolated skill (`spec`, `plan`, `ux`, `review`, `quality-loop`).
- Pros: easy iteration, low blast radius.
- Cons: more manual orchestration.

## Pattern B: Plugin Bundle
Use when operating model is stable.
- Bundle curated skills + subagents + MCP presets + rules into one plugin.
- Pros: fast replication across projects/workspaces.
- Cons: requires strict versioning and plugin QA.

## Pattern C: Hybrid
Use for mixed maturity.
- Keep core plugin stable.
- Keep fast-changing capabilities as external modular skills.

## Recommended For Current Booster
- Phase 1-2: Hybrid.
- Stabilize core:
  - tier routing
  - review gate policy
  - encoding guard
  - answer/coding feedback loops
- Move volatile items (new Cursor feature experiments) outside core plugin until validated.

