# Rollout Operations

## Preferred Path (Scripted)
Use fleet sync script with dry-run first:

```powershell
powershell -ExecutionPolicy Bypass -File d:\Work\IDE_booster\scripts\sync-skills.ps1 -DryRun
```

Sync selected skills:

```powershell
powershell -ExecutionPolicy Bypass -File d:\Work\IDE_booster\scripts\sync-skills.ps1 -SkillNames ai-orchestrator,codex-booster
```

Sync all skills:

```powershell
powershell -ExecutionPolicy Bypass -File d:\Work\IDE_booster\scripts\sync-skills.ps1
```

## Manual Path (When Needed)
1. Copy skill folder from `d:/Work/IDE_booster/skills/<skill-name>`.
2. Paste into `<project>/skills/core/`.
3. Verify `<project>/skills/core/<skill-name>/SKILL.md` exists.

## Local Codex Installation
Copy skill folders to:
- `C:/Users/user/.codex/skills/`

Then restart Codex session to refresh available skills.

## Verification Checklist
- Skill exists in booster source.
- Skill synced to project `skills/core`.
- Skill visible in local Codex skill list after restart.
- One sample prompt triggers the expected skill behavior.

