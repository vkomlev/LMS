# Skill Catalog (Current Booster)

This catalog tracks the managed Codex Booster skill set and nearby runtime capabilities. For the source of truth used by packaging, see `Docs/ai-booster/skill-packaging-manifest.json`.

## Orchestration and Planning
- `ai-orchestrator`: route work across contours, tiers, and gates.
- `architect-system-analyst`: architecture + system analysis blueprint with contracts, risks, and phased rollout.
- `spec-writer`: produce implementation-ready specs.
- `change-plan-architect`: phased plan + dependency/resource gaps.
- `tech-spec-composer`: assignment package for Tier L execution.
- `context-auditor`: check whether plans, specs, implementation, QA, or review artifacts still match original goals and constraints.

## Execution and Review
- `executor-pro`: execute approved higher-risk implementation plans with gates, rollback notes, validation, and review handoff.
- `review-gate`: independent PASS/FAIL quality gate.
- `techlead-code-reviewer`: strict production-readiness review with architecture/migration/testing/security checks.
- `qa-report`: report-only QA pass with reproducible evidence and no fixes.
- `qa-fix`: reproduce QA issues, apply the smallest safe fix, and verify before/after evidence.
- `release-prep`: release go/no-go package with preflight, validation, rollback, and post-release follow-ups.
- `db-check`: DB-centric validation and safety checks.
- `encoding-guard`: UTF-8 checks for docs/reviews before write.

## Product, Engineering, and Domain Specialists
- `product-review`: product-level plan review for value, scope, adoption, and acceptance.
- `eng-review`: engineering lead plan review for architecture, trust boundaries, tests, rollback, and delivery shape.
- `ceo-review`: legacy alias for product-level review; prefer `product-review` for new work.
- `fastapi-api-developer`: FastAPI backend execution with MCP and log-debug loop.
- `project-docs`: two-layer documentation updates for AI-facing `AGENTS.md`/`docs/ai` and human README/docs.
- `site-researcher`: website structure, robots/sitemap, DOM, SEO, competitor, and likely API endpoint research.

## Quality Improvement Loops
- `response-quality-coach`: improve answer quality and skill outputs.
- `codex-booster`: import Claude practices into Codex, manage Codex runtime/project mirrors, and run skill improvement loops.
- `session-digest`: summarize Codex or Claude session history for retrospectives and handoffs.
- Coding loop register: `docs/ai/ERRORS.md` (per project).
- Answer loop register: `d:/Work/IDE_booster/Docs/ai/ANSWER_ERRORS.md`.

## Content and Education Layer
- `digital-copywriter`: Russian digital content for TG, VK, site, email, and reality/process formats.
- `smm-specialist`: TG/VK strategy, content calendars, warmup chains, and attraction tactics.
- `methodist`: IT course modules, learning plans, assignments, rubrics, and LMS/WP export-ready projections.
- `travel-copywriter`: Russian travel content for posts, blogs, guides, and itinerary-style materials.

## Nearby Non-Managed Skills
- `cursor-booster`: Cursor plugin/subagent/skill/MCP adoption and rollout.
- `telegram-ux-flow-designer`: minimal aiogram/aiogram-dialog UX flows.
- `executor-lite`: low-risk deterministic implementation where available.

## Claude Practice Import
- Use `codex-booster import-claude` for verified Claude practices that should become Codex source, runtime, or project behavior.
- Use [skill-standard.md](skill-standard.md) for Codex skill shape; do not copy Claude-only `allowed-tools`.
- Use [codex-project-binding.md](codex-project-binding.md) for project setup, trust, MCP, runtime mirrors, and `AGENTS.md` mapping.
- Use [browser-gstack-mapping.md](browser-gstack-mapping.md) for Claude/gstack workflow names that map to Codex Browser, QA, review, and release skills.
- Use [browser-qa-runtime.md](/d:/Work/IDE_booster/Docs/ai-booster/browser-qa-runtime.md) for Browser plugin QA sessions, cookie/session handling, screenshot evidence, local app smoke, and manual MFA handoff.
