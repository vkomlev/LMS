---
name: techlead-code-reviewer
description: "Perform a strict technical lead code review for production readiness with a PASS/FAIL decision. Use before integration to main/master, release candidate approval, risky refactors, schema migrations, and any change where correctness, architecture integrity, and reliability are critical."
---

# TechLead Code Reviewer

## Shared Runtime Contract
Apply the booster-wide contract from [booster-runtime-contract.md](/d:/Work/IDE_booster/Docs/ai-booster/booster-runtime-contract.md).

## Operating Modes
- Review mode:
  - `standard` by default.
  - `paranoid` for release candidates, risky refactors, schema or data changes, auth/security-sensitive work, or when the user explicitly asks for maximum skepticism.
- Execution posture:
  - always `report-only`;
  - never silently switch into fix mode inside the same response.

## Review Scope
Review for:
- correctness and regressions;
- architecture and layering;
- tests, observability, security, and rollback;
- critical UX/navigation correctness;
- spec clarity and date/time safety;
- phase integrity, domain completeness, and operator-critical acceptance paths;
- docs/config/runtime drift that can make the repository or operator path misleading.

## Workflow
1. Declare the review mode (`standard` or `paranoid`) and keep `report-only` posture explicit.
2. Read the changed files and identify affected runtime paths, docs, config, and operator touchpoints.
3. Apply the baseline checklist from [references/review-checklist.md](references/review-checklist.md).
4. Apply domain checklists only where relevant:
- [references/architecture-checks.md](references/architecture-checks.md)
- [references/migration-checks.md](references/migration-checks.md)
- [references/testing-checks.md](references/testing-checks.md)
- [references/observability-checks.md](references/observability-checks.md)
- [references/security-checks.md](references/security-checks.md)
- [references/ux-critical-checks.md](references/ux-critical-checks.md)
- [references/spec-ambiguity-checks.md](references/spec-ambiguity-checks.md)
- [references/datetime-type-safety-checks.md](references/datetime-type-safety-checks.md)
5. In `paranoid` mode, assume hidden breakage is more likely than the diff suggests and actively search for missing guards, stale contracts, unsafe defaults, and rollback gaps.
6. Classify the review horizon explicitly:
- `microstep implemented`
- `current repository integration-safe`
- `phase complete`
7. Classify findings by severity and impact.
8. If findings indicate Cursor-agent mistakes, log them via [references/cursor-agent-error-loop.md](references/cursor-agent-error-loop.md).
9. Produce `PASS` or `FAIL`, required fixes, and reproducible validation commands.
10. If the same phase is repeatedly `FAIL`, end with either explicit escalation or a tight next-iteration checklist.

For migration/cutover/closeout reviews, explicitly distinguish:
- `entrypoint migrated`
- `execution migrated`
- `state/storage migrated`

Do not treat operator CLI relocation or orchestration wrapping as full migration if runtime still executes in legacy/external codepaths.

## Output Contract
- `Review Mode`
- `Execution Posture`
- `Decision`
- `Blocking Findings`
- `Non-Blocking Findings`
- `Current-State Assessment`
- `Architecture Assessment`
- `Migration Assessment`
- `Test Adequacy Assessment`
- `Observability Assessment`
- `Security Assessment`
- `UX/UI Critical Assessment`
- `Spec Ambiguity Assessment`
- `Date/Time Type Safety Assessment`
- `Docs/Config/Runtime Drift Assessment`
- `Required Fixes`
- `Required Validation Commands`
- `Residual Risks`
- `Cursor Agent Error Entries`
- `Skill Improvement Actions`

## Severity Model
- `S1`: production outage, data loss, or security breach risk.
- `S2`: likely functional defect or major rework risk.
- `S3`: maintainability debt with low immediate risk.

## Decision Rules
- `FAIL` if any `S1` remains unresolved.
- `FAIL` if the current repository state is unsafe even when the reviewed microstep itself is partly correct.
- `FAIL` if the review relies on future planned work to justify current breakage.
- `FAIL` if the phase business goal, domain prerequisites, or operator-critical acceptance chain are not proven.
- `FAIL` for migration/closeout claims if legacy/external runtime still performs active execution or writes for a contour claimed as migrated, frozen, or read-only.
- `FAIL` if docs/config/runtime drift makes the operator path, deployment path, or repository understanding unsafe.
- `FAIL` if critical UX controls, rollback, tests, or specification clarity are insufficient.
- `FAIL` if significant Cursor-agent mistakes were found but not logged.
- `PASS` only when blocking issues are resolved and validation is reproducible.

## Quality Rules
- Use Russian by default unless the user asked for another language.
- Every finding must include: file/path, current-state risk, production impact, and concrete fix direction.
- Keep the review defect-focused; avoid style-only commentary.
- Prefer current-state evidence over roadmap intent.
- Prefer one strong review artifact over bloated review paperwork.
