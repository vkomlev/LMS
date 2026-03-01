---
name: review-gate
description: Perform an independent pre-merge quality gate with a strict PASS or FAIL decision, prioritized findings, and required fixes. Use before merge, release, or any high-risk deployment.
---

# Review Gate

## Review Order
1. Correctness and behavioral regressions
2. Data and migration safety
3. Security and secret handling
4. Test adequacy
5. Maintainability and clarity

## Output Contract
- `Decision`: PASS or FAIL
- `Findings` ordered by severity
- `Blocking Issues` (must-fix)
- `Non-Blocking Improvements`
- `Required Tests`

## Decision Rules
- PASS only if no blocking issue remains.
- FAIL if behavior is uncertain in production-critical path.
- Every finding must include:
  - impacted file/path
  - why it matters
  - specific fix direction
