# Operating Model

## Work Contours
1. Product delivery contour
- spec -> implementation -> review -> integration

2. Content/pipeline contour
- source intake -> parsing -> enrichment -> publishing/reporting

3. Improvement contour
- capture errors -> classify -> update rules/skills -> regression check

## Tier Routing
- Tier L: Cursor agents for routine deterministic execution.
- Tier M: Codex for medium complexity integration and structured planning.
- Tier H: Claude for architecture-level reasoning and final quality gate.

## Gates
- `spec-gate`: objective, scope, acceptance, constraints are explicit.
- `execution-gate`: implementation completed with relevant checks.
- `review-gate`: independent PASS/FAIL decision with findings.
- `merge/commit-gate`: integrate to main/master only after PASS.

## Branch Policy
- Default mode: direct work in main/master with stronger pre-commit controls.
- Separate branches: only for fully autonomous long-running agent work.

