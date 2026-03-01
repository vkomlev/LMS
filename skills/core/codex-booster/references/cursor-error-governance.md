# Cursor Error Governance

## Policy
Any significant error made by Cursor agents must be:
1. logged in the project error register,
2. classified by severity/class,
3. converted into preventive changes to skills/rules/workflows.

## Registers
- Project-local register (preferred): `<project>/docs/ai/ERRORS.md`
- Shared register (fallback): `d:/Work/IDE_booster/Docs/ai/ERRORS.md`

## Trigger Conditions
- incorrect implementation causing rework
- outdated/deprecated API usage
- architecture or boundary violations
- unsafe migration/data actions
- missing tests for changed behavior
- repeated DRY/commonization violations

## Audit Checklist
- Was each significant Cursor-agent mistake logged?
- Does each log entry include a prevention action?
- Were prevention actions actually applied to skills/rules?
- Is there a regression check showing the issue is reduced?

## Exit Criteria
For `audit`/`configure` tasks:
- no open high-severity Cursor-agent mistakes without prevention plan
- at least one concrete preventive update for each recurring error pattern

