# Shared Booster Invariants

Codex-adapted mirror of the proven Claude booster practices. Use this reference when behavior is cross-platform and should not live only in a platform-specific `SKILL.md`.

## Mandatory Improvement Loop

Any skill, agent, or project instruction change caused by a defect must pass this loop:

1. For any shared resource listed in `D:/Work/Root/agents/ownership.md`, apply
the cross-agent Step 0-A from `D:/Work/Root/agents/README.md` before writing:
check active claims, read the ledger, create a claim, and release it with a
ledger entry after the work.
2. Log the incident in the right register:
- Codex skill defect: project-local `docs/ai/ERRORS.md` or `d:/Work/IDE_booster/Docs/ai/ERRORS.md`.
- Response quality defect: `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md`.
- Cursor agent defect: project/Cursor register, then route through `cursor-booster`.
- Claude source defect: Claude register, then import only platform-neutral invariants into Codex.
3. Run `5 Whys` until the root is `instruction gap`, `context gap`, or `execution gap`.
4. Patch the culprit runtime or source-of-truth, not only the visible mirror.
5. Verify with a deterministic check.
6. Run anti-bloat before closing.

Execution gaps are model/runtime limitations. Record them, but do not inflate skills with rules that cannot prevent the failure.

## Anti-Bloat Pass

Before closing an improvement:

1. If an existing rule covers the problem, strengthen that rule instead of adding a duplicate.
2. Put local behavior in the responsible `SKILL.md`; put cross-platform behavior in references.
3. Move checklists with 3 or more items into `references/*.md`.
4. Check neighboring skills before adding a new rule.
5. Remove obsolete wording made redundant by the new invariant.
6. Put reusable rules in this shared root before adding any skill-local wording.

Prefer compact invariants over incident-specific exception lists.

For unattended weekly improvements, stage changes through
`scripts/weekly-skill-maintenance.py`. Never write automated improvements
directly into canonical skills. The pre-write gate must pass before rollout.

## Ownership And Runtime

Always classify before editing:

- `canonical source-of-truth`: usually `d:/Work/IDE_booster/skills/<skill>`.
- `codex-runtime`: `C:/Users/user/.codex/skills/<skill>`.
- `project-runtime`: `<project>/skills/core/<skill>` plus generated `<project>/AGENTS.md`.
- `external-runtime`: bundled/system skills and plugin packages; do not patch as project source.

Never patch only one copy when required mirrors exist. Compare by bytes or hash after rollout, not terminal rendering.

If the target appears in `D:/Work/Root/agents/ownership.md`, ownership
classification is not enough: check `_index.md` for live claims and `_ledger.md`
for recent cross-agent changes before editing.

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

## Integration Contract Drift

When a task touches a cross-project API contract, compare implementation,
spec or ADR, OpenAPI output, and caller expectations before closure. Any
changed URL, method, status code, request schema, response schema, or hardcoded
public URL requires same-task backsync or an explicit deviation note.
For bugfix or QA loops, re-run the original caller path and smoke the concrete
endpoint or schema that failed before marking the contract fixed.

## Cross-Agent Coordination

Use `D:/Work/Root/agents/` as the shared coordination registry for Claude,
Codex, and Cursor:

- `ownership.md` defines shared resources and generated mirrors.
- `_index.md` stores live claims with TTL.
- `_ledger.md` is append-only history of completed cross-agent actions.
- `handoff/` stores specs and transfer artifacts.

Before writing to any shared resource, follow `agents/README.md` Step 0-A. This
is required for skill trees, global instructions, shared registers, course
content, WP/LMS publication paths, `.mcp.json`, hooks, and any run of
`package-skills.py` that changes mirrors.

Generated mirrors are never edited directly. For Codex, edit
`D:/Work/IDE_booster/skills/` or the appropriate source document, then package
and record the consequences in `_ledger.md`.

## Cross-Agent Second Opinion

Use `D:/Work/Root/agents/second-opinion.md` for critical decisions and every
explicit operator request for a second opinion. It complements, not replaces,
the claim registry:

- claim registry = who may write which shared resource;
- second opinion = how agents keep independent judgment and merge findings.

Apply the protocol for architecture, skill/infrastructure standards, course
review before WP/LMS publication, critical refactors or migrations, irreversible
external actions, and any case where the cost of a miss is higher than a second
run.

Preserve phase-1 isolation: do not read the other agent's `claude-*`/`codex-*`,
`*-proposal.md`, or handoff artifacts before your own independent pass. After
exchange, accept stronger findings without escalating agreement; escalate only a
real unresolved disagreement to the operator. Operator screenshots, walkthroughs,
and other facts outrank agent assumptions.

## Operator Communication

Apply [operator-handoff-rules.md](operator-handoff-rules.md) in every Codex session:

- work autonomously unless operator action is truly required or explicitly requested;
- continue independent work when one step is blocked;
- when operator action is unavoidable, provide a short step-by-step instruction;
- write operator-facing messages in clear Russian, keeping literal commands, file names, program names, and error codes unchanged.

## Commit Discipline

Apply [git-commit-rules.md](/d:/Work/IDE_booster/Docs/ai-booster/git-commit-rules.md)
whenever a commit may be created, even if no execution skill was invoked.

- Use a Russian imperative subject in the form `<type>: <description>`.
- Inspect staged files and diff before committing.
- Keep one coherent change per commit; exclude secrets, temporary files, and unrelated drift.
- Run relevant checks before committing and require `review-gate PASS` before integration to `main/master`.
- Never rewrite history or force-push without explicit operator instruction.
