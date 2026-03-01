# Log Debug Playbook

## Inputs
- Primary log file: `d:/Work/LMS/logs/app.log`
- Optional rotated logs: `app.log.1`, `app.log.2`, etc.

## Fast Triage
Run:
```powershell
python d:/Work/IDE_booster/skills/fastapi-api-developer/scripts/log_triage.py --log-file d:/Work/LMS/logs/app.log --tail 4000 --top 10
```

## What To Extract
- Error/exception lines and frequency.
- Endpoint status distribution from `uvicorn.access`.
- Repeated failing paths (4xx/5xx).
- Suspicious SQL patterns (timeouts, constraint errors, transaction rollbacks tied to failures).

## Fix Loop
1. Reproduce endpoint failure with explicit request.
2. Capture log triage summary.
3. Correlate with MCP DB check (missing rows, FK mismatch, invalid state).
4. Apply minimal fix in repo/service/api layer.
5. Re-run smoke + log triage until no new blocking errors appear.

