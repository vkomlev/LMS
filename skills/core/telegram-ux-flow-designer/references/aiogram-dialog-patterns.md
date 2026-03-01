# aiogram + aiogram-dialog Patterns

## Keep Flows Flat
- Prefer fewer windows with dynamic sections over deep nested menus.
- Keep primary user action visible without extra navigation.

## Button Strategy
- One primary action, optional secondary action, one clear back action.
- Use stable callback payloads and avoid overloaded callback semantics.

## State Management
- Keep state identifiers semantic and feature-scoped.
- Store only minimal transient UI state in dialog context.
- Keep business data in service layer, not inside dialog-only state.

## Data Loading
- Use explicit loading placeholders for remote/API operations.
- Render empty state with a next action, not only informational text.
- Handle stale data with refresh/retry entry points.

## Error Recovery
- Show short actionable message.
- Offer retry and safe back navigation.
- Avoid forcing restart from `/start` for recoverable failures.

## Observability
- Emit events for: entry, successful completion, cancellation, and errors.
- Track drop-off points by state/window ID.

