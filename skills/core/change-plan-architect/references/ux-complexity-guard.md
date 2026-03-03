# UX Complexity Guard

## Goal
Prevent unnecessary intermediate screens/steps that increase user friction.

## Guard Questions
- Can the user be taken directly to the actionable screen in happy path?
- Does each extra screen reduce risk or improve clarity measurably?
- Can "refresh" or "next" behavior be embedded without separate screen?
- Is navigation reversible and predictable with one Back action?

## Decision Rule
- If an intermediate screen has no distinct user value, remove it.
- Mark deliberate complexity as `justified` with explicit reason and acceptance test.

