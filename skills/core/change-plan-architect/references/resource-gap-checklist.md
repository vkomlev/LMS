# Resource Gap Checklist

## Contract Gaps
- Missing or vague API endpoint contract
- Undefined request/response fields
- Missing error code and retry behavior
- Missing timezone/date format convention

## Data Gaps
- Missing DB tables/columns/indexes
- Missing migration/rollback strategy
- Missing ownership/permission mapping
- Missing seed/test fixture strategy

## Integration Gaps
- Missing client methods or SDK wrappers
- Missing queue/topic or event schema
- Missing webhook/callback verification
- Missing rate-limit/circuit-breaker policy

## Product/UX Gaps
- Undefined user flow for happy path and failure path
- Ambiguous roles/access levels
- Undefined empty/loading/error states
- Missing copy/content rules

## Operational Gaps
- Missing observability signals (logs/metrics/alerts)
- Missing smoke/regression command set
- Missing release/rollback playbook
- Missing owner for production incidents

