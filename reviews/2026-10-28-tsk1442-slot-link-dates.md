# tsk-1442 — даты действия у привязки ученика к слоту

## Контекст
Ученик 4623, начисление 207 за октябрь: 600 ₽ вместо 3000 ₽. 28.10 перевод со
слота 33 (чт) на слот 36 (сб): старая привязка выключена целиком, новая жила с
`lesson_slot.active_from` = 31.08 → октябрь посчитан по субботам, 4 «пропавших».

## Решение
- `lesson_slot_student.active_from` / `active_until` (date, МСК, включительно).
- Снятие со слота (`_detach_student_from_slot`: удаление, перевод, смена сетки
  через `schedule_plan_service`) закрывает привязку вчерашним днём.
- Прикрепление (`_attach_student_to_slot`) всегда заводит новую строку с
  `active_from` = сегодня, не оживляет закрытую. Уникальность пары — частичный
  индекс по действующим.
- Расчёт (`charge_service.lesson_counts_for_month`): привязка с датой конца
  остаётся в расчёте до этой даты; начало привязки срезает только дни после
  прихода ученика (иначе новичок теряет долю).
- Миграция `tsk1442_slot_student_dates`: колонки, индекс, даты для двух строк
  4623 (231 по 27.10, 332 с 28.10). Остальным NULL — поведение прежнее.

## Места записи привязок (снято из кода)
- `lesson_calendar_service.create_lesson_slot` — создание слота с учениками: дат
  не ставит (слот новый, его собственные даты ограничивают).
- `_attach_student_to_slot` ← `add_slot_participant`, `transfer_slot_participant`,
  `schedule_plan_service` (применение сетки), `schedule_booking_service`.
- `_detach_student_from_slot` ← `remove_slot_participant`, `transfer_slot_participant`,
  `schedule_plan_service`.
- `graduation_service._detach_from_schedule` — уход из школы: не тронут
  сознательно, уход учитывает `left_on` / `after_leave` (tsk-804), суммы
  уходящих заморожены.
- `user_merge_service` — перенос строк при слиянии учёток, дат не меняет.

## Не тронуто
`pricing_service.weekly_lessons` (частота на 1-е число) читает только
действующие привязки — как раньше.

Дифф: `2026-10-28-tsk1442-slot-link-dates.diff`.
