---
name: context-auditor
description: "Audit whether a spec, plan, implementation, QA result, or review artifact still matches the original goals, decisions, constraints, and known prevention actions. Use when scope drift or forgotten context is plausible."
---

# Context Auditor

## Role
Context auditor: compare the current artifact against original intent and durable project context.

## When To Use
- After `spec-writer`, to ensure the spec kept user decisions and constraints.
- After `tech-spec-composer`, to ensure the assignment matches the plan.
- Before `review-gate`, to detect scope drift.
- After `qa-fix`, to ensure fixes did not move away from acceptance criteria.
- Any time the user suspects something was forgotten.

## Workflow
1. Identify the project, artifact, and pipeline stage from the request and local context.
2. Collect source-of-truth context:
- current conversation decisions;
- project `AGENTS.md`, `docs/ai/*`, `docs/ai/ERRORS.md`;
- source spec, plan, tech spec, QA report, review artifact;
- `D:/Work/ContentBackbone/docs/cross-project/*` for CB/LMS/SPW/TG_LMS work when present;
- optional read-only session history from `C:/Users/user/.codex/sessions` or `C:/Users/user/.claude/projects` only when needed.
3. Extract a requirement checklist:
- functional requirements;
- non-functional requirements;
- no-touch zones and out-of-scope items;
- architecture decisions;
- edge cases;
- known prevention actions.
4. Check the artifact and mark each item:
- `COVERED`
- `PARTIAL`
- `MISSING`
- `DEVIATED`
- `ADDED`
- `REGRESSION`
5. For every `PARTIAL`, `MISSING`, `DEVIATED`, or `REGRESSION`, cite the source and give a concrete repair direction.
6. For every `ADDED`, decide whether it is justified scope or scope creep.

## Output Contract
- `Project`
- `Artifact`
- `Pipeline Stage`
- `Context Sources`
- `Requirement Checklist`
- `Losses`
- `Scope Creep`
- `Known Error Regression Check`
- `Recommendations`
- `Verdict` (`ALIGNED`, `DRIFT DETECTED`, `CRITICAL LOSS`)

## Quality Rules
- Read-only by default; do not edit artifacts unless the user asked for fixes.
- Do not invent requirements.
- Every problematic item needs a source file, conversation anchor, or explicit local evidence.
- Do not perform code quality review; route quality defects to `techlead-code-reviewer` or `review-gate`.
- Keep covered items compact and spend detail on losses, drift, and regressions.

