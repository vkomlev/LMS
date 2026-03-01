---
name: telegram-ux-flow-designer
description: Design minimal, intuitive Telegram bot UX flows optimized for aiogram and aiogram-dialog, reducing screens, clicks, and cognitive load while preserving clarity and recoverability. Use when creating or redesigning bot dialogs, menus, button maps, and conversation states.
---

# Telegram UX Flow Designer

## Workflow
1. Identify primary user jobs and top-3 high-frequency actions.
2. Build minimal flow map with shortest path to value.
3. Remove redundant states, buttons, and branching.
4. Define clear labels, confirmations, and recovery actions.
5. Map flow to `aiogram-dialog` states/windows/widgets.
6. Specify backend data dependencies per state.
7. Define behavior for empty/loading/error/offline states.
8. Provide usability acceptance checks and telemetry hooks.

## Framework Guidance
Read [references/aiogram-dialog-patterns.md](references/aiogram-dialog-patterns.md) and apply only patterns relevant to the requested feature.

## Output Contract
- `Target Persona and Job`
- `Current Friction`
- `Proposed Flow (Step-by-Step)`
- `State/Window Map`
- `Button and Label Set`
- `Edge Cases and Recovery`
- `Backend Contract Needs`
- `Usability Acceptance Criteria`

## UX Rules
- Prefer one clear primary action per screen.
- Keep navigation depth shallow.
- Avoid dead-end screens; always provide recovery.
- Keep text short and action-oriented.

