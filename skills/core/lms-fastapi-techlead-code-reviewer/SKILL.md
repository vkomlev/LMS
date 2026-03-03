---
name: lms-fastapi-techlead-code-reviewer
description: "Perform a strict FastAPI-focused technical lead review for LMS production readiness with PASS/FAIL. Use for API/backend changes, especially date/time logic, raw SQL paths, migration safety, and runtime endpoint behavior."
---

# LMS FastAPI TechLead Code Reviewer

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
- critical UX/UI option correctness and navigation integrity
- specification ambiguity and interpretation risks
- date/time type safety in service logic and raw SQL result handling

## Workflow
1. Read changed files and identify affected runtime paths.
2. Apply baseline checklist from [references/review-checklist.md](references/review-checklist.md).
3. Apply domain checklists as relevant:
- [references/architecture-checks.md](references/architecture-checks.md)
- [references/migration-checks.md](references/migration-checks.md)
- [references/testing-checks.md](references/testing-checks.md)
- [references/observability-checks.md](references/observability-checks.md)
- [references/security-checks.md](references/security-checks.md)
- [references/ux-critical-checks.md](references/ux-critical-checks.md)
- [references/spec-ambiguity-checks.md](references/spec-ambiguity-checks.md)
- [references/datetime-type-safety-checks.md](references/datetime-type-safety-checks.md)
4. Classify findings by severity and impact.
5. If findings indicate Cursor-agent mistakes, create error-log entries using [references/cursor-agent-error-loop.md](references/cursor-agent-error-loop.md).
6. Produce PASS/FAIL with required fixes and validation commands.
7. Add residual risk and post-merge watchpoints if PASS.

## Output Contract
- `Decision` (`PASS` or `FAIL`)
- `Blocking Findings` (must-fix, ordered by severity)
- `Non-Blocking Findings`
- `Architecture Assessment`
- `Migration Assessment` (if DB affected)
- `Test Adequacy Assessment`
- `Observability Assessment`
- `Security Assessment`
- `UX/UI Critical Assessment`
- `Spec Ambiguity Assessment`
- `Date/Time Type Safety Assessment`
- `Required Fixes`
- `Required Validation Commands`
- `Residual Risks`
- `Cursor Agent Error Entries` (one entry per significant Cursor-agent mistake)
- `Skill Improvement Actions` (what to change in developer skills/rules to prevent recurrence)

## Severity Model
- `S1`: production outage/data loss/security breach risk.
- `S2`: likely functional defect or significant rework risk.
- `S3`: maintainability/readability debt with low immediate risk.

## Decision Rules
- `FAIL` if any `S1` remains unresolved.
- `FAIL` if behavior is uncertain in a production-critical path.
- `FAIL` if critical UX action is missing/broken/misdirected in actual user flow.
- `FAIL` if unresolved specification ambiguity can change behavior of critical path.
- `FAIL` if migration rollback is missing for schema-affecting change.
- `FAIL` if tests do not cover the changed behavior and key regressions.
- `FAIL` if raw SQL date/time values are compared with `now` without normalization/type-guards.
- `FAIL` if there is no runtime smoke for at least one detail/list endpoint with date fields (when relevant).
- `FAIL` if bugfix lacks a reproducer test proving failure before fix.
- `FAIL` if significant Cursor-agent mistakes are detected but not logged into project error register.
- `PASS` only when no blocking issue remains and validation is reproducible.

## Quality Rules
- Every finding must include:
- file/path
- why it matters in production
- concrete fix direction
- Keep focus on defects and risks, not style-only commentary.
- Prefer evidence-based claims (tests, logs, code path reasoning).
- Treat repeated Cursor-agent mistakes as process defects: always produce preventive skill/rule updates.
