# tsk-1528 production release plan

## Scope

- LMS: exam progress API/service, two additive Alembic migrations, OpenAPI, tests.
- SPW: student exam map/run, teacher review queue, generated types and required runtime helpers.
- ContentBackbone: reviewed KIM 25208834 import, create-only tasks, immutable exam variant, scale 0–29.
- Explicitly excluded: all unrelated local WIP, tsk-1536/1545 changes beyond already deployed origin HEAD.

## Verified preflight

- Production LMS/SPW healthy; LMS 200, SPW 307 auth redirect.
- Backup log 2026-11-18 completed successfully.
- Prod Alembic head `tsk1536_methodist_readiness`; exam tables absent.
- Course 112 `wp:ege-informatika` active; only canonical 17869/17870 exist for reuse.
- Feature flag `EXAM_PROGRESS_ENABLED` absent/false on prod.
- Clean worktrees based on origin avoid local WIP. 14 LMS tests + mypy, 10 CB tests, 3 SPW tests + type-check PASS.

## Deploy sequence

1. Independent review of staged LMS/SPW/CB diffs; FAIL blocks release.
2. Commit only staged exam scope and push main/master.
3. Server deploy via `/opt/lms/deploy/vps/deploy.sh`; inspect actual deployed commits and Alembic head `tsk1538_exam_group_score`; health 200.
4. Server deploy via `/opt/spw/deploy/vps/deploy.sh`; fresh BUILD_ID, chunk guard, health and student UI smoke.
5. Prod dry-run: compare 28 creates + exactly 2 reuse links. Prod content import with reviewed artifact, `--target prod --prod-confirm KIM_25208834_PROD`, create-only, live reuse verification; save apply-result with created IDs immediately on response and verify exactly 28 creates + 2 reuse. On interruption rerun dry-run and reconcile existing UIDs before retry.
6. Verify all 14 media via public HTTPS and correct Content-Type before pilot.
7. Publish immutable variant through reviewed/hash-gated publisher with `--import-result` proving 28 creates + 2 reuse; verify 30 positions, 27 numbers, 30 unique tasks, weighted maximum 29. Recompute the published variant+items hash from prod DB and require exact match with the reviewed artifact before enabling the flag.
8. Only now set `EXAM_PROGRESS_ENABLED=true` with `EXAM_PROGRESS_STUDENT_IDS=142` after claim/backup of prod `.env`; restart LMS and re-check. Another student ID gets 403.
9. Limited pilot only: student 142 + teacher/admin QA. No expansion without separate operator decision.

## Rollback

- First response: `EXAM_PROGRESS_ENABLED=false`, restart LMS; keep history, no destructive migration downgrade. Student allowlist remains intact.
- Code: standard `/opt/lms/deploy/vps/rollback.sh` and SPW rollback to `.last-deploy-sha` if required. Previous production SHA was launched against migrated dev schema and returned health 200; rollback.sh performs no Alembic downgrade or head validation. After any rollback verify Alembic head and production health 200 explicitly. Keep feature flag off as primary rollback.
- Content: archive pilot variant / deactivate only the recorded newly created task IDs via service path; never overwrite reused 2091/2092. New tasks must not appear in ordinary course flows before/after rollback checks.
- Database: no production downgrade during pilot; backup from 2026-11-18 available.

## QA acceptance

- Student start, all 30 positions, №14/25 repeated correctly, №19–21 distinct with common stimulus.
- Skip→blank→required reflection, pause/resume, finish and map colors/score.
- №26 one correct answer scores 1/2; both score 2/2. Partial remains an unresolved initial wrong with preserved positive initial_score and mandatory РНО.
- End-to-end score scenarios: all correct → 29/29 and 100; all blank → 0 and 0; mixed set with manually calculated total and scale mapping.
- Repeated answer request id and concurrent finish must not duplicate results.
- Teacher queue ACL and disappearance after video hint.
- Back navigation from each screen, mobile layout, no client error.
- No duplicate results or effects on ordinary learning progress.
