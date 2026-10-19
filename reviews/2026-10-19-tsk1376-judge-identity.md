# tsk-1376 (часть 2) — судья принимал вердикты от подмен

## Находка
`client.complete` (нестриминговый путь: code_review, rubric_review, text_authorship, черновики критериев/подсказок/отзывов, chain_check) вызывал `check_response_id` только внутри ветки `anthropic-direct`. Через CloseRouter подлинность не проверялась, id ответа не журналировался.

Прод 12–19.10 (`llm_usage_event`, purpose=code_review): gemini-3.6 ok 244, gpt-5.6-sol ok 60, отказов `LLMIdentityMismatch` — 0.

Проба 19.10 без потока, по 4 вызова:

| Звено | id | входных токенов | вывод |
|---|---|---|---|
| gemini-3.7-flash | base64url | 17 | настоящая |
| gemini-3.6-flash | `msg_<hex>` | 270 | подмена |
| gpt-5.6-luna | `chatcmpl-<цифры>` | 44 | настоящая |
| gpt-5.6-sol | `chatcmpl-msg_01…` | 2093 | подмена (Claude) |

## Изменения
- `client.py` — `check_response_id` для всех провайдеров в `complete`; `response_id` в meta учёта.
- `providers.py` — судья по умолчанию `gemini-3.7-flash, gpt-5.6-luna`.
- Прод `.env`: `LLM_JUDGE_MODELS` тем же значением (захват, бэкап).

## model-routing
Новых моделей нет. Обе оставшиеся проверены на двух образцах в tsk-573; стенд 19.10 (`docs/qa/2026-10-19-180624-llm-judge-bakeoff-partial.md`): gemini-3.7 3/3, 3,2/8,6 c; luna 3/3, 6,6/69 c. gpt-5.5 — 3/3, 19,7/29,4 c, не взята (нужны второй образец и второй час).

## Тесты
`tests/test_tsk1376_tutor_silence.py`: подмены в батче уводят к следующей модели; родной id Gemini проходит. LLM и все потребители судьи: 390 passed.
