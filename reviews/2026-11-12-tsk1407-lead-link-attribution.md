# Review-артефакт: tsk-1407 — атрибуция при конверсии заявки + consume (12.11.2026)

## Цель

Метка источника из `leads.attribution` должна становиться `users.signup_attribution`
в момент конверсии заявки в ученика; consume magic-link должен передавать
`guest_session_id` из localStorage-зеркала (новая вкладка не теряет метку).

## Затронутые файлы и контракты

- LMS `app/services/lead_service.py` (`link_student`) — поведение: при успешной
  привязке копирует `leads.attribution` (+`guest_session_id`) в пустое
  `users.signup_attribution`, ставит `guest_session.attributed_user_id`.
  Контракт API не менялся. Два UPDATE в той же транзакции перед commit.
- LMS `tests/test_tsk1407_lead_link_attribution.py` — новый, 3 теста.
- SPW `app/(public)/auth/magic-link/consume/ConsumeClient.tsx` — payload `/verify`
  теперь `getPendingAttribution() ?? getGuestSessionId()`. Контракт API не менялся
  (поле `guest_session_id` уже было в `MagicLinkVerifyRequest`).

## Валидация (raw)

- `pytest tests/test_tsk1407_lead_link_attribution.py tests/test_tsk1407_signup_attribution.py tests/test_tsk1407_landing_bot_lead.py tests/test_tsk506_leads.py -q` → **31 passed**.
- `pytest tests/ -q -k "funnel or magnet or quiz"` → **125 passed**.
- SPW `npx tsc --noEmit` → чисто (exit 0).
- URL-guard: hardcoded URL не добавлялись (diff только SQL/TS-логика).
- Cross-repo grep на старые пути: не требуется (URL/схемы не менялись).

## Риски

- `link_student`: UPDATE users сработает только когда `signup_attribution IS NULL`
  — первое касание защищено; UPDATE guest_session — только при пустом
  `attributed_user_id`. Перезаписи чужих данных нет.
- Consume: localStorage-зеркало может теоретически принадлежать другому человеку
  на общем компьютере — тот же уровень доверия, что и раньше у sessionStorage
  pending; LMS-сторона не изменилась.

## Rollback

`git revert eca456c` (LMS), `git revert 00cd6a0` (SPW). Данные, записанные после
выката, не мигрируем обратно (поле останется — безвредно).
