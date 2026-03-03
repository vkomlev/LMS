# UX Critical Checks

## Goal
Catch production-impacting UX/UI defects that code-centric review can miss.

## Mandatory Checks
- Is every critical action visibly available when user needs it?
- Are critical buttons actually actionable (render condition + callback path)?
- Does `Back` navigate to expected parent state/screen?
- Is navigation behavior consistent with spec and user mental model?
- Are empty/error states not blocking critical user task?
- After UI hotfix, does smoke documentation match the actual button set and navigation behavior on screen?

## Evidence
Require one of:
- deterministic UI test/assertion for visibility and navigation
- manual smoke evidence with exact scenario and outcome

For UI hotfixes also require:
- explicit smoke-doc consistency note (expected buttons vs actual rendered buttons)
