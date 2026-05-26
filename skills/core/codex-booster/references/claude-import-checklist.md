# Claude To Codex Import Checklist

Use this checklist when importing proven Claude practices into Codex.

## Select What To Import

- Import platform-neutral invariants: RCA, anti-bloat, ownership, encoding, routing, handoff, verification.
- Adapt platform-specific mechanics: permissions, MCP config, project memory, hooks, commands, and tool names.
- Skip Claude-only command syntax unless there is a Codex equivalent.
- Prefer references over expanding top-level `SKILL.md`.

## Compare Existing Codex Coverage

For each Claude reference or skill:

1. Identify the closest Codex skill or reference.
2. Mark overlap as `covered`, `covered but weaker`, `missing`, or `Claude-only`.
3. For `covered but weaker`, patch the existing Codex invariant.
4. For `missing`, add a compact reference and link to it from the owner skill.
5. For `Claude-only`, document the boundary instead of copying.

## Rollout

1. Add or update source under `d:/Work/IDE_booster/skills`.
2. Add managed skill metadata to `Docs/ai-booster/skill-packaging-manifest.json` when runtime/project mirrors are required.
3. Run dry-run packaging.
4. Run real packaging for selected skills.
5. Validate runtime and project mirrors.

## Stop Conditions

- Do not overwrite Codex runtime system skills.
- Do not import credentials or machine-specific Claude permissions.
- Do not leave source/runtime/project copies with different intended behavior.
- Do not treat file-open/header/row-count checks as enough for operator artifacts; preserve semantic usability checks from Claude practices.

