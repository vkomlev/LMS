# Project Integration (Current Environment)

## Canonical Project Registry
- `d:/Work/IDE_booster/Docs/ai-booster/project-registry.md`

## Fleet Sync
- Skill sync script:
  - `d:/Work/IDE_booster/scripts/sync-skills.ps1`
- Supports:
  - full sync
  - selected skills
  - dry-run

## Tier Model
- Tier L: Cursor agents
- Tier M: Codex
- Tier H: Claude

## Mandatory Operational Loops
- Coding loop:
  - per-project `docs/ai/ERRORS.md`
- Answer quality loop:
  - shared `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md`
  - process in `d:/Work/IDE_booster/Docs/ai-booster/answer-feedback-loop.md`

## Integration Checklist
1. Ensure skill exists in `d:/Work/IDE_booster/skills/<skill-name>`.
2. Sync to projects with `sync-skills.ps1`.
3. Install to local Codex skills directory if needed.
4. Run one pilot task in LMS/TG_LMS before full rollout.

