# Library Fact-Check Protocol

## Goal
Prevent hallucinated or outdated API recommendations in generated plans/instructions.

## Mandatory Checks
1. Identify target library and version from project lock/pins.
2. Verify candidate API in official docs/changelog for that version range.
3. If version-specific uncertainty remains, mark as `Unverified` and propose safe fallback.

## For aiogram / aiogram-dialog
- Confirm pinned versions from project requirements.
- Reject aiogram 2.x syntax when project is on 3.x.
- Confirm handler/router/dialog API signatures against official docs.

## Output Requirement
Always include:
- source used
- version assumption
- verified API symbols
- rejected deprecated/invalid alternatives

