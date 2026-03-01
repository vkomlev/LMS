# Durable Fix Patterns

## Instruction-Level Fixes
- Add a trigger condition in skill `description` so the skill activates at the right time.
- Add explicit output contract fields that were missing in weak responses.
- Add deterministic quality rules to reduce style variance.

## Workflow-Level Fixes
- Insert a mandatory verification step before final answer in unstable domains.
- Insert a scope lock step: restate objective and out-of-scope before execution.
- Add a stop/escalation condition for ambiguous or risky tasks.

## Content-Level Fixes
- Replace vague adjectives with measurable criteria.
- Convert generic advice into file/path/action scoped instructions.
- Add concise examples for known failure patterns.

## Regression Check
- Re-run with the original failed prompt.
- Run one neighboring prompt from same domain.
- Confirm both pass without extra manual clarification.

