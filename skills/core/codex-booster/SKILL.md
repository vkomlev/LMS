---
name: codex-booster
description: "Operate and improve Codex within the Cursor-based booster environment: select Codex role usage, apply local skills, manage rollout to projects, and enforce answer/code quality loops. Use when configuring, auditing, or scaling Codex workflows across your project fleet."
---

# Codex Booster

## Workflow
1. Choose mode:
- `use`
- `configure`
- `rollout`
- `audit`
- `optimize`
- `import-claude`
2. Determine the source case:
- default source is the current chat if the user did not name another case;
- extract the concrete incident, affected artifact, and expected behavior.
3. Load fleet context from [references/fleet-map.md](references/fleet-map.md), routing context from [references/skill-catalog.md](references/skill-catalog.md), and shared booster invariants from [references/booster-shared.md](references/booster-shared.md).
4. Before editing any skill, classify ownership and runtime:
- canonical source-of-truth;
- required mirrors/runtime copies;
- runtime class: `codex-runtime`, `project-runtime`, or `external-runtime`.
If the target is a shared resource from `D:/Work/Root/agents/ownership.md`, apply
Step 0-A from `D:/Work/Root/agents/README.md`: check claims, read the ledger,
create a claim before writing, and release it with a ledger entry after
verification.
4a. Run the second-opinion gate for critical work:
- apply `D:/Work/Root/agents/second-opinion.md` for architecture, skill or
  infrastructure standards, course review before WP/LMS publication, critical
  refactors/migrations, irreversible external actions, or any explicit operator
  request;
- preserve phase-1 isolation: do not read the other agent's `claude-*`/`codex-*`,
  `*-proposal.md`, or handoff artifacts before your independent pass;
- after exchange, import stronger findings without escalating agreement; escalate
  only real unresolved disagreement to the operator-arbiter.
5. Run the mandatory improvement loop:
- log the incident in the error register;
- run `5 Whys`;
- patch the responsible Codex skill directly;
- if the culprit is a Cursor agent, hand off the preventive fix to `cursor-booster`.
6. When improving any skill or agent, run an anti-bloat refactor pass before closing:
- collapse repeated incident-specific rules into the smallest reusable invariant set;
- move detailed checks to existing references/checklists when possible instead of growing `SKILL.md`;
- remove or merge wording that duplicates already-covered guardrails.
7. Run packaging and placement checks when skill files are touched:
- YAML/frontmatter parse;
- `quick_validate.py`;
- `agents/openai.yaml` consistency if UI visibility matters;
- correct runtime placement in `~/.codex/skills` when Codex visibility is required.
8. When Codex/Claude "disappear" in Cursor, check runtime before blaming install or skills:
- verify the extensions are installed and activate from Cursor logs;
- inspect workspace-local `state.vscdb` under `%APPDATA%/Cursor/User/workspaceStorage/*` for hidden view/container state such as `workbench.view.extension.codexViewContainer.state` and `workbench.view.extension.claude-sidebar.state`;
- inspect `renderer.log` for extension/container compatibility signals such as missing API proposals or messages that `codexSecondaryViewContainer` / `claude-sidebar-secondary` do not exist;
- if the logs show secondary-sidebar containers are missing, treat it as Cursor/extension compatibility drift and prefer a backed-up manifest workaround instead of changing project skills;
- treat missing buttons as a workspace UI-state incident first if extensions are present;
- back up the affected `state.vscdb` before any repair and prefer a deterministic script such as `scripts/repair-cursor-ai-views.py`.
9. When Codex in Cursor fails on send/create-task with `no-client-found` on Windows:
- inspect `Codex.log` for `local-environments is not supported in the extension`, `client-status-changed`, and `Initialize received`;
- run `wsl.exe --status` and `wsl.exe --list --verbose`;
- if `wsl.exe` exists but no distro is installed, treat it as missing local execution environment rather than a project/skill failure;
- prefer fixing WSL/local environment readiness before changing skills, prompts, or project files.
10. Run mandatory encoding discipline for non-ASCII skill/docs work, especially on Windows/PowerShell:
- force explicit UTF-8 in shell-driven read/write/validation paths;
- when using Python from CLI, set `PYTHONUTF8=1`;
- do not trust terminal rendering alone for Cyrillic or other non-ASCII text;
- if output shows mojibake or replacement characters, verify the file bytes directly before deciding whether the file is actually broken.
11. Run post-edit parity and validation:
- canonical source matches intended mirrors;
- `~/.codex/skills/**/SKILL.md` parses cleanly;
- impacted project-local copies parse cleanly with runtime-appropriate checks;
- tolerate UTF-8 BOM during validation instead of reporting false frontmatter failures.
12. For non-ASCII files touched through CLI or rollout, run a byte-level encoding check:
- confirm UTF-8 bytes survived the operation;
- compare canonical vs mirror copies by hash/bytes, not only terminal text;
- fail the task if replacement `?` characters were introduced where the canonical source contains Cyrillic text.
13. Enforce strict UTF-8 as the default storage format for skill, rule, prompt, markdown, and similar text artifacts:
- prefer UTF-8 without BOM unless a runtime explicitly requires BOM;
- do not keep `cp1251` or other legacy encodings as a convenience workaround;
- if a legacy encoded file is touched, normalize it to UTF-8 unless a documented runtime constraint forbids that conversion.
14. In `optimize` mode:
- load the current skill/agent instructions;
- identify duplicated, overly specific, or incident-bound wording;
- compress them into the smallest reusable invariant set that preserves behavior;
- prefer shorter `SKILL.md` plus stronger references/checklists over instruction sprawl.
15. In `import-claude` mode:
- use [references/claude-import-checklist.md](references/claude-import-checklist.md);
- compare live Claude coverage from `C:/Users/user/.claude` against source and Codex runtime before editing;
- import platform-neutral invariants and adapt platform-specific mechanics through [references/codex-project-binding.md](references/codex-project-binding.md);
- update source-of-truth first, then package runtime and project mirrors.
16. For rollout, use [references/rollout-ops.md](references/rollout-ops.md) and do dry-run first.
17. For project-bound generated assets and handoffs, reconcile every requested asset before closure:
`requested -> generated -> saved in workspace -> inserted or explicitly delegated/rejected/blocked -> published/verified when in scope`.
Do not report asset work as complete while approved generated files remain orphaned on disk with no consuming reference or explicit owner.
18. Finish with deterministic commands, expected artifacts, and verification.
19. For unattended weekly skill improvements, use [references/booster-shared.md](references/booster-shared.md) and stage edits through `scripts/weekly-skill-maintenance.py`; never write automated improvements directly into canonical skills.

## Input Contract
- `Mode`
- `Project` or `Fleet Scope`
- `Objective`
- `Constraints`

## Output Contract
- `Selected Mode`
- `Source Case`
- `Tier and Skill Routing`
- `5 Whys`
- `Execution Steps`
- `Commands`
- `Expected Artifacts`
- `Verification`
- `Runtime Classification`
- `Source of Truth and Mirrors`
- `Skill Packaging Checks`
- `Runtime Placement Check`
- `Runtime Boundary Check`
- `Cross-Agent Claim Check`
- `Second-Opinion Gate`
- `Mirror Parity Check`
- `Claude Practice Import Map`
- `Post-Edit Skill Index Check`
- `Cursor Error Loop Actions`
- `Register Update`
- `Target Codex Skill to Improve`
- `Follow-up Improvements`

## Quality Rules
- Never close an improvement task without: register entry, `5 Whys`, culprit patch, and verification.
- Never patch only one copy when the same skill has required mirrors.
- Never edit a shared resource listed in `D:/Work/Root/agents/ownership.md` without checking live claims and recording your own claim/ledger entry.
- Never skip `D:/Work/Root/agents/second-opinion.md` for critical decisions or explicit operator second-opinion requests; never read the other agent's isolated artifacts before your own independent pass.
- Never apply Codex packaging assumptions to project/external runtimes without evidence or explicit user request.
- Treat fleet updates as incomplete until affected project mirrors were checked or synchronized.
- Prefer compact, reusable guardrails over incident-specific rule accretion.
- Treat skill/agent improvement as incomplete if repeated rules were not normalized and unnecessary instruction growth was left in place.
- Treat any CLI-driven non-ASCII file update as incomplete until encoding was validated by explicit UTF-8 and byte-level parity checks.
- Treat "missing Codex/Claude buttons" as unresolved until extension activation and workspace `state.vscdb` view visibility were both checked.
- Treat UTF-8 as the required steady-state encoding for touched text artifacts unless a documented runtime constraint says otherwise.
- Treat project-bound generated assets as unresolved until every generated file is consumed by the project, explicitly delegated, rejected, or blocked; a PNG on disk is not a completed visual.
- In `optimize` mode, prefer behavioral equivalence with fewer instructions over verbatim preservation of duplicated wording.
- In `import-claude` mode, import invariants and verified workflow patterns; do not copy Claude-only permissions, command syntax, hooks, or runtime assumptions without a Codex mapping.
