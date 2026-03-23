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

## Docs / Config / Runtime Drift
- Do docs, config, migrations, prompts, or operator instructions drift from the implemented behavior?
- Could a deployer or operator follow repository docs and still produce the wrong outcome?
- Are feature flags, env vars, cron/config files, or task wiring updated together with code?
- Is any runtime-critical change present in code but missing from docs/config, or vice versa?
- If generated or mirrored artifacts exist, were they kept in sync?

## Phase Integrity Check
- Is there a source-of-truth document for stage names and boundaries?
- Does the review separate `microstep implemented`, `current repository integration-safe`, and `phase complete`?
- Are all mandatory subparts/source kinds of the current phase explicitly accounted for?
- Is any recommendation to move forward blocked until unfinished current-phase work is called out?
- Is the live repository/runtime judged as it exists now, not as it may look after future planned steps?

## Goal-Level Data Completeness Check
- If the business goal is migration/backfill/import, was actual target data presence checked, not only code/tests?
- Is there a source-of-truth for expected counts or reconciliation?
- Are smoke fixtures clearly separated from real historical data?
- If media transfer matters, was actual media presence checked?
- Does the review separate `code complete`, `smoke complete`, and `historical data loaded`?

## Domain Model Completeness Check
- If commands or operator flows are being migrated, what domain model stands behind them?
- Are classification fields, policy state, generator keys, mappings, and similar prerequisites present in the target system as usable data?
- Is any command being treated as implemented even though only its shell exists?

## Operator-Critical Chain Check
- If the phase gate includes a manual/control run, was the real chain checked end-to-end?
- For conditional chains, was reachability of the recovery/interactive branch proven from the preceding step?
- If component tests are green but the real control run contradicts them, does the review keep the phase at `FAIL`?
- Is live source-side or operator-side evidence treated as stronger than mocked/component-level evidence for final acceptance?

## Closure Check
- Is the phase blocked only by one last acceptance step?
- If yes, is it safe, authorized by the user, and targetable through a disposable object?
- If yes, has the execution plan shifted from "manual later" to "run now and capture evidence"?

## Date/Time Critical Check
- For any `raw SQL -> date field -> now comparison` path, are types normalized and guarded before comparison?
