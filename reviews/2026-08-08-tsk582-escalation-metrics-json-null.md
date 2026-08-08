# tsk-582 — крон эскалации не видел работы с `metrics` = JSON-null

Дата: 2026-08-08 · Задача: tsk-582 (найдено при работе над tsk-396)
Артефакт diff: `reviews/2026-08-08-tsk582-escalation-metrics-json-null.diff`

## Дефект

`app/services/escalation_service.py::escalation_cron_tick` отбирал кандидатов условием

```sql
AND (tr.metrics IS NULL
     OR (jsonb_typeof(tr.metrics) = 'object' AND NOT (tr.metrics ? 'escalated_at')))
```

В `task_results.metrics` при сдаче через API ложится не SQL NULL, а JSON-null
(Pydantic-поле `metrics=None` сериализуется в json null). Для такой строки
`IS NULL` ложно, а `jsonb_typeof` даёт `'null'`. Работа не проходила ни одну
ветку и не эскалировалась методисту никогда — тихо, без ошибок в логах.
Тот же класс, что tsk-361 (`solution_rules` = JSON-null мимо `IS NULL`).

Тесты дефект не ловили: фабрика `_create_pending_tr` вставляет строку напрямую
SQL-ом, где `metrics` остаётся SQL NULL, — то есть в тестах жила форма, которой
на проде нет ни в одной строке.

## Замер на ПРОДЕ (read-only MCP `learn_prod_db`, 2026-08-08)

| форма `metrics` | всего | непроверенных |
|---|---|---|
| object | 12090 | 347 |
| json null | 1912 | 1910 |
| array (`[null, {...}]`, след скрипта tsk-210) | 8 | 8 |
| SQL NULL | 0 | 0 |

Реальных кандидатов крона по полному предикату (тип задания + `is_correct` +
таймаут 48 ч) — **268**: 23 курса, 25 учеников, самая старая с 2026-07-15,
средний возраст 9,1 дня.

## Правка

```sql
AND (
    tr.metrics IS NULL
    OR jsonb_typeof(tr.metrics) <> 'object'
    OR NOT (tr.metrics ? 'escalated_at')
)
```

Пропускаем работу, только если эскалация уже была. Ловит SQL NULL, JSON-null и
массив.

**Промежуточная ошибка, которую поймали тесты.** Первая версия правки была
краткой: `AND NOT (jsonb_typeof(tr.metrics) = 'object' AND tr.metrics ? 'escalated_at')`.
На SQL NULL это выражение даёт NULL, а не TRUE (трёхзначная логика), и отсекает
ровно те строки, ради которых правка делалась — два существующих теста Y-6
покраснели. Итоговая форма двузначна: ветка `IS NULL` стоит явно и первой.

## Тесты

- `tests/test_y6_review_loop.py::test_y6_escalation_cron_sees_any_metrics_shape`
  — новый, параметризован пятью формами `metrics`: SQL NULL, JSON-null, массив,
  пустой объект, объект без ключа.
- `tests/test_y6_review_loop.py::test_y6_escalation_cron_skips_already_escalated`
  — обратная сторона: помеченная работа второй раз не эскалируется и метка не
  перезаписывается.
- `tests/test_partial_auto_check_tsk396.py::test_pending_hybrid_work_is_escalated`
  — снят обход `metrics = '{}'`, добавлена проверка предпосылки
  `jsonb_typeof(metrics) = 'null'`; тест заодно стережёт форму metrics.

Прогон: `tests/test_y6_review_loop.py` + `tests/test_partial_auto_check_tsk396.py`
— 30 passed.

## Решение оператора по накопленной очереди

Выбран минимальный вариант: чиним предикат, накопленную очередь не трогаем.
Первый прогон после выката даст **23 курса × 2 методиста = 46 записей** в inbox
(ограничитель `METHODIST_RATE_LIMIT_PER_DAY_PER_COURSE=1`), очередь разойдётся
за 3 тика (~15 минут при `LIMIT 100` и тике раз в 5 минут).

Остальные 245 работ будут помечены `escalated_at` без уведомления
(`methodist_notify_service.py:92-100` помечает и на rate-limited пути). Из
очереди преподавателя они не пропадают — `teacher_queue_service.list_pending_reviews`
на `metrics` не смотрит; теряется только push-сигнал.

## Риски / follow-up

- Пометка `escalated_at` при rate-limit гасит работу навсегда вместо переноса
  напоминания на следующие сутки. Честная починка требует ещё и правки
  `ORDER BY submitted_at ASC LIMIT 100`: вечные кандидаты забьют окно и заслонят
  свежие работы. Зафиксировано в tsk-582 как follow-up, отдельно от этой правки.
- Прод-скрипты, дописывающие `metrics` конкатенацией без `CASE WHEN
  jsonb_typeof(metrics) = 'object'`, снова породят массивы (как tsk-210). Сервис
  уведомлений такой гард уже имеет.
