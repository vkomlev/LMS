---
name: response-quality-coach
description: "Audit and improve AI text responses using a structured feedback loop with defect classification, root-cause analysis, and concrete instruction updates. Use when a chat reply is weak, unclear, wrong, overly verbose, poorly structured, or when a skill output needs quality calibration."
---

# Response Quality Coach

## Workflow
1. Determine the source case:
- default source is the current chat if no other example was provided;
- capture the concrete prompt, bad response, and expected behavior.
2. Classify defects using [references/defect-taxonomy.md](references/defect-taxonomy.md).
3. Run `5 Whys` and reduce the failure to one of three causes:
- instruction gap;
- context gap;
- execution gap.
4. Produce the corrected response with only the necessary fixes.
5. Define the durable fix using [references/durable-fix-patterns.md](references/durable-fix-patterns.md).
6. Log the case in the answer-error register.
7. Patch the responsible artifact:
- Codex skill directly if the defect came from a skill;
- Cursor agent via `cursor-booster` if the culprit is on the Cursor side.
8. Before finalizing the durable fix, run an anti-bloat refactor pass:
- collapse repeated defect-specific instructions into a smaller invariant set;
- prefer checklist/reference updates over expanding top-level skill instructions;
- remove wording that duplicates existing guidance without improving coverage.
9. End with a short verification checklist.

## Input Contract
- `Source Case`
- `Task Prompt`
- `Observed Response`
- `Expected Behavior`
- `Context Constraints` (optional)

## Output Contract
- `Defect Classes`
- `Severity`
- `5 Whys`
- `Root Cause`
- `Corrected Response`
- `Durable Instruction Fix`
- `Register Update`
- `Target Artifact to Improve`
- `Verification Checklist`

## Apply-to-Skill Mode
When calibrating another skill:
1. Use one real prompt/response pair, defaulting to the current chat if needed.
2. Compare the output against the taxonomy and the skill's intended output contract.
3. Propose exact `SKILL.md` edits.
4. Log the deficiency in the appropriate answer-error register.
5. Keep edits minimal and test with one follow-up case.

## Quality Rules
- Keep critique specific and evidence-based.
- Do not rewrite everything when one defect class explains the failure.
- Prefer compact, testable instruction fixes over long prose.
- For plans/specs/reviews, check phase integrity, domain completeness, and minimum-sufficient documentation explicitly.
- Treat calibration as incomplete if the resulting fix leaves obvious instruction bloat or duplicated guardrails in place.
