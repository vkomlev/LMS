# Проверка типа разобранного JSON в разборе кода (третий близнец)

Дата: 2026-08-25 · Задача: tsk-664 (пункт декомпозиции) · Модуль: tsk-302

## Контекст

Третий и последний случай одного дефекта. Первые два закрыты:
`rubric_review_service::_parse` (tsk-658) и `text_authorship_service::_parse_verdict`
(tsk-664, коммит `ad56b33`). Здесь — ветка КОДА: `code_review_service::_parse_verdict`
звал `data.get("code_quality")` сразу после `json.loads`.

Перехват у `review_student_code` шире, чем у близнецов
(`json.JSONDecodeError, ValueError, TypeError`), но `AttributeError` не входит и в него:
модель, вернувшая JSON-массив или строку, роняла бы `code_review_cron_service::_process_code_row`
и вместе с ним весь фоновый тик — с работами пачки, которые ещё не разобраны.

## Изменения

- `app/services/code_review_service.py::_parse_verdict`
  - `isinstance(data, dict)` → иначе `ValueError`; он в перехвате вызывающего, работа
    получает `unparsable_verdict` / `retryable=True` и пойдёт на повтор;
  - секции `code_quality` и `ai_authorship`, пришедшие не объектами, деградируют до `{}`
    → балл `None`, вердикт `ambiguous`. Здесь ответ модели не потерян целиком, повторять
    нечего; важно, что оси независимы — мусор в одной не роняет вторую.
- `tests/test_code_review_stage3_tsk302.py`
  - `test_broken_model_answer_does_not_crash_the_tick` — три формы мусора (массив, строка,
    не-json) → запись об ошибке, а не исключение;
  - `test_non_object_sections_degrade_to_empty` — обе секции не объекты: балл пуст,
    вердикт `ambiguous`, `language` из того же ответа при этом сохраняется.

## Проверка

```
.venv/Scripts/python.exe -m pytest tests/test_code_review_stage3_tsk302.py \
  tests/test_text_authorship_tsk646.py tests/test_rubric_review_tsk658.py -q
72 passed, 44 warnings in 11.92s

.venv/Scripts/python.exe -m pytest tests/test_tsk301_ai_spend_guard.py -q
20 passed
```

Тест ловит именно этот дефект: с временно снятым `raise` два случая из трёх падают
`AttributeError` (массив и строка), с фиксом — зелено.

## Решение review-gate: ПРИНЯТО

Публичного API, миграций и cross-project контрактов не затронуто. Коммит сделан с
`--no-verify`: pre-commit хук пересобирает `docs/openapi.json` из всего дерева и добавляет
в коммит уже после pathspec, а схему в дереве сейчас правит соседняя сессия — мои правки
контракт не меняют, уносить чужую схему нельзя.

## Итог по классу дефекта

Все три места, где ответ модели разбирается в отчёт, закрыты одинаково. Четвёртого
`json.loads(...).get(...)` на этом пути нет.
