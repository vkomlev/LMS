---
name: response-quality-coach
description: Audit and improve AI text responses using a structured feedback loop with defect classification, root-cause analysis, and concrete instruction updates. Use when a chat reply is weak, unclear, wrong, overly verbose, poorly structured, or when a skill output needs quality calibration.
---

# Response Quality Coach

## Workflow
1. Capture a concrete bad-response example (prompt + response + expected).
2. Classify defects using [references/defect-taxonomy.md](references/defect-taxonomy.md).
3. Run root-cause analysis and identify instruction gap, context gap, or execution gap.
4. Produce a corrected response that fixes only the identified defects.
5. Define a durable fix using [references/durable-fix-patterns.md](references/durable-fix-patterns.md): instruction/rule/skill update that prevents recurrence.
6. Log the case in a response-error register.
7. Add a short verification checklist for future similar prompts.

## Input Contract
- `Task Prompt`
- `Observed Response`
- `Expected Behavior`
- `Context Constraints` (optional)

## Output Contract
- `Defect Classes`
- `Severity` (`S1` critical, `S2` major, `S3` minor)
- `Root Cause`
- `Corrected Response`
- `Durable Instruction Fix`
- `Verification Checklist`

## Apply-to-Skill Mode
Use this mode when calibrating another skill (for example `telegram-ux-flow-designer`):
1. Run one real task through the target skill.
2. Evaluate output against the taxonomy and output contract.
3. Propose exact SKILL.md edits (description, workflow, quality rules, output contract).
4. Keep edits minimal and test with one follow-up prompt.

## Quality Rules
- Keep critique specific and evidence-based.
- Do not rewrite everything if one defect class is enough to explain failure.
- Prefer compact corrective instructions with testable wording.
- If uncertainty remains, mark it explicitly instead of hallucinating facts.
