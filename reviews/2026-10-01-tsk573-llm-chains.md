# tsk-573 — смена цепочек LLM по месячному стенду 2026-10-01

Контекст: сводка `docs/qa/2026-10-01-llm-bakeoff-summary.md`, решение оператора (вариант А).

- Наставник (`providers._DEFAULT_TUTOR_MODELS`): `gpt-5.5 → claude-sonnet-4.6`; выведены `gemini-3-flash` (прод 7/29), `glm-5.3` (0/10).
- Судья (`_DEFAULT_JUDGE_MODELS`, `LLM_JUDGE_MODELS`): `gemini-3.7-flash, gemini-3.6-flash, gpt-5.6-luna, gpt-5.6-sol`; выведена `glm-5.3` (44% таймаутов).
- `.env.example`, контракт клиента §6a.

Проверки: `pytest -k "llm or provider or tutor or chain or judge"` — 175 passed.
Review-gate: ПРИНЯТО. Риск: цепочка наставника короче (2 модели), но выведенные почти не отвечали.
Diff: `reviews/2026-10-01-tsk573-llm-chains.diff`.
