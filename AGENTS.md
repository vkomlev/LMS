# AGENTS.md instructions for d:\Work\LMS

<INSTRUCTIONS>
## Skills
A skill is a set of local instructions to follow that is stored in a `SKILL.md` file. Below is the list of skills that can be used in this project.
### Available skills
- ai-orchestrator: Orchestrate multi-agent execution across the booster setup using three work contours and three AI tiers (L/M/H), with project-aware routing, gates, and handoff artifacts. Use when planning or coordinating end-to-end task delivery across Cursor agents, Codex, and Claude. (file: d:/Work/LMS/skills/core/ai-orchestrator/SKILL.md)
- architect-system-analyst: Combine solution architecture and system analysis for small local projects: clarify business goals, keep architecture intentionally lightweight, formalize only essential contracts, and produce an implementation-ready plan with pragmatic risk controls. (file: d:/Work/LMS/skills/core/architect-system-analyst/SKILL.md)
- ceo-review: Legacy alias for product-level plan review. Prefer `product-review` for new work; use `ceo-review` only when an older workflow or a legacy prompt explicitly calls for it. (file: d:/Work/LMS/skills/core/ceo-review/SKILL.md)
- change-plan-architect: Сформировать план внедрения изменения после анализа текущего кода, контрактов, зависимостей и недостающих ресурсов. Использовать для задач средней сложности: поиск блокеров, выявление пробелов в API/контрактах и безопасная поэтапная поставка. (file: d:/Work/LMS/skills/core/change-plan-architect/SKILL.md)
- codex-booster: Operate and improve Codex within the Cursor-based booster environment: select Codex role usage, apply local skills, manage rollout to projects, and enforce answer/code quality loops. Use when configuring, auditing, or scaling Codex workflows across your project fleet. (file: d:/Work/LMS/skills/core/codex-booster/SKILL.md)
- cursor-booster: Design, package, and roll out Cursor capabilities using the latest platform features: plugins, subagents, skills, MCP, rules, sandbox controls, and cloud/background agents. Use when you need a concrete Cursor adoption strategy, plugin architecture, or operational setup for your project fleet. (file: d:/Work/LMS/skills/core/cursor-booster/SKILL.md)
- db-check: Validate database assumptions, schema state, and data invariants safely through read-only checks before and after implementation. Use for migrations, data-sensitive features, and incident triage. (file: d:/Work/LMS/skills/core/db-check/SKILL.md)
- encoding-guard: Prevent and fix text encoding issues (UTF-8 corruption, mojibake, mixed encodings) in markdown, docs, review artifacts, config, and rule files. Use before and after editing text files, especially when working through PowerShell, git diff pipelines, or bulk file updates. (file: d:/Work/LMS/skills/core/encoding-guard/SKILL.md)
- eng-review: Review an implementation plan from an engineering lead perspective before coding starts. Use to pressure-test architecture, trust boundaries, test strategy, rollback, and delivery shape without drifting into implementation. (file: d:/Work/LMS/skills/core/eng-review/SKILL.md)
- executor-lite: Execute low-risk, routine implementation tasks with strict scope control and minimal token usage. Use for deterministic edits, repetitive transformations, formatting, boilerplate, and simple test scaffolding. (file: d:/Work/LMS/skills/core/executor-lite/SKILL.md)
- fastapi-api-developer: Implement and debug FastAPI backend changes with PostgreSQL MCP-aware analysis, schema-aware SQL checks, and log-driven diagnosis. Use for API feature delivery, bugfixes, DB-impacting changes, and smoke-debug loops in LMS-style Python services. (file: d:/Work/LMS/skills/core/fastapi-api-developer/SKILL.md)
- lms-fastapi-techlead-code-reviewer: Perform a strict FastAPI-focused technical lead review for LMS production readiness with PASS/FAIL. Use for API/backend changes, especially date/time logic, raw SQL paths, migration safety, and runtime endpoint behavior. (file: d:/Work/LMS/skills/core/lms-fastapi-techlead-code-reviewer/SKILL.md)
- pipeline-operator: Run and supervise repeatable content/data pipelines with explicit preflight checks, exit-code handling, and artifact reporting. Use for scheduled or trigger-based automation flows, including OpenClaw-driven runs. (file: d:/Work/LMS/skills/core/pipeline-operator/SKILL.md)
- pr-review: | (file: d:/Work/LMS/skills/core/pr-review/SKILL.md)
- product-review: Review a plan from a product perspective before implementation. Use to test whether the scope, user value, acceptance path, and tradeoffs are strong enough to justify the work. (file: d:/Work/LMS/skills/core/product-review/SKILL.md)
- qa-fix: Reproduce QA issues, apply the smallest safe fixes, and verify the result. Use after `qa-report` or when the user explicitly wants a QA-driven fix loop with before/after evidence. (file: d:/Work/LMS/skills/core/qa-fix/SKILL.md)
- qa-report: Produce a reproducible QA report without applying fixes. Use to verify user-visible behavior, operator flows, regression risks, and release readiness before deciding whether to fix, escalate, or gate. (file: d:/Work/LMS/skills/core/qa-report/SKILL.md)
- release-prep: Prepare a release go/no-go package with preflight checks, validation evidence, rollback readiness, and post-release follow-ups. Use before release, risky deployment, or final merge to main/master when operator confidence matters. (file: d:/Work/LMS/skills/core/release-prep/SKILL.md)
- response-quality-coach: Audit and improve AI text responses using a structured feedback loop with defect classification, root-cause analysis, and concrete instruction updates. Use when a chat reply is weak, unclear, wrong, overly verbose, poorly structured, or when a skill output needs quality calibration. (file: d:/Work/LMS/skills/core/response-quality-coach/SKILL.md)
- retro: | (file: d:/Work/LMS/skills/core/retro/SKILL.md)
- review-gate: Perform an independent pre-merge quality gate with a strict PASS or FAIL decision, prioritized findings, and required fixes. Use before merge, release, or any high-risk deployment. (file: d:/Work/LMS/skills/core/review-gate/SKILL.md)
- ship: | (file: d:/Work/LMS/skills/core/ship/SKILL.md)
- spec-writer: Convert ambiguous requests into an implementation-ready specification with scope, constraints, risks, acceptance criteria, and execution checkpoints. Use when a task is unclear, under-specified, or likely to cause rework without a clear plan. (file: d:/Work/LMS/skills/core/spec-writer/SKILL.md)
- tech-spec-composer: Сформировать техническое задание, готовое к исполнению Tier L: с явным контекстом, ограничениями, правилами стека, обязательными скиллами/инструментами, критериями приемки и артефактами передачи/ревью. Использовать при постановке задач для Cursor-агентов и Codex-исполнителей (API, боты, парсеры, публикация). (file: d:/Work/LMS/skills/core/tech-spec-composer/SKILL.md)
- techlead-code-reviewer: Perform a strict technical lead code review for production readiness with a PASS/FAIL decision. Use before integration to main/master, release candidate approval, risky refactors, schema migrations, and any change where correctness, architecture integrity, and reliability are critical. (file: d:/Work/LMS/skills/core/techlead-code-reviewer/SKILL.md)
- telegram-ux-flow-designer: Design minimal, intuitive Telegram bot UX flows optimized for aiogram and aiogram-dialog, reducing screens, clicks, and cognitive load while preserving clarity and recoverability. Use when creating or redesigning bot dialogs, menus, button maps, and conversation states. (file: d:/Work/LMS/skills/core/telegram-ux-flow-designer/SKILL.md)
### How to use skills
- Discovery: The list above is the skills available in this session (name + description + file path). Skill bodies live on disk at the listed paths.
- Trigger rules: If the user names a skill (with `$SkillName` or plain text) OR the task clearly matches a skill's description shown above, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
- Missing/blocked: If a named skill isn't in the list or the path can't be read, say so briefly and continue with the best fallback.
- How to use a skill (progressive disclosure):
  1) After deciding to use a skill, open its `SKILL.md`. Read only enough to follow the workflow.
  2) When `SKILL.md` references relative paths (e.g., `scripts/foo.py`), resolve them relative to the skill directory listed above first, and only consider other paths if needed.
  3) If `SKILL.md` points to extra folders such as `references/`, load only the specific files needed for the request; don't bulk-load everything.
  4) If `scripts/` exist, prefer running or patching them instead of retyping large code blocks.
  5) If `assets/` or templates exist, reuse them instead of recreating from scratch.
- Coordination and sequencing:
  - If multiple skills apply, choose the minimal set that covers the request and state the order you'll use them.
  - Announce which skill(s) you're using and why (one short line). If you skip an obvious skill, say why.
- Context hygiene:
  - Keep context small: summarize long sections instead of pasting them; only load extra files when needed.
  - Avoid deep reference-chasing: prefer opening only files directly linked from `SKILL.md` unless you're blocked.
- Safety and fallback: If a skill can't be applied cleanly (missing files, unclear instructions), state the issue, pick the next-best approach, and continue.

</INSTRUCTIONS>
