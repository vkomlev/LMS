---
name: review-gate
description: Perform an independent pre-merge quality gate with a strict PASS or FAIL decision, prioritized findings, and required fixes. Use before merge, release, or any high-risk deployment.
---

# Review Gate

## Shared Runtime Contract
Apply the booster-wide contract from [booster-runtime-contract.md](/d:/Work/IDE_booster/Docs/ai-booster/booster-runtime-contract.md).

## Operating Modes
- Gate mode:
  - `standard` by default.
  - `paranoid` for release, risky deploy, schema or data changes, auth/security-sensitive work, or when the user asks for maximum conservatism.
- Execution posture:
  - always `report-only`.

## Purpose
This skill is the final gate decision layer.
It should consume the strongest available review artifacts first, especially outputs from `techlead-code-reviewer`, and convert them into a strict go/no-go decision for integration to `main/master`, release, or risky deployment.

## Workflow
1. Declare the gate mode (`standard` or `paranoid`) and keep `report-only` posture explicit.
2. Read the current-state evidence:
- changed files and affected runtime paths;
- relevant review artifacts, if they already exist;
- validation commands and observed results.
3. If a `techlead-code-reviewer` artifact exists, explicitly consume these fields from it:
- `Review Mode`
- `Execution Posture`
- `Decision`
- `Blocking Findings`
- `Non-Blocking Findings`
- `Current-State Assessment`
- `Docs/Config/Runtime Drift Assessment`
- `Required Validation Commands`
- `Residual Risks`
4. If a `qa-report` artifact exists, explicitly consume these fields from it:
- `QA Mode`
- `Execution Posture`
- `Scope`
- `Environment`
- `Current-State Assessment`
- `Blocking Issues`
- `Non-Blocking Issues`
- `Untested or Blocked Areas`
- `Severity Assessment`
- `Evidence`
- `Recommended Next Step`
5. If a `release-prep` artifact exists, explicitly consume these fields from it:
- `Release Mode`
- `Execution Posture`
- `Release Scope`
- `Preflight Assessment`
- `Validation Assessment`
- `Blocked or Untested Critical Paths`
- `Docs/Config/Runtime Drift Assessment`
- `Rollback Assessment`
- `Operator Readiness Assessment`
- `Go/No-Go`
- `Recommended Next Step`
6. If no upstream review artifact exists, perform a compact independent gate review focused on decision safety rather than broad commentary.
7. Evaluate the gate in this order:
- correctness and behavioral regressions;
- data and migration safety;
- security and secret handling;
- QA evidence and blocked acceptance paths;
- release readiness, rollback credibility, and operator readiness;
- docs/config/runtime drift;
- test adequacy and validation coverage;
- maintainability only where it changes release safety.
8. In `paranoid` mode, assume missing evidence is unsafe until proven otherwise.
9. Return a strict `PASS` or `FAIL` with required fixes and the next safe step.

## Output Contract
- `Gate Mode`
- `Execution Posture`
- `Decision`
- `Current-State Assessment`
- `Consumed Review Artifacts`
- `Consumed QA Artifacts`
- `Consumed Release Artifacts`
- `Blocking Issues`
- `Non-Blocking Improvements`
- `Docs/Config/Runtime Drift Assessment`
- `Required Fixes`
- `Required Tests`
- `Required Validation Commands`
- `Residual Risks`
- `Next Safe Step`

## Decision Rules
- `PASS` only if no blocking issue remains.
- `FAIL` if production-critical behavior is uncertain.
- `FAIL` if validation evidence is missing for a risky path.
- `FAIL` if QA artifacts show unresolved `S1/S2` behavior on a release-critical, user-critical, or operator-critical path.
- `FAIL` if QA reports blocked or untested acceptance paths that are required for integration or release confidence.
- `FAIL` if release artifacts show `no-go`, non-credible rollback, or unready operator path for the planned release.
- `FAIL` if docs/config/runtime drift makes deployment, operator flow, or repository understanding unsafe.
- `FAIL` if an upstream review already says `FAIL` and the blocking issues remain unresolved.
- In `paranoid` mode, unresolved ambiguity is itself a blocking issue.

## Quality Rules
- Prefer current-state evidence over intended follow-up work.
- Consume existing review artifacts instead of rewriting a second full review when the first one is already strong.
- Every finding must include: impacted file/path, current-state risk, why it matters, and specific fix direction.
- Keep the gate decision compact and binary: it is a go/no-go layer, not a brainstorming layer.
