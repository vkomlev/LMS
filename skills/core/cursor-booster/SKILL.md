---
name: cursor-booster
description: "Design, package, and roll out Cursor capabilities using the latest platform features: plugins, subagents, skills, MCP, rules, sandbox controls, and cloud/background agents. Use when you need a concrete Cursor adoption strategy, plugin architecture, or operational setup for your project fleet."
---

# Cursor Booster

## Workflow
1. Choose mode:
- `research`
- `architecture`
- `implementation`
- `rollout`
2. Determine the source case:
- default source is the current chat if the user did not specify another;
- identify the concrete Cursor-agent mistake, expected behavior, and affected prompts/rules/commands.
3. Read:
- [references/cursor-latest-capabilities.md](references/cursor-latest-capabilities.md)
- [references/booster-packaging-patterns.md](references/booster-packaging-patterns.md)
- [references/project-integration.md](references/project-integration.md)
4. Before changing any Cursor agent:
- log the incident in the error register;
- run `5 Whys`;
- verify library/framework facts via [references/library-fact-check.md](references/library-fact-check.md) when API usage matters.
5. Produce a deterministic plan:
- target architecture or change set;
- concrete files/commands;
- security, sandbox, and rollback controls.
6. Add role-specific guards when relevant:
- real logging layout must match triage commands;
- migration/operator agents must check domain-model completeness, not only command coverage.
7. Before closing an agent-improvement task, run an anti-bloat refactor pass:
- collapse repeated incident-specific rules into compact invariants;
- prefer updating references/checklists over inflating top-level prompts/rules;
- remove wording that duplicates existing guardrails without adding new coverage.
8. Patch the responsible Cursor agent directly; if the culprit is a Codex skill, route preventive changes to `codex-booster`.

## Input Contract
- `Source Case`
- `Mode`
- `Target Projects`
- `Objective`
- `Constraints`

## Output Contract
- `Capability Map`
- `5 Whys`
- `Recommended Architecture`
- `Implementation Steps`
- `Config/Files to Create`
- `Risk and Security Controls`
- `Validation and Success Criteria`
- `Rollback Plan`
- `Fact-Check Evidence`
- `Register Update`
- `Target Cursor Agent to Improve`

## Quality Rules
- Always attach dates to "latest" capability claims.
- Prefer official Cursor sources.
- Distinguish GA from preview features.
- Never close an improvement task without: register entry, `5 Whys`, culprit patch, and validation.
- Prefer compact invariant-based rules over long lists of incident-specific exceptions.
- Treat agent-improvement work as incomplete if instruction growth was left unnormalized after the fix.
