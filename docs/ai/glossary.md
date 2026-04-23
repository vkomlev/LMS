# Glossary — LMS Core API

Доменные термины. Цель — однозначная трактовка в коде, документации и общении с агентами.

## Пользователи и роли

- **User** — любой человек в системе. Может иметь несколько ролей одновременно.
- **Role** — роль пользователя. Поддерживаются русские и английские имена. Известные: `student`, `teacher`, `methodist`, `admin` (и др. из таблицы `roles`).
- **Student↔Teacher link** — прикрепление ученика к преподавателю. Отдельная таблица связей.
- **Access Request** — заявка на получение роли; подтверждается методистом/админом.

## Курсы и материалы

- **Course** — курс. Поддерживает иерархию (M2M parent/child через `course_parents`) и жёсткие зависимости (`course_dependencies`).
- **Course Parent** — родительский курс; связь M2M с `order_number` для порядка.
- **Course Dependency** — зависимость «курс B требует прохождения курса A». Без самоссылок (триггер).
- **UserCourse** — привязка студента к курсу с авто-`order_number`. Нумерация обновляется триггером при удалении.
- **TeacherCourse** — привязка преподавателя к курсу. Исторически была авто-синхронизация дочерних (снята в миграции `20260127_230000`, сейчас — parent-check).
- **Material** — учебный материал курса. Типы: `text`, `video`, `link`, `pdf`, `script`, `document` (расширяются).

## Задания и проверка

- **Task** — задача (quiz). Имеет тип, solution-правила, уровень сложности.
- **Meta Task** — обёртка/группировка задач (мета-задания).
- **Attempt** — попытка решения задачи студентом. Может быть отменена (stage 3.5).
- **Task Result** — итоговый результат по задаче (агрегат попыток).
- **Hint Event** — открытие подсказки в попытке (для учёта).
- **Help Request** — запрос помощи от ученика (stage 3.8). Имеет `type` и `context` (stage 3.8.1).
- **Help Request Reply** — ответ преподавателя на Help Request.
- **Next Mode** — режим выдачи следующего задания (stage 3.9): teacher-driven / auto / by-difficulty и др.

## Инфраструктура и интеграции

- **Learning Engine** — подсистема выдачи/проверки заданий (stages 1-7 в истории миграций).
- **Import (GSheets)** — импорт курсов/материалов/задач из Google Sheets через service-account.
- **DomainError** — доменное исключение (`app/utils/exceptions.py`); всегда со `status_code`.
- **API key** — аутентификация через query-параметр `api_key`. Список валидных — в `VALID_API_KEYS`.
- **MCP PostgreSQL** — dev-инструмент для read-only SQL из AI-агентов; алиас `postgresql`.

## Гейты (процессные)

- **Spec-Gate** — фиксация scope и acceptance criteria до реализации.
- **Execution-Gate** — имплементация + minimal smoke.
- **Review-Gate** — независимое PASS/FAIL до merge в `main`.
- **Merge-Gate** — интеграция только при PASS review-gate.

## Даты и время

- **Naive datetime** — `datetime` без tzinfo; в проекте — reject или normalize до сравнения.
- **Raw SQL text()** — SQLAlchemy `text(...)` вернёт `str`, если тип колонки не явный; перед сравнением с `datetime` — обязательная нормализация.
- **SLA/TTL compare** — любое сравнение даты-времени в бизнес-логике требует explicit type-guard в сервисе.
