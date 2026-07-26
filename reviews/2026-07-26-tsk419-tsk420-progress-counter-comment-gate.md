# tsk-419 + tsk-420: обязательный комментарий/файл SA_COM/TBL_COM + фикс счётчика прогресса

Дата: 2026-07-26. Продолжение [[tsk-414]] — решения оператора по открытым пунктам
приёмки QA курса 88.

## tsk-420: счётчик прогресса не засчитывает авто-проверенные SA_COM/TBL_COM

**Диагноз** (см. `reviews/2026-07-26-tsk414-tsk313-course88-qa-fixes.md`): `_compute_
syllabus_task_status` (`app/services/me_service.py`) требовал `checked_at IS NOT NULL`
для ЛЮБОГО `SA_COM`/`TBL_COM`/`TA` (blanket `MANUAL_REVIEW_TASK_TYPES`), не глядя на
`solution_rules.manual_review_required` — хотя этот флаг уже единственный источник
истины «нужен ли учитель» (`teacher_queue_service.mandatory_review_sql`, tsk-247).

**Решение оператора:** фиксить сейчас, исключить опциональную (manual_review_required
=false) проверку из условия зачёта.

**Фикс:** `_SYLLABUS_TASKS_SQL` — добавлена колонка `manual_review_required`
(`COALESCE((solution_rules->>'manual_review_required')::boolean, false)`).
`_compute_syllabus_task_status` — для `SA_COM`/`TBL_COM` смотрит на этот флаг вместо
blanket task_type-whitelist: `manual_review_required=false` → `passed` сразу (паритет
с `SC`/`MC`/`SA`); `TA` — по-прежнему всегда ручная (рубрики).

**Тесты:** `tests/test_tsk420_syllabus_optional_review_status.py` (6 юнит-тестов на
`_compute_syllabus_task_status`) + `tests/test_y62_syllabus_states.py` — новый
интеграционный `test_status_sa_com_optional_review_passed_no_checked` (end-to-end
через реальный API-эндпоинт), плюс исправлен existing `test_status_pending_review_
optimistic` (пикер задачи для "ручного" сценария теперь явно фильтрует `manual_review_
required=true`, иначе он мог случайно взять авто-проверяемую задачу и стать ложным
регрессом на мой же фикс).

## tsk-419: обязательный комментарий или файл для SA_COM/TBL_COM

**Контекст:** класс H из письма QA — часть SA_COM решается устно/подбором без
доказательства хода решения (id-149 «курсор→танцор»). Решение оператора: для ВСЕХ
SA_COM и TBL_COM (не per-task флаг, как `requires_attachment` в tsk-227, а
универсальное правило по типу задания) обязателен комментарий ИЛИ файл — иначе ответ
не засчитывается.

**Фикс (LMS backend):** `app/api/v1/attempts.py`, новый гейт 2.3f — после уже
существующего 2.3e (`requires_attachment`, tsk-227). Для `task_content.type in
COMMENT_TASK_TYPES` ("SA_COM", "TBL_COM") без непустого `response.comment` и без
реально загруженного файла (`_attempt_attachment_files`) — `check_result` принудительно
`score=0/is_correct=False` с сообщением «Добавьте комментарий... или приложите файл».
Гейт пропускается, если 2.3e уже отклонил ответ по требуемому вложению (не задваивает
сообщение).

**Фикс (SPW frontend):** `TaskFormSA_COM.tsx`/`TaskFormTBL_COM.tsx` — `canSubmit`
дополнен условием `commentOrAttachmentMissing` (universal, независимо от
`requiresAttachment`), `blockReason` показывает причину. Клиентский гейт — UX,
сервер (2.3f) источник истины.

**Тесты:**
- LMS: `tests/test_comment_or_attachment_gate_tsk419.py` (8 тестов: SA_COM/TBL_COM без
  comment/файла → отклонено; с comment → зачёт; с файлом → зачёт; SA не затронут;
  requires_attachment важнее при конфликте).
- SPW unit: `tests/unit/task-form-sa-com.test.tsx`, `tests/unit/task-form-tbl-com.test.tsx`,
  `tests/unit/attachment-submit-gate.test.tsx` — обновлены существующие тесты
  (заполняли только ответ, ожидая доступный submit — теперь дополнены комментарием/
  файлом либо явно проверяют новую блокировку) + новые кейсы на сам гейт.
- SPW e2e: `tests/e2e/y4-sa-com-flow.spec.ts` — обновлён (клик в CodeMirror-редактор
  комментария перед проверкой доступности submit). Не прогонялся локально (нужен
  Playwright + живой dev-сервер) — логическая корректность проверена по паттерну
  unit-тестов, полагаться на живую проверку после деплоя.
- Побочно: `tests/test_attempts_null_solution_rules_tsk325.py` — существующий тест
  `test_auto_task_with_media_empty_passes_autocheck` (не про tsk-419) добавлен comment
  в ответ, чтобы не смешивать два независимых гейта в одном тесте.

## Дополнительно (не из письма QA, из разбора навигатора)

**Перестановка видео/теории курса 108.** Выполнена ТОЛЬКО чётко описанная в письме
перестановка: материалы «Форматирование строк» (текст 264, видео 483) перенесены в
конец занятия, непосредственно перед «Модульstring» (266) —
`scripts/tsk414_reorder_material_264_483_before_string_module.py`. Две другие строчки
письма («переместить блок с видео после видео "создание строк..."», повторное
«переместить блок с видео») НЕ выполнены — они ссылаются на картинку из оригинального
письма, которая не сохранилась при извлечении текста; исполнение вслепую рискует
сделать порядок хуже. Вынесено в письмо к QA — просьба прислать скриншот повторно.

**Новые задачи, заведены по решению оператора:**
- [[tsk-417]] (backlog) — кнопка «На весь экран» для видео, общий вопрос платформы.
- [[tsk-418]] (backlog) — `requirement_level` материалов ЕГЭ/Python не соответствует
  исходным WP-иконкам (рука=обязательно/стрелка=можно пропустить) — при импорте все
  материалы стали "обязательными".

## Деплой и живая проверка

LMS: коммит применяется отдельно, деплой на прод — стандартный процесс LMS backend.
SPW: коммит + деплой (см. `deploy/vps/deploy.sh`), обязательна живая проверка после —
задача SA_COM/TBL_COM показывает блокировку без комментария/файла, счётчик прогресса
курса 90 (данные QA, задача 110) даёт 6/6.
