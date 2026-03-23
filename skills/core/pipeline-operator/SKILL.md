---
name: pipeline-operator
description: Run and supervise repeatable content/data pipelines with explicit preflight checks, exit-code handling, and artifact reporting. Use for scheduled or trigger-based automation flows, including OpenClaw-driven runs.
---

# Pipeline Operator

## Workflow
1. Validate inputs and required secrets.
2. Run preflight checks (config, paths, connectivity, dry-run if available).
3. Execute pipeline steps in defined order.
4. Capture exit codes and key logs per step.
5. Generate final run report with status and next actions.

## Failure Handling
- On partial failure, mark run as degraded and continue only safe steps.
- On fatal failure, stop chain and emit actionable diagnostics.
- Never hide non-zero exit codes.

## Output Contract
- `Run ID`
- `Executed Steps`
- `Exit Codes`
- `Produced Artifacts`
- `Final Status` (success, degraded, failed)
- `Next Actions`
