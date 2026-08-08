# tsk-032 — базовый уровень «активность между занятиями» ДО внедрения механик

**Дата снятия:** 2026-08-08
**Источник:** боевая БД `learn` (MCP `learn_prod_db`, только чтение)
**Зачем:** цель задачи — рост `between_lessons`. Опорные цифры сняты ДО внедрения,
иначе эффект нечем доказать. Тот же приём, что в tsk-577/578.

## Как считалось

Определение события «между занятиями» взято **дословно** из уже работающего
дашборда родителя (tsk-494/504), функция
`app/services/student_dashboard_service.py::_bulk_between_lessons_activity`:

- задание засчитано: `task_results.is_correct = true`, попытка не отменена
  (`attempts.cancelled_at IS NULL`), `source_system <> 'manual_teacher'`;
- материал засчитан: `student_material_progress.status = 'completed'`,
  `completed_at IS NOT NULL`, `source <> 'manual_teacher'`;
- **вычтено время урока:** событие не попадает в окно
  `[lesson_occurrence.scheduled_at; + duration_minutes]` ни одного занятия
  этого ученика;
- когорта: `user_courses.is_active = true` И `users.is_active = true` → **49 учеников**;
- границы недели — **явно `Europe/Moscow`** (у сессии боевой БД таймзона UTC+5,
  от неё границы недели поехали бы; код считает так же явно).

Точный SQL — в конце файла, повторный замер делать **им же**.

## Опорные цифры

### Недельный охват когорты

| Неделя (пн, МСК) | Активных учеников | % когорты | Ученико-дней | Дней на активного |
|---|---|---|---|---|
| 2026-06-22 | 1 | 2.0 % | 2 | 2.00 |
| 2026-06-29 | 1 | 2.0 % | 3 | 3.00 |
| 2026-07-06 | 3 | 6.1 % | 9 | 3.00 |
| 2026-07-13 | 6 | 12.2 % | 16 | 2.67 |
| 2026-07-20 | 22 | 44.9 % | 35 | 1.59 |
| 2026-07-27 | 17 | 34.7 % | 25 | 1.47 |
| 2026-08-03 | 21 | 42.9 % | 37 | 1.76 |

**Оговорка о сопоставимости.** Занятия (`lesson_occurrence`) заведены только
с 2026-07-27 (календарь запущен в июле, tsk-021/429): в неделях до этой даты
вычитать время урока было не из чего, и «между занятиями» там фактически равно
всей активности. **Строго сопоставимы только недели с 2026-07-27.**
Для сравнения «до/после» опорой берутся две последние строки таблицы.

### Опорная тройка чисел (последние 2 полные недели)

- **Охват:** 17 и 21 ученик из 49 → **35–43 % когорты** делают что-то между занятиями за неделю.
- **Глубина:** **1.5–1.8 дня** активности на активного ученика за неделю.
- **Тишина:** 14 из 49 (**29 %**) не сделали между занятиями **ничего за 4 недели**.

### Распределение за 4 недели (на 2026-08-08)

| Активных дней за 4 недели | Учеников | Доля |
|---|---|---|
| 0 | 14 | 29 % |
| 1–3 | 24 | 49 % |
| 4–7 | 9 | 18 % |
| 8+ | 2 | 4 % |

Среднее — 2.35 дня, медиана — **2 дня за 4 недели**, максимум 10.
Средний разрыв с последней активностью — **6.3 дня**.

### Серии (за 12 недель) — чем обоснован выбор механики

| Серия | Учеников достигали |
|---|---|
| 2 дня подряд | 16 |
| 3 дня подряд | 4 |
| **7 дней подряд** | **0** (максимум за всё время — 5 дней) |
| 2 недели подряд | 19 |
| 3 недели подряд | 8 |
| 4 недели подряд | 2 (максимум — 5 недель) |

**Вывод, определивший выбор.** Дневной стрик 7 дней из исходной летней рамки
не взял **ни один ученик из 49 ни разу за 12 недель**. Цель, которой никто не
достигает, работает против удержания. Недельная серия ложится на реальное
поведение: половина активных учеников уже держит 2 недели подряд, треть — 3.
Решение оператора 2026-08-08: **недельная серия + личные вехи, без соревнования**
(лидерборд отпадает отдельно — когорты курсов меньше 5 человек, tsk-504).

## Как перепроверить (тот же запрос)

```sql
WITH active_students AS (
  SELECT DISTINCT uc.user_id FROM user_courses uc
  JOIN users u ON u.id = uc.user_id AND u.is_active = true
  WHERE uc.is_active = true
),
ev AS (
  SELECT tr.user_id, (tr.submitted_at AT TIME ZONE 'Europe/Moscow')::date AS d
  FROM task_results tr
  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
  WHERE tr.is_correct = true AND tr.source_system IS DISTINCT FROM 'manual_teacher'
    AND tr.user_id IN (SELECT user_id FROM active_students)
    AND tr.submitted_at >= now() - interval '12 weeks'
    AND NOT EXISTS (
      SELECT 1 FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
      WHERE lop.student_id = tr.user_id
        AND tr.submitted_at BETWEEN lo.scheduled_at
            AND lo.scheduled_at + make_interval(mins => lo.duration_minutes))
  UNION
  SELECT smp.student_id, (smp.completed_at AT TIME ZONE 'Europe/Moscow')::date
  FROM student_material_progress smp
  WHERE smp.status = 'completed' AND smp.completed_at IS NOT NULL
    AND smp.source IS DISTINCT FROM 'manual_teacher'
    AND smp.student_id IN (SELECT user_id FROM active_students)
    AND smp.completed_at >= now() - interval '12 weeks'
    AND NOT EXISTS (
      SELECT 1 FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
      WHERE lop.student_id = smp.student_id
        AND smp.completed_at BETWEEN lo.scheduled_at
            AND lo.scheduled_at + make_interval(mins => lo.duration_minutes))
)
SELECT date_trunc('week', d)::date AS week_msk,
       count(DISTINCT user_id) AS active_students,
       count(*) AS student_days,
       round(count(*)::numeric / NULLIF(count(DISTINCT user_id),0), 2) AS days_per_active_student,
       round(100.0 * count(DISTINCT user_id) / (SELECT count(*) FROM active_students), 1) AS pct_cohort_active
FROM ev GROUP BY 1 ORDER BY 1;
```

## Состояние каркаса достижений на момент замера

`achievements` — 0 строк, `user_achievements` — 0 строк (таблицы заведены давно,
ни разу не использовались). Модель: `achievements(id, name UNIQUE, condition jsonb,
description, badge_image_url, reward_points, is_recurring)`,
`user_achievements(user_id, achievement_id) PK, earned_at, progress jsonb`.

Ограничение схемы, важное для проектирования: PK `user_achievements` —
`(user_id, achievement_id)`, то есть **одна строка на ученика на достижение**.
Повторно выдать то же достижение схема не даёт (флаг `is_recurring` в схеме есть,
но физически не поддержан). Поэтому текущая серия считается **производной**
величиной по событиям, а в `user_achievements` пишутся только **однократные вехи** —
это же снимает класс ошибок «производная строка замерла со старым значением».
