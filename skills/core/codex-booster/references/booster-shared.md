# Shared Booster Invariants

Codex-adapted mirror of the proven Claude booster practices. Use this reference when behavior is cross-platform and should not live only in a platform-specific `SKILL.md`.

## Mandatory Improvement Loop

Any skill, agent, or project instruction change caused by a defect must pass this loop:

1. Log the incident in the right register:
- Codex skill defect: project-local `docs/ai/ERRORS.md` or `d:/Work/IDE_booster/Docs/ai/ERRORS.md`.
- Response quality defect: `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md`.
- Cursor agent defect: project/Cursor register, then route through `cursor-booster`.
- Claude source defect: Claude register, then import only platform-neutral invariants into Codex.
2. Run `5 Whys` until the root is `instruction gap`, `context gap`, or `execution gap`.
3. Patch the culprit runtime or source-of-truth, not only the visible mirror.
4. Verify with a deterministic check.
5. Run anti-bloat before closing.

Execution gaps are model/runtime limitations. Record them, but do not inflate skills with rules that cannot prevent the failure.

## Anti-Bloat Pass

Before closing an improvement:

1. If an existing rule covers the problem, strengthen that rule instead of adding a duplicate.
2. Put local behavior in the responsible `SKILL.md`; put cross-platform behavior in references.
3. Move checklists with 3 or more items into `references/*.md`.
4. Check neighboring skills before adding a new rule.
5. Remove obsolete wording made redundant by the new invariant.

Prefer compact invariants over incident-specific exception lists.

## Ownership And Runtime

Always classify before editing:

- `canonical source-of-truth`: usually `d:/Work/IDE_booster/skills/<skill>`.
- `codex-runtime`: `C:/Users/user/.codex/skills/<skill>`.
- `project-runtime`: `<project>/skills/core/<skill>` plus generated `<project>/AGENTS.md`.
- `external-runtime`: bundled/system skills and plugin packages; do not patch as project source.

Never patch only one copy when required mirrors exist. Compare by bytes or hash after rollout, not terminal rendering.

## Encoding Discipline

For non-ASCII docs, prompts, rules, and skills:

- Store as UTF-8 without BOM unless a runtime explicitly requires otherwise.
- Use explicit UTF-8 in PowerShell/Python reads and validation.
- Do not trust terminal mojibake as proof of file corruption.
- Fail the task if replacement `?` characters are introduced where the canonical source has Cyrillic text.

## Current Capability Claims

Any claim about latest Codex, OpenAI, Cursor, or Claude capabilities must include:

- date of the claim;
- official source when the claim affects implementation;
- GA vs preview/experimental status;
- a local fallback when the capability is unavailable.

