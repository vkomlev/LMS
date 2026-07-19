# Claude-Imported Spec Guards

These guards are imported from the proven Claude workflow and adapted for Codex execution.

## No Guessing

Stop and ask only when the missing information is a strategic fork, an irreversible action, or a manual/operator-only step. Otherwise inspect the repo and choose the conservative existing pattern.

Questions must include:

- context;
- concrete uncertainty;
- options A/B/C when there is a real fork;
- recommendation.

## Executor Markers

Each substantial implementation step must include:

- `Executor`: one available skill or `manual` with reason;
- `Review`: required for security, contracts, migrations, concurrency, data writes, and cross-project behavior.

Do not use generic names like `developer`, `agent`, or `engineer`.

## Acceptance Guards

Include the relevant guard when applicable:

- new dependency/config/external service: `Preflight / Deployment Checklist`;
- external API write path: gated live smoke test criterion, not mocks only;
- mutation endpoint or queue: `Concurrency & Idempotency`;
- multi-stage plan: explicit `BLOCKED_BY` table or Mermaid dependency graph;
- public frontend/backend work: separate `Frontend Routes` and `API Endpoints` tables;
- platform API flow: token/client type and method limits;
- raw SQL with window functions, gap detection, or recursive CTE: 3-row mental trace.

## Operator Artifacts

Artifacts meant for a human operator must answer one business question end to end. A file that merely opens, has headers, or has matching row counts is not enough if the operator must mentally join incompatible populations.
