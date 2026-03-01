---
name: change-plan-architect
description: Build an implementation plan for a requested change after analyzing current code, contracts, dependencies, and missing resources. Use when planning medium-complexity changes, detecting blockers, identifying missing endpoints/contracts, and sequencing safe delivery steps.
---

# Change Plan Architect

## Workflow
1. Parse request into target capability and non-goals.
2. Inspect current implementation and list impacted components.
3. Detect dependency gaps (API, data model, permissions, external service).
4. Detect contract ambiguity and convert it into explicit questions/assumptions.
5. Build phased plan with checkpoints and owner/tier mapping.
6. Define preconditions for each phase (what must exist first).
7. Define delivery risks, mitigations, and fallback path.
8. Define validation and readiness gate for main/master integration.

## Required Gap Analysis
Read [references/resource-gap-checklist.md](references/resource-gap-checklist.md) and include every relevant gap category in analysis.

## Output Contract
- `Requested Capability`
- `Current State Summary`
- `Impact Map`
- `Gaps and Missing Resources`
- `Assumptions and Open Questions`
- `Implementation Phases`
- `Tier Routing per Phase`
- `Validation Plan`
- `Risks and Mitigations`
- `Go/No-Go Criteria`

## Quality Rules
- Prefer explicit dependency statements over implicit assumptions.
- Mark every unresolved contract as `Blocking` or `Non-Blocking`.
- Keep phases short and testable.
- Do not mix design and implementation in one unchecked phase.

