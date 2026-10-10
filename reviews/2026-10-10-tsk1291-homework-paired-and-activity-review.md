# Ревью tsk-1291: сдвоенный час без явки и кольцо ДЗ-активность

Решение: ПРИНЯТО.

- `app/services/homework_service.py`: `paired_ahead` ждёт следующую пару, только если она ещё не кончилась или на ней явка; `_homework_driven_sql` исключает из `_ROOTS_BY_ACTIVITY_SQL` работу по выданному ДЗ, сделанную вне урока.
- Тесты: `test_homework_after_the_block_when_last_pair_was_missed`, `test_homework_work_does_not_pick_the_next_homework_course` — падают без правки, 123 теста сервиса ДЗ и смежных зелёные.
- API/схема/межпроектные контракты не меняются.
- Не блокирует: EXISTS на каждую сдачу в запросе активности — только для учеников вне программ, объём на ученика малый.
