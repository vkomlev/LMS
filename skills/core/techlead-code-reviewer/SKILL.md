---
name: techlead-code-reviewer
description: "Perform a strict technical lead code review for production readiness with a PASS/FAIL decision. Use before integration to main/master, release candidate approval, risky refactors, schema migrations, and any change where correctness, architecture integrity, and reliability are critical."
---

# TechLead Code Reviewer

## Review Scope
Review for:
- correctness and regressions
- architecture and layering
- SOLID and DRY adherence
- clean code and maintainability
- logging/observability quality
- migration safety and rollback
- test coverage adequacy
- security and operational risk

## Workflow
1. Read changed files and identify affected runtime paths.
2. Apply baseline checklist from [references/review-checklist.md](references/review-checklist.md).
3. Apply domain checklists as relevant:
- [references/architecture-checks.md](references/architecture-checks.md)
- [references/migration-checks.md](references/migration-checks.md)
- [references/testing-checks.md](references/testing-checks.md)
- [references/observability-checks.md](references/observability-checks.md)
- [references/security-checks.md](references/security-checks.md)
4. Classify findings by severity and impact.
5. Produce PASS/FAIL with required fixes and validation commands.
6. Add residual risk and post-merge watchpoints if PASS.

## Output Contract
- `Decision` (`PASS` or `FAIL`)
- `Blocking Findings` (must-fix, ordered by severity)
- `Non-Blocking Findings`
- `Architecture Assessment`
- `Migration Assessment` (if DB affected)
- `Test Adequacy Assessment`
- `Observability Assessment`
- `Security Assessment`
- `Required Fixes`
- `Required Validation Commands`
- `Residual Risks`

## Severity Model
- `S1`: production outage/data loss/security breach risk.
- `S2`: likely functional defect or significant rework risk.
- `S3`: maintainability/readability debt with low immediate risk.

## Decision Rules
- `FAIL` if any `S1` remains unresolved.
- `FAIL` if behavior is uncertain in a production-critical path.
- `FAIL` if migration rollback is missing for schema-affecting change.
- `FAIL` if tests do not cover the changed behavior and key regressions.
- `PASS` only when no blocking issue remains and validation is reproducible.

## Quality Rules
- Every finding must include:
- file/path
- why it matters in production
- concrete fix direction
- Keep focus on defects and risks, not style-only commentary.
- Prefer evidence-based claims (tests, logs, code path reasoning).
