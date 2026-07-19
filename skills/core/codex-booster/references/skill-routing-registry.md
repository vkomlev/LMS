# Skill Routing Registry

Use this reference when Codex needs to import or consult Claude-side automatic
skill-routing practice.

## Source

- Live registry: `C:/Users/user/.claude/hooks/skill_routing.json`.
- Owner: Claude infrastructure. Codex may read it, but must not edit it.
- Handoff: `D:/Work/Root/agents/handoff/2026-07-16-skill-gate-claude-to-codex.md`.

## Codex Contract

Claude uses the registry behind a `PreToolUse` gate. Codex currently has no
equivalent pre-write hook in its local config; `notify` runs after a turn. For
Codex, the registry is advisory route data:

1. Read the JSON when a task touches a path that may belong to a managed domain.
2. Identify the domain and the intended skill family from matching rules.
3. Engage the closest available Codex skill before editing that domain.
4. Preserve domain isolation: an engineering skill does not cover course
   content, and a content skill does not cover code or infrastructure.
5. If the JSON schema changes or the file is unavailable, fall back to
   `skill-catalog.md`, project `AGENTS.md`, and the operator-facing skill list;
   report the registry as unavailable instead of inventing a route.

## Boundaries

- Do not copy Claude hooks, settings, permissions, or marker mechanics into
  Codex verbatim.
- Do not write to `C:/Users/user/.claude/hooks/skill_routing.json` from Codex.
- Do not report enforcement on Codex unless a Codex-side mechanism was
  implemented and verified.
- Do not treat `package-skills.py` as a way to import this registry; it packages
  the Codex skill canon from `D:/Work/IDE_booster/skills`.

## Practical Mapping

- Course content and exports: prefer `methodist`, `digital-copywriter`,
  `naive-learner-review`, or `expert-course-review` according to the task.
- Course visual briefs: prefer the visual-authoring route documented by the
  live registry and project visuals policy; do not handle generated PNGs as
  completed work until they are consumed by the course.
- Code, APIs, migrations, and scripts: prefer engineering skills such as
  `executor-pro`, `fastapi-api-developer`, `db-check`, `qa-fix`, or
  `techlead-code-reviewer` according to risk.
- AI infrastructure and skill trees: prefer `codex-booster`, `encoding-guard`,
  `response-quality-coach`, `skill-creator`, or `plugin-creator` according to
  the artifact.
- Marketing client work: prefer `smm-specialist`, `digital-copywriter`, or the
  domain-specific marketing skill available in the current session.

## Future Enforcement Option

If Codex needs mechanical protection, implement it on a Codex-owned interception
point such as a pre-commit check for shared repositories. A post-turn `notify`
can warn but cannot prevent the write that already happened.
