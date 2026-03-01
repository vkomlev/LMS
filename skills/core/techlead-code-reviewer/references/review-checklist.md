# Core Review Checklist

## Correctness
- Does code implement intended behavior exactly?
- Are edge cases and failure paths handled?
- Any hidden state/ordering assumptions?
- Any likely regression in neighboring flows?

## SOLID / DRY / Clean Code
- Single responsibility preserved at class/function level?
- Open/closed preserved without fragile condition chains?
- Dependency inversion respected at boundaries?
- Duplicated logic introduced instead of reuse?
- Naming, abstraction, and function size support maintainability?

## Production Reliability
- Timeouts/retries/error handling are explicit and bounded?
- Resource handling (DB sessions, files, network) is safe?
- Concurrency/idempotency considerations covered where relevant?

## Operational Readiness
- Logs provide enough context for incident diagnosis?
- Sensitive data excluded from logs?
- Validation commands are reproducible?

