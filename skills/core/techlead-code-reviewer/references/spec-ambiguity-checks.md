# Spec Ambiguity Checks

## Goal
Prevent silent implementation drift caused by ambiguous requirements.

## Red Flags
- Multiple possible interpretations of navigation target (for example Back target).
- Conflicting phrases like "go back to menu/list" without explicit state/screen id.
- Missing precedence rules for optional/alternative behavior.
- Undefined behavior for empty/error data conditions.

## Review Actions
- Mark ambiguity as finding, not assumption.
- Classify as `Blocking` if it affects critical path.
- Require explicit contract update in spec/TZ.
- Require implementation to reference exact target behavior (state/screen id).

