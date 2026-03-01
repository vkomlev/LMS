---
name: cursor-booster
description: "Design, package, and roll out Cursor capabilities using the latest platform features: plugins, subagents, skills, MCP, rules, sandbox controls, and cloud/background agents. Use when you need a concrete Cursor adoption strategy, plugin architecture, or operational setup for your project fleet."
---

# Cursor Booster

## Workflow
1. Parse request into one mode:
- `research`: map latest Cursor capabilities and constraints.
- `architecture`: design plugin/subagent/skill/MCP composition.
- `implementation`: produce concrete setup steps and file changes.
- `rollout`: stage deployment across project fleet.
2. Read [references/cursor-latest-capabilities.md](references/cursor-latest-capabilities.md) and include concrete feature-date mapping.
3. Read [references/booster-packaging-patterns.md](references/booster-packaging-patterns.md) and select packaging strategy (single plugin vs modular skills).
4. Read [references/project-integration.md](references/project-integration.md) to align with your current project registry and tier model.
5. Produce deterministic execution plan with commands and acceptance checks.
6. Add risk controls (sandbox/network/permissions) and rollback path.

## Input Contract
- `Mode` (`research|architecture|implementation|rollout`)
- `Target Projects`
- `Objective`
- `Constraints` (security, budget, timeline, autonomy level)

## Output Contract
- `Capability Map`
- `Recommended Architecture`
- `Implementation Steps`
- `Config/Files to Create`
- `Risk and Security Controls`
- `Validation and Success Criteria`
- `Rollback Plan`

## Quality Rules
- Always include feature dates for "latest" claims.
- Prefer official Cursor docs/changelog/blog as primary sources.
- Distinguish GA features from previews/experimental.
- Keep rollout incremental: pilot -> core projects -> full fleet.
