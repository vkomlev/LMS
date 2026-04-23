# Review: токен `strip_punctuation` в `_normalize_text`

**Дата:** 2026-04-23
**Skill:** `/fastapi-api-developer`
**Tech spec:** [tech-spec-lms-strip-punctuation-v1.md](../../ContentBackbone/docs/tech-specs/tech-spec-lms-strip-punctuation-v1.md) (Фаза 0.5 Subsystem C)
**Клиент:** ContentBackbone

---

## Контекст

Добавлен токен `strip_punctuation` в `CheckingService._normalize_text`. Разблокирует серверную проверку SA/SA_COM в ContentBackbone, где эталоны вида `"Да Нет Да Да"` сравниваются со студенческими ответами `"Да, Нет. Да, да"`.

Порядок применения зафиксирован детерминированно: `trim → lower → strip_punctuation → collapse_spaces`. Токен работает через precompiled `re.compile(r"[^\w\s]", flags=re.UNICODE)` на module level.

Backward compat: старые задачи без токена работают идентично (покрыто регрессионными тестами).

---

## Изменённые файлы

| Файл | Суть правки |
|---|---|
| [app/services/checking_service.py](../app/services/checking_service.py) | `import re` + module-level `_PUNCT_RE` + ветка `strip_punctuation` в `_normalize_text` + обновлён docstring |
| [app/schemas/solution_rules.py](../app/schemas/solution_rules.py) | `ShortAnswerRules.normalization`: обновлено описание + добавлен example с новым токеном |
| [tests/test_checking_normalization.py](../tests/test_checking_normalization.py) | Новый файл, 19 регрессионных тестов (ASCII + кириллица, backward compat, edge cases, idempotency, порядок применения) |
| [docs/openapi.json](../docs/openapi.json) | Перегенерирован со снимка `/openapi.json` живого сервера; содержит новый токен в описании `ShortAnswerRules.normalization` |

---

## Результаты приёмочных критериев

| # | Критерий | Результат | Evidence |
|---|---|---|---|
| A1 | `_normalize_text` принимает `strip_punctuation` | PASS | stdout `да нет да да` для `"  Да, Нет. Да, да  "` с полным steps-списком |
| A2 | Unit-тесты зелёные | PASS (19/19) | `reviews/evidence/2026-04-23-strip-punctuation-pytest.log` |
| A3 | Backward compat: старые задачи не сломаны | PASS | Тесты `test_legacy_*` внутри нового файла + регрессия `_normalize_text` без `strip_punctuation` |
| A4 | Schema обновлена | PASS | `app/schemas/solution_rules.py` — description содержит `strip_punctuation, collapse_spaces`, в examples — полный список из 4 шагов |
| A5 | `/tasks/validate` принимает новый токен | PASS | HTTP 200. `is_valid:false` — исключительно из-за fake `course_code='SMOKE'`, не из-за токена. См. `reviews/evidence/...validate-smoke.log` |
| A6 | `/check/task` правильно применяет токен | PASS | HTTP 200, `is_correct: true`, `score: 1`, `matched_short_answer: "да нет да да"` при answer `"Да, Нет. Да, да"` vs accepted `"да нет да да"` |
| A7 | Русский коммит по формату | PENDING — ждёт явного одобрения оператора | — |
| A8 | Нет новых pip-зависимостей | PASS | Использован только stdlib `re`; `requirements*.txt` не менялись |

A1–A6, A8 выполнены. A7 — коммит оставлен на усмотрение оператора.

---

## Критическое доказательство (A6)

```
POST /api/v1/check/task
payload: SA_COM task, normalization=["trim","lower","strip_punctuation","collapse_spaces"],
         accepted="да нет да да", student_answer="Да, Нет. Да, да"
response: {"is_correct":true,"score":1,"max_score":1,
           "details":{"matched_short_answer":"да нет да да"}, ...}
```

Нормализация end-to-end через реальный API: сервер схлопнул пунктуацию + кейс + пробелы и дал корректный матч.

---

## Проверка соседних endpoint'ов (регрессия)

`/health` → 200. LMS не упал после hot-reload с новым `_PUNCT_RE`. Отдельных регрессий в других API-ручках не выявлено (изменения локальны в `CheckingService._normalize_text`).

---

## Risks и follow-ups

- **TS-R1 (аггрессивный regex для математики)** — задокументировано в спецификации: `strip_punctuation` включается только для текстовых Yes/No-ответов. На стороне ContentBackbone payload_builder не будет добавлять токен к математическим задачам.
- **openapi.json** перегенерирован из живого сервера (hot-reload подхватил изменения). Если в CI/CD есть отдельный шаг экспорта — при следующей сборке файл будет идентичен.
- **Коммит** не создан — ждёт явного одобрения оператора на коммит с русским сообщением по формату CLAUDE.md.
- **Live smoke использовал fake `course_code`** — это не мешает подтверждению A5 (schema-валидация прошла), но для полного e2e на реальной задаче оператор может прогнать `POST /tasks/validate` с существующим `course_code`.

---

## Rollback

```bash
cd d:/Work/LMS
git revert <commit_sha>
# uvicorn reload подхватит откат автоматически
```

Rollback полностью безопасен: API-контракт расширен (не изменён); задачи без `strip_punctuation` в `normalization[]` работают идентично до и после.

---

## Артефакты

- `reviews/2026-04-23-strip-punctuation-token.md` — этот файл
- `reviews/2026-04-23-strip-punctuation-token.diff` — `git diff` по коду (без openapi.json — он большой бинарно-форматный снимок)
- `reviews/evidence/2026-04-23-strip-punctuation-pytest.log` — полный лог pytest (19 PASS)
- `reviews/evidence/2026-04-23-strip-punctuation-validate-smoke.log` — stdout curl A5 + A6
- `reviews/evidence/a5_payload.json`, `a6_payload.json` — payloads для воспроизведения smoke
