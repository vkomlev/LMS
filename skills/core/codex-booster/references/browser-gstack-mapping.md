# Browser and gstack Mapping

Use this map when importing Claude/gstack habits into Codex. Copy intent and workflow semantics; do not copy Claude-only command syntax, permission metadata, or credential/session handling.

## Workflow Mapping

| Claude/gstack pattern | Codex routing |
| --- | --- |
| `gstack browse` | Browser plugin for navigation/inspection; `site-researcher` for website reconnaissance. |
| `qa` | `qa-fix`; add Browser plugin when UI/browser reproduction is needed. |
| `qa-only` | `qa-report`. |
| `qa-design-review` | `qa-report` with Browser evidence; add `product-review` for UX/value judgment. |
| `plan-design-review` | `product-review` plus `eng-review`; add `architect-system-analyst` for system-wide impacts. |
| `plan-ceo-review` | `product-review`; `ceo-review` only as a legacy alias. |
| `plan-eng-review` | `eng-review`. |
| `review` | `techlead-code-reviewer`. |
| `paranoid-review` | `techlead-code-reviewer` with high-risk/paranoid posture; `review-gate` for final PASS/FAIL. |
| `ship` | `release-prep` plus `review-gate`. |
| `setup-browser-cookies` | Browser plugin/session setup; manual handoff only for credentials or MFA. |

## Rules

- Browser persistence, cookies, credentials, and MFA are runtime concerns, not skill text.
- Do not store secrets in skills, `AGENTS.md`, project docs, or generated YAML.
- Prefer `qa-report` for report-only inspection and `qa-fix` for remediation.
- Keep final release decisions in `release-prep`/`review-gate` rather than ad hoc QA notes.
- Use [browser-qa-runtime.md](/d:/Work/IDE_booster/Docs/ai-booster/browser-qa-runtime.md) for session, cookie, screenshot evidence, local smoke, and manual MFA handoff rules.
