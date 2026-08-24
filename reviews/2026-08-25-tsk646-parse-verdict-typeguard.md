# Проверка типа разобранного JSON в признаке ИИ-авторства текста

Дата: 2026-08-25 · Задача: tsk-646 (модуль), образец фикса — tsk-658

## Контекст

`app/services/text_authorship_service.py::_parse_verdict` звал `data.get("ai_authorship")`
сразу после `json.loads`, не проверив, что разобрался именно объект. Модель, вернувшая
JSON-массив или строку (`[{"verdict": "ambiguous"}]`), давала `AttributeError`. Вызывающий
`review_student_text` ловит только `(ValueError, TypeError)` — значит исключение улетало в
`code_review_cron_service::_process_text_row` и роняло **весь фоновый тик** вместе с работами
пачки, которые ещё не разобраны.

Тот же класс дефекта закрыт сутками раньше в `rubric_review_service::_parse` (tsk-658).
Здесь сделано по его образцу.

## Изменения

- `app/services/text_authorship_service.py`
  - `isinstance(data, dict)` → иначе `ValueError` (попадает в перехват вызывающего,
    работа помечается `unparsable_verdict` / `retryable=True` и пойдёт на повтор);
  - вложенная секция `ai_authorship`, пришедшая не объектом, мягко деградирует до `{}`
    → вердикт `ambiguous`. Ответ модели тут не потерян целиком, повторять нечего,
    а обвинение из мусора выдумывать нельзя.
- `tests/test_text_authorship_tsk646.py`
  - `test_broken_model_answer_does_not_crash_the_tick` — три формы мусора (массив, строка,
    не-json): запись об ошибке со `status`/`retryable`, а не исключение; следы вставки
    (`signals`) при этом доезжают до преподавателя;
  - `test_non_object_authorship_section_degrades_to_ambiguous` — секция не объект → `ambiguous`.

## Проверка

```
.venv/Scripts/python.exe -m pytest tests/test_text_authorship_tsk646.py tests/test_rubric_review_tsk658.py -q
33 passed, 44 warnings in 14.31s
```

Доказательство, что тест ловит именно этот дефект: с временно снятым `raise` два из трёх
случаев падают (`AttributeError` на массиве и на строке), с фиксом — зелено.

## Решение review-gate: ПРИНЯТО

Публичного API, миграций и cross-project контрактов не затронуто — синхронизация
`docs/cross-project/` не требуется. Дата/время не участвуют. Охват правки = охват задачи.

## Follow-up (за пределами охвата)

`app/services/code_review_service.py::_parse_verdict` (строки 507–510, tsk-302) содержит
**ровно тот же дефект**: `data.get("code_quality")` сразу после `json.loads`. Перехват у
`review_student_code` шире (`json.JSONDecodeError, ValueError, TypeError`), но `AttributeError`
в него тоже не входит — массив от модели уронит тот же фоновый тик на ветке кода.
Правка не сделана: вне охвата этой задачи.
