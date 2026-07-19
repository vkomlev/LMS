# Root Task Tracker Guard

Use this guard when composing specs for work that should be tracked in the portfolio backlog.

## Trigger

Before finalizing a spec, decide whether the work needs a Root tracker task:

- explicit implementation/rollout request that should outlive the current chat;
- multi-step infrastructure, release, QA, docs, or cross-project work;
- user says "задача", "трекер", "берем в работу", "закрывай", or asks to connect work to Root.

Do not create Root tasks for pure questions, quick diagnosis, tiny one-turn edits, or brainstorming unless tracking is explicitly requested.

## Rule

Creating `D:\Work\Root\tasks\tsk-NNN-{slug}.md` for an agreed plan is routine agent work, not operator handoff. Do it without asking unless a strategic fork, destructive/irreversible action, or unavailable manual step blocks progress.

Use the Root allocator instead of choosing ids by eye:

- preferred path: `python -m orchestrator new-task --non-interactive --json --title ... --projects ...`;
- if the task body must be written manually, first reserve the id with `python -m orchestrator claim-id --kind=tsk`;
- never compute `max + 1` from existing `tasks/tsk-*.md`;
- include valid frontmatter and movement history;
- run `python -m orchestrator build-index`;
- run `python -m orchestrator validate`.

The spec should reference the existing `tsk-NNN`; it should not tell the operator to create the task.

## Graph Safety

- Do not modify `status` or `depends_on` based only on task body text.
- Resolve graph/status conflicts one at a time with a history note in every changed task.
- `tsk-002` is long-running until `2026-06-19`; do not mark it stuck before that date.
