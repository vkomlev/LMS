# Observability Checks

## Logging
- Are important state transitions and failures logged?
- Are log levels appropriate (INFO/WARN/ERROR)?
- Is correlation/context included (request id, entity id, operation)?
- Are secrets/PII excluded from logs?

## Diagnostics
- Can on-call engineer identify root cause from logs?
- Are actionable messages present for expected failure modes?

## Metrics/Monitoring (if applicable)
- Are critical counters/latencies/error rates observable?
- Are alert-worthy conditions identifiable?
