# Context And Routing

Use this reference before turning a vague request into a spec.

## Context Anchors

Collect only durable context that changes the task:

- project memory or existing `AGENTS.md` guidance;
- recent related specs, plans, reviews, or error-register entries;
- active cross-project contracts when the task touches ContentBackbone, LMS, SPW, or TG_LMS;
- user decisions already made in the current conversation.

Every anchor must be reflected in the spec or explicitly excluded with a reason.

## Cross-Project Memory

For CB / LMS / SPW / TG_LMS work, check:

- `D:/Work/ContentBackbone/docs/cross-project/STATE.md`
- `D:/Work/ContentBackbone/docs/cross-project/CHANGELOG.md`
- relevant files under `D:/Work/ContentBackbone/docs/cross-project/contracts/`

If those files do not exist, state that the cross-project memory source is unavailable and continue with local evidence.

## Root Task Tracker

For implementation, rollout, release, QA, docs, infrastructure, or multi-project work that should outlive the chat, check whether a Root tracker task exists under `D:/Work/Root/tasks/`.

Create or reuse a Root task when the user explicitly asks to do tracked work or uses language such as "сделай", "реализуй", "нужно", "берем в работу", "закрывай", "задача", or "трекер".

Creating a Root task for an agreed plan is routine agent work, not operator handoff. Use `python -m orchestrator new-task --non-interactive --json --title ... --projects ...`; if you must write the body yourself, reserve the id first with `python -m orchestrator claim-id --kind=tsk`. Never compute `max + 1` from existing task files. Rebuild `_index.md` and validate the graph after task changes.

The resulting spec should reference the existing `tsk-NNN`; it should not ask the operator to create the task.

## Skill Routing

Every implementation step must name one responsible skill, for example:

- `fastapi-api-developer` for FastAPI/API/backend work;
- `telegram-ux-flow-designer` for Telegram bot flows;
- `tech-spec-composer` for executor-ready assignment packages;
- `qa-report` for report-only QA;
- `qa-fix` for QA remediation;
- `techlead-code-reviewer` or `review-gate` for production readiness;
- `codex-booster` for Codex skill/runtime/project mirror changes.

If no skill fits, use `executor-lite` for low-risk routine work or mark the step `manual` with the reason.
