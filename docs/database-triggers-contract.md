# Контракт: Бизнес-логика на уровне БД (Database-Level Business Logic Contract)

**Версия:** 1.0  
**Дата:** 2026-01-21  
**Статус:** ОБЯЗАТЕЛЬНЫЙ К ИСПОЛНЕНИЮ

---

## ⚠️ КРИТИЧЕСКИ ВАЖНО

**ВСЯ бизнес-логика, описанная в этом документе, реализована на уровне PostgreSQL через триггеры и ограничения.**

**ЗАПРЕЩЕНО:**
- ❌ Дублировать эту логику в коде приложения (Python/FastAPI)
- ❌ Реализовывать аналогичную логику в сервисах или репозиториях
- ❌ Добавлять валидацию, которая конфликтует с триггерами БД
- ❌ Обходить триггеры через прямые SQL-запросы без учета их логики

**РАЗРЕШЕНО:**
- ✅ Использовать триггеры БД как единственный источник истины
- ✅ Обрабатывать исключения от триггеров (IntegrityError) и преобразовывать их в DomainError
- ✅ Документировать поведение триггеров в комментариях кода
- ✅ Тестировать поведение триггеров через интеграционные тесты

---

## 📋 Реализованная бизнес-логика в БД

### 1. Автоматическая нумерация `order_number` в `user_courses`

**Триггер:** `trg_set_user_course_order_number`  
**Функция:** `set_user_course_order_number()`  
**Таблица:** `user_courses`  
**События:** `BEFORE INSERT`, `BEFORE UPDATE`

#### Логика триггера:

1. **При INSERT:**
   - Если `order_number IS NULL` → автоматически устанавливается `MAX(order_number) + 1` для данного пользователя
   - Если `order_number` указан явно → существующие курсы с `order_number >= NEW.order_number` сдвигаются вправо (+1)

2. **При UPDATE:**
   - Если `order_number` изменился → автоматически пересчитываются порядковые номера остальных курсов пользователя
   - При увеличении номера → курсы между старым и новым номером сдвигаются влево (-1)
   - При уменьшении номера → курсы между новым и старым номером сдвигаются вправо (+1)
   - Если `order_number` установлен в `NULL` → автоматически ставится следующий номер

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вычислять order_number вручную
max_order = await db.execute(select(func.max(UserCourses.order_number)).where(...))
new_order = (max_order or 0) + 1

# ❌ ЗАПРЕЩЕНО: сдвигать курсы вручную при изменении order_number
await db.execute(update(UserCourses).where(...).values(order_number=order_number + 1))

# ✅ ПРАВИЛЬНО: просто передать None или явный номер, триггер сделает все сам
await db.execute(insert(UserCourses).values(user_id=1, course_id=2, order_number=None))
```

#### Обработка ошибок:

Триггер не генерирует ошибок, только автоматически устанавливает/пересчитывает значения.

---

### 2. Пересчет `order_number` после удаления курса

**Триггер:** `trg_reorder_after_delete`  
**Функция:** `reorder_after_delete()`  
**Таблица:** `user_courses`  
**События:** `AFTER DELETE`

#### Логика триггера:

- После удаления записи из `user_courses`:
  - Если у удаленной записи был `order_number IS NOT NULL`
  - Все курсы пользователя с `order_number > OLD.order_number` автоматически сдвигаются влево (-1)

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: пересчитывать order_number после удаления вручную
await db.delete(user_course)
await db.execute(update(UserCourses).where(...).values(order_number=order_number - 1))

# ✅ ПРАВИЛЬНО: просто удалить запись, триггер пересчитает автоматически
await db.delete(user_course)
await db.commit()
```

---

### 3. Автоматическая нумерация `order_number` в `course_parents`

**Триггер:** `trg_set_course_parent_order_number`  
**Функция:** `set_course_parent_order_number()`  
**Таблица:** `course_parents`  
**События:** `BEFORE INSERT`, `BEFORE UPDATE`

#### Логика триггера:

1. **При INSERT:**
   - Если `order_number IS NULL` → автоматически устанавливается `MAX(order_number) + 1` для данного родительского курса
   - Если `order_number` указан явно → существующие подкурсы с `order_number >= NEW.order_number` сдвигаются вправо (+1)

2. **При UPDATE:**
   - Если `order_number` изменился → автоматически пересчитываются порядковые номера остальных подкурсов родителя
   - При увеличении номера → подкурсы между старым и новым номером сдвигаются влево (-1)
   - При уменьшении номера → подкурсы между новым и старым номером сдвигаются вправо (+1)
   - Если `order_number` установлен в `NULL` → автоматически ставится следующий номер

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вычислять order_number вручную
max_order = await db.execute(select(func.max(t_course_parents.c.order_number)).where(...))
new_order = (max_order or 0) + 1

# ❌ ЗАПРЕЩЕНО: сдвигать подкурсы вручную при изменении order_number
await db.execute(update(t_course_parents).where(...).values(order_number=order_number + 1))

# ✅ ПРАВИЛЬНО: просто передать None или явный номер, триггер сделает все сам
await db.execute(insert(t_course_parents).values(course_id=1, parent_course_id=2, order_number=None))
```

#### Обработка ошибок:

Триггер не генерирует ошибок, только автоматически устанавливает/пересчитывает значения.

---

### 4. Пересчет `order_number` после удаления подкурса

**Триггер:** `trg_reorder_course_parents_after_delete`  
**Функция:** `reorder_course_parents_after_delete()`  
**Таблица:** `course_parents`  
**События:** `AFTER DELETE`

#### Логика триггера:

- После удаления записи из `course_parents`:
  - Если у удаленной записи был `order_number IS NOT NULL`
  - Все подкурсы родителя с `order_number > OLD.order_number` автоматически сдвигаются влево (-1)

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: пересчитывать order_number после удаления вручную
await db.delete(course_parent_link)
await db.execute(update(t_course_parents).where(...).values(order_number=order_number - 1))

# ✅ ПРАВИЛЬНО: просто удалить запись, триггер пересчитает автоматически
await db.delete(course_parent_link)
await db.commit()
```

---

### 5. Валидация циклов в иерархии курсов

**Триггер:** `trg_check_course_hierarchy_cycle`  
**Функция:** `check_course_hierarchy_cycle()`  
**Таблица:** `course_parents`  
**События:** `BEFORE INSERT`, `BEFORE UPDATE`

#### Логика триггера:

1. **Проверка самоссылки:**
   - Если `NEW.course_id = NEW.parent_course_id` → `RAISE EXCEPTION 'Course cannot be its own parent'`

2. **Проверка циклов:**
   - Рекурсивно проверяет всю цепочку предков курса через таблицу `course_parents`
   - Если курс является потомком своего потенциального родителя → `RAISE EXCEPTION 'Circular reference detected'`

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: проверять циклы вручную через рекурсивные запросы
async def check_cycle(course_id, parent_id):
    # Рекурсивная проверка...
    pass

# ❌ ЗАПРЕЩЕНО: валидировать самоссылки вручную
if course_id == parent_id:
    raise DomainError("Self-reference not allowed")

# ✅ ПРАВИЛЬНО: просто вставить/обновить связь, триггер проверит все сам
try:
    await db.execute(insert(t_course_parents).values(course_id=1, parent_course_id=2))
    await db.commit()
except IntegrityError as e:
    # Обработать ошибку от триггера
    if "Circular reference" in str(e):
        raise DomainError("Cycle detected", status_code=400)
```

#### Обработка ошибок:

Триггер генерирует `IntegrityError` с сообщениями:
- `'Course cannot be its own parent'` → преобразовать в `DomainError` с кодом 400
- `'Circular reference detected: course X cannot be a descendant of course Y'` → преобразовать в `DomainError` с кодом 400

---

### 6. Предотвращение самоссылок в зависимостях курсов

**Ограничение:** `check_no_self_dependency`  
**Таблица:** `course_dependencies`  
**Тип:** `CHECK CONSTRAINT`

#### Логика ограничения:

- Проверяет, что `course_id != required_course_id`
- При попытке создать зависимость курса от самого себя → PostgreSQL генерирует `IntegrityError`

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: проверять self-dependency вручную
if course_id == required_course_id:
    raise DomainError("Self-dependency not allowed")

# ✅ ПРАВИЛЬНО: ограничение БД само проверит
try:
    await db.execute(insert(course_dependencies).values(...))
    await db.commit()
except IntegrityError as e:
    if "check_no_self_dependency" in str(e):
        raise DomainError("Self-dependency not allowed", status_code=400)
```

#### Обработка ошибок:

PostgreSQL генерирует `IntegrityError` при нарушении ограничения. Преобразовать в `DomainError` с кодом 400.

---

## 🔒 Правила для разработчиков

### Обязательные правила:

1. **Перед добавлением валидации в код:**
   - Проверить этот документ на наличие аналогичной логики в БД
   - Если логика есть в БД → НЕ добавлять в код, использовать триггеры

2. **При работе с `order_number` в `user_courses`:**
   - Всегда можно передать `None` → триггер установит автоматически
   - Можно передать явный номер → триггер пересчитает остальные
   - НЕ нужно вычислять `order_number` вручную перед INSERT

3. **При работе с `order_number` в `course_parents`:**
   - Всегда можно передать `None` → триггер установит автоматически
   - Можно передать явный номер → триггер пересчитает остальные
   - НЕ нужно вычислять `order_number` вручную перед INSERT
   - Порядок подкурсов внутри родителя управляется автоматически

4. **При работе с иерархией курсов:**
   - НЕ нужно проверять циклы вручную
   - Просто обновить `parent_course_ids` → триггер проверит циклы
   - Обработать `IntegrityError` от триггера и преобразовать в `DomainError`

5. **При работе с зависимостями курсов:**
   - НЕ нужно проверять self-dependency вручную
   - Ограничение БД само проверит
   - Обработать `IntegrityError` и преобразовать в `DomainError`

### Рекомендуемые практики:

1. **Документировать использование триггеров:**
   ```python
   # Порядковый номер установится автоматически триггером БД, если не указан
   order_number: Optional[int] = None
   ```

2. **Обрабатывать ошибки от триггеров:**
   ```python
   try:
       await db.commit()
   except IntegrityError as e:
       if "Circular reference" in str(e):
           raise DomainError("Cycle detected", status_code=400)
   ```

3. **Тестировать поведение триггеров:**
   - Использовать интеграционные тесты, которые проверяют реальное поведение БД
   - Не мокировать триггеры в тестах

---

## 📝 Список всех триггеров и ограничений

| Триггер/Ограничение | Таблица | Событие | Функция | Описание |
|---------------------|---------|---------|---------|----------|
| `trg_set_user_course_order_number` | `user_courses` | BEFORE INSERT/UPDATE | `set_user_course_order_number()` | Автоматическая нумерация и пересчет order_number |
| `trg_reorder_after_delete` | `user_courses` | AFTER DELETE | `reorder_after_delete()` | Пересчет order_number после удаления |
| `trg_check_course_hierarchy_cycle` | `courses` | BEFORE INSERT/UPDATE | `check_course_hierarchy_cycle()` | Проверка циклов в иерархии курсов |
| `check_no_self_dependency` | `course_dependencies` | CHECK CONSTRAINT | - | Предотвращение самоссылок в зависимостях |
| `trg_set_course_parent_order_number` | `course_parents` | BEFORE INSERT/UPDATE | `set_course_parent_order_number()` | Автоматическая нумерация и пересчет order_number для подкурсов |
| `trg_reorder_course_parents_after_delete` | `course_parents` | AFTER DELETE | `reorder_course_parents_after_delete()` | Пересчет order_number после удаления подкурса |
| `trg_check_teacher_course_no_parents` | `teacher_courses` | BEFORE INSERT | `check_course_has_no_parents()` | Проверка, что курс не имеет родителей перед привязкой преподавателя |
| `trg_check_user_course_no_parents` | `user_courses` | BEFORE INSERT | `check_user_course_has_no_parents()` | Проверка, что курс не имеет родителей перед привязкой студента |
| `trg_set_material_order_position` | `materials` | BEFORE INSERT/UPDATE | `set_material_order_position()` | Автоматическая нумерация и пересчет order_position материалов курса |
| `trg_reorder_materials_after_delete` | `materials` | AFTER DELETE | `reorder_materials_after_delete()` | Пересчет order_position после удаления материала |
| `trg_material_updated_at` | `materials` | BEFORE UPDATE | `set_material_updated_at()` | Обновление поля updated_at при изменении записи |
| `trg_set_task_order_position` | `tasks` | BEFORE INSERT/UPDATE | `set_task_order_position()` | Автоматическая нумерация и пересчет order_position заданий курса |
| `trg_reorder_tasks_after_delete` | `tasks` | AFTER DELETE | `reorder_tasks_after_delete()` | Пересчет order_position после удаления задания (statement-level) |

---

### 7. Автоматическая нумерация `order_position` в `materials`

**Триггер:** `trg_set_material_order_position`  
**Функция:** `set_material_order_position()`  
**Таблица:** `materials`  
**События:** `BEFORE INSERT`, `BEFORE UPDATE`

#### Логика триггера:

1. **При INSERT:**
   - Если `order_position IS NULL` → автоматически устанавливается `MAX(order_position) + 1` для данного курса
   - Если `order_position` указан явно → существующие материалы курса с `order_position >= NEW.order_position` сдвигаются вправо (+1)

2. **При UPDATE:**
   - Если `order_position` изменился → автоматически пересчитываются порядковые номера остальных материалов курса
   - При увеличении номера → материалы между старым и новым номером сдвигаются влево (-1)
   - При уменьшении номера → материалы между новым и старым номером сдвигаются вправо (+1)
   - Если `order_position` установлен в `NULL` → автоматически ставится следующий номер

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вычислять order_position вручную
max_order = await db.execute(select(func.max(Materials.order_position)).where(Materials.course_id == course_id))
new_order = (max_order or 0) + 1

# ✅ ПРАВИЛЬНО: передать None или явный номер, триггер сделает все сам
await materials_repo.create(db, course_id=1, title="...", type="link", content={...}, order_position=None)
```

---

### 8. Пересчет `order_position` материалов после удаления

**Триггер:** `trg_reorder_materials_after_delete`  
**Функция:** `reorder_materials_after_delete()`  
**Таблица:** `materials`  
**События:** `AFTER DELETE`

#### Логика триггера:

- После удаления записи из `materials`: если у удаленной записи был `order_position IS NOT NULL`, все материалы этого курса с `order_position > OLD.order_position` сдвигаются влево (-1).

---

### 9. Автоматическая привязка детей при привязке родителя к преподавателю

**Триггер:** `trg_auto_link_teacher_course_children`  
**Функция:** `auto_link_teacher_course_children()`  
**Таблица:** `teacher_courses`  
**События:** `AFTER INSERT`

#### Логика триггера:

1. При привязке преподавателя к курсу (INSERT в `teacher_courses`):
   - Автоматически находятся все потомки курса рекурсивно (с ограничением глубины 20 уровней)
   - Автоматически создаются связи преподавателя со всеми потомками
   - Используется `ON CONFLICT DO NOTHING` для идемпотентности

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вручную привязывать детей при привязке родителя
await teacher_courses_repo.add(db, teacher_id=16, course_id=1)
# Вручную находить и привязывать детей
children = await courses_repo.get_all_children(db, course_id=1)
for child in children:
    await teacher_courses_repo.add(db, teacher_id=16, course_id=child.id)

# ✅ ПРАВИЛЬНО: просто привязать родителя, триггер привяжет детей автоматически
await teacher_courses_repo.add(db, teacher_id=16, course_id=1)
```

#### Обработка ошибок:

Триггер не генерирует ошибок, только автоматически создает связи.

---

### 10. Автоматическая отвязка детей при отвязке родителя от преподавателя

**Триггер:** `trg_auto_unlink_teacher_course_children`  
**Функция:** `auto_unlink_teacher_course_children()`  
**Таблица:** `teacher_courses`  
**События:** `AFTER DELETE`

#### Логика триггера:

1. При отвязке преподавателя от курса (DELETE из `teacher_courses`):
   - Автоматически находятся все потомки курса рекурсивно (с ограничением глубины 20 уровней)
   - Автоматически удаляются связи преподавателя со всеми потомками

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вручную отвязывать детей при отвязке родителя
await teacher_courses_repo.remove(db, teacher_id=16, course_id=1)
# Вручную находить и отвязывать детей
children = await courses_repo.get_all_children(db, course_id=1)
for child in children:
    await teacher_courses_repo.remove(db, teacher_id=16, course_id=child.id)

# ✅ ПРАВИЛЬНО: просто отвязать родителя, триггер отвяжет детей автоматически
await teacher_courses_repo.remove(db, teacher_id=16, course_id=1)
```

#### Обработка ошибок:

Триггер не генерирует ошибок, только автоматически удаляет связи.

---

### 11. Синхронизация при добавлении ребенка в иерархию

**Триггер:** `trg_sync_teacher_courses_on_child_added`  
**Функция:** `sync_teacher_courses_on_child_added()`  
**Таблица:** `course_parents`  
**События:** `AFTER INSERT`

#### Логика триггера:

1. При добавлении ребенка к родителю (INSERT в `course_parents`):
   - Находятся все преподаватели, привязанные к родительскому курсу
   - Автоматически создаются связи этих преподавателей с новым ребенком
   - Автоматически создаются связи со всеми потомками нового ребенка (рекурсивно)

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вручную синхронизировать связи при добавлении ребенка
await courses_repo.add_child(db, course_id=10, parent_id=1)
# Вручную находить преподавателей и привязывать к ребенку
teachers = await teacher_courses_repo.get_teachers_by_course(db, course_id=1)
for teacher_id in teachers:
    await teacher_courses_repo.add(db, teacher_id=teacher_id, course_id=10)

# ✅ ПРАВИЛЬНО: просто добавить ребенка, триггер синхронизирует связи автоматически
await courses_repo.add_child(db, course_id=10, parent_id=1)
```

#### Обработка ошибок:

Триггер не генерирует ошибок, только автоматически создает связи.

---

### 12. Синхронизация при удалении ребенка из иерархии

**⚠️ ТЕХНИЧЕСКИЙ ДОЛГ:** Эта логика реализована в коде, а не в триггере БД.

**Причина:** PostgreSQL не позволяет изменять таблицу `teacher_courses` в AFTER DELETE триггере на `course_parents`, если `teacher_courses` используется в запросе триггера (ошибка `TriggeredDataChangeViolationError`).

**Реализация:** `TeacherCoursesRepository.sync_on_child_removed()`

#### Логика:

1. При удалении ребенка из иерархии (DELETE из `course_parents`):
   - Находятся все преподаватели, привязанные к удаляемому родителю
   - Проверяется, есть ли у курса другие родители
   - Если есть другие родители - проверяется, привязаны ли преподаватели к ним
   - Удаляются связи для преподавателей, которые не привязаны к другим родителям
   - Удаляются связи для всех потомков удаляемого ребенка

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: игнорировать синхронизацию при удалении ребенка
await courses_repo.remove_child(db, course_id=10, parent_id=1)
# Связи преподавателей останутся некорректными

# ✅ ПРАВИЛЬНО: вызвать метод синхронизации после удаления
await courses_repo.remove_child(db, course_id=10, parent_id=1)
teacher_courses_repo = TeacherCoursesRepository()
await teacher_courses_repo.sync_on_child_removed(db, removed_course_id=10, removed_parent_id=1)
```

**⚠️ ВАЖНО:** Этот метод должен вызываться в сервисе при удалении связи в `course_parents`.  
**⚠️ ПЛАН:** В будущем можно рассмотреть использование DEFERRED триггеров или других подходов для переноса логики обратно в БД.

---

### 13. Автоматическая нумерация `order_position` в `tasks`

**Триггер:** `trg_set_task_order_position`  
**Функция:** `set_task_order_position()`  
**Таблица:** `tasks`  
**События:** `BEFORE INSERT`, `BEFORE UPDATE`

#### Логика триггера:

1. **При INSERT:**
   - Если `order_position IS NULL` → автоматически устанавливается `MAX(order_position) + 1` для данного курса
   - Если `order_position` указан явно → существующие задания курса с `order_position >= NEW.order_position` сдвигаются вправо (+1)

2. **При UPDATE:**
   - Если `order_position` изменился → автоматически пересчитываются порядковые номера остальных заданий курса
   - При увеличении номера → задания между старым и новым номером сдвигаются влево (-1)
   - При уменьшении номера → задания между новым и старым номером сдвигаются вправо (+1)
   - Если `order_position` установлен в `NULL` → автоматически ставится следующий номер

Триггер использует session-var `app.skip_task_order_trigger` (`is_local=true`) для безопасной
рекурсии при пересчёте соседей. Значение существует только в текущей транзакции.

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: вычислять order_position вручную
max_order = await db.execute(
    select(func.max(Tasks.order_position)).where(Tasks.course_id == course_id)
)
new_order = (max_order or 0) + 1

# ✅ ПРАВИЛЬНО: передать None или явный номер, триггер сделает всё сам
await tasks_repo.create(db, course_id=1, task_content={...}, order_position=None)
```

#### ⚠️ ОТЛИЧИЕ ОТ MATERIALS

`tasks.external_uid` имеет глобальный `UNIQUE` (не `UNIQUE(course_id, external_uid)`
как у `materials`). На работу триггеров `order_position` это не влияет — партиция
по `course_id`, — но при bulk-импорте по `external_uid` нужно учитывать кросс-курсовую
уникальность: один и тот же `external_uid` не может существовать в двух разных курсах.

---

### 14. Пересчет `order_position` заданий после удаления

**Триггер:** `trg_reorder_tasks_after_delete`  
**Функция:** `reorder_tasks_after_delete()`  
**Таблица:** `tasks`  
**События:** `AFTER DELETE`  
**Уровень:** `FOR EACH STATEMENT` с `REFERENCING OLD TABLE AS old_rows`

#### Логика триггера:

- После DELETE из `tasks` функция выполняет полный пересчёт `order_position`
  для всех затронутых `course_id`:
  ```sql
  ROW_NUMBER() OVER (
      PARTITION BY course_id
      ORDER BY order_position NULLS LAST, id
  )
  ```
- Statement-level + transition table нужны, чтобы избежать
  `TriggeredDataChangeViolationError` при multi-row DELETE
  (исторический баг материалов, см. fix `20260205_140000_fix_materials_delete_trigger.py`).

#### Что НЕ нужно делать в коде:

```python
# ❌ ЗАПРЕЩЕНО: пересчитывать order_position после удаления вручную
await db.delete(task)
await db.execute(
    update(Tasks)
    .where(Tasks.course_id == task.course_id, Tasks.order_position > task.order_position)
    .values(order_position=Tasks.order_position - 1)
)

# ✅ ПРАВИЛЬНО: просто удалить запись, триггер пересчитает автоматически
await db.delete(task)
await db.commit()
```

#### Рекомендация для bulk-импорта

При массовых `INSERT`'ах с явным возрастающим `order_position` каждая строка
триггерит `UPDATE` сдвига всех `>= NEW.order_position` — это `O(N²)` UPDATE'ов
внутри транзакции. Для bulk-импорта (Google Sheets / `POST /tasks/bulk-upsert`)
рекомендуется передавать `order_position=NULL` — триггер расставит позиции
по порядку INSERT'ов через `MAX+1`. Это сохраняет порядок строк импорта,
если они приходят в желаемой последовательности.

---

### 15. Bulk reorder для `tasks` через session-variable `app.skip_task_order_trigger`

**Endpoint:** `POST /api/v1/courses/{course_id}/tasks/reorder`
**Сервис:** `TasksService.reorder_tasks`
**Репозиторий:** `TasksRepository.reorder_tasks`
**Зеркало:** `MaterialsService.reorder_materials` / `MaterialsRepository.reorder_materials`.

#### Логика паттерна

```sql
-- В одной транзакции:
SELECT set_config('app.skip_task_order_trigger', 'true', true);  -- is_local=true
UPDATE tasks SET order_position = :pos1 WHERE id = :tid1 AND course_id = :cid;
UPDATE tasks SET order_position = :pos2 WHERE id = :tid2 AND course_id = :cid;
-- ...
COMMIT;  -- session-var сбрасывается автоматически (is_local=true)
```

Триггер `trg_set_task_order_position` имеет условие:
```sql
WHEN (current_setting('app.skip_task_order_trigger', true) IS DISTINCT FROM 'true')
```
— пока флаг `'true'`, BEFORE INSERT/UPDATE триггер пропускается, и наши явные
позиции попадают в БД без каскадного пересдвига соседей.

#### Гарантии

- **Атомарность:** все `UPDATE`'ы и `commit` в одной транзакции; при сбое
  никакая часть нового порядка не применяется (rollback).
- **Изоляция transaction-scoped:** третий параметр `set_config(..., true)` —
  `is_local=true`, значение видно только в текущей транзакции и сбрасывается
  по `COMMIT`/`ROLLBACK`. Не утекает в другие сессии (тест F5 в
  `tests/test_tasks_order_position.py`).
- **Триггер активен после commit:** следующий обычный `INSERT`/`UPDATE`
  снова идёт через `set_task_order_position` (тест BR6).

#### Валидация в сервисе (расширение относительно materials)

| Код | Условие |
|---|---|
| 404 | `course_id` не существует |
| 422 | Дубликат `task_id` в теле запроса |
| 422 | Дубликат `order_position` в теле запроса |
| 400 | `task_id` не принадлежит указанному `course_id` |

#### Partial reorder

Допускается отправлять порядок только для подмножества заданий курса:
не перечисленные задания сохраняют свои текущие позиции. Это **сознательное**
поведение для drag-list UI методиста. Возможны коллизии позиций между
«переданными» и «непереданными» задачами — клиент отвечает за консистентность.

#### Что НЕ нужно делать в коде

```python
# ❌ ЗАПРЕЩЕНО: отключать триггер через ALTER TABLE
await db.execute(text("ALTER TABLE tasks DISABLE TRIGGER trg_set_task_order_position"))
# ... UPDATE'ы ...
await db.execute(text("ALTER TABLE tasks ENABLE TRIGGER trg_set_task_order_position"))

# ❌ ЗАПРЕЩЕНО: эмулировать reorder через PATCH в цикле без транзакции
for item in new_order:
    await db.execute(update(Tasks).where(Tasks.id == item["task_id"]).values(...))
    await db.commit()  # промежуточный commit — клиент в случае обрыва увидит частичный порядок

# ✅ ПРАВИЛЬНО: использовать TasksRepository.reorder_tasks
return await self.repo.reorder_tasks(db, course_id, task_orders)
```

---

## 🔍 Как проверить наличие триггеров

```sql
-- Список всех триггеров
SELECT 
    trigger_name, 
    event_object_table, 
    action_statement,
    action_timing,
    event_manipulation
FROM information_schema.triggers 
WHERE trigger_schema = 'public'
ORDER BY event_object_table, trigger_name;

-- Список всех CHECK ограничений
SELECT 
    constraint_name,
    table_name,
    check_clause
FROM information_schema.check_constraints
WHERE constraint_schema = 'public';
```

---

## 📚 Связанные документы

- [Миграция триггеров](../app/db/migrations/versions/20250101_000000_add_courses_triggers.py)
- [Smoke тесты триггеров](../tests/test_triggers_smoke.py)
- [API документация курсов](courses-api.md)

---

## ⚖️ Ответственность

**Нарушение этого контракта может привести к:**
- Дублированию бизнес-логики
- Конфликтам между логикой БД и приложения
- Непредсказуемому поведению системы
- Сложностям в поддержке и отладке

**При обнаружении нарушения:**
1. Исправить код, удалив дублирующую логику
2. Обновить этот документ, если логика изменилась
3. Добавить тесты, проверяющие поведение триггеров

---

**Последнее обновление:** 2026-01-27  
**Ответственный:** Команда разработки LMS

---

## 📝 История изменений

### 2026-05-21: Добавлены триггеры order_position для tasks (зеркало materials)

**Добавлено:**
- Колонка `tasks.order_position INTEGER NULL` + индекс `idx_tasks_course_order`.
- Триггер `trg_set_task_order_position` (BEFORE INSERT/UPDATE, FOR EACH ROW).
- Триггер `trg_reorder_tasks_after_delete` (AFTER DELETE, FOR EACH STATEMENT с REFERENCING OLD TABLE — учитываем урок materials).
- Разделы 13-14 этого документа.
- Бекфилл существующих ~567 строк через `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)` — порядок Learning Engine не меняется.

**Authority:** `app/db/migrations/versions/20260521_120000_tasks_order_position_triggers.py`, бриф `docs/briefs/tsk-004-tasks-order-position.md`.

### 2026-01-27: Упрощение логики работы с триггерами

**Удалено:**
- Триггеры автоматической привязки/отвязки детей (`trg_auto_link_teacher_course_children`, `trg_auto_unlink_teacher_course_children`)
- Триггер синхронизации при добавлении ребенка (`trg_sync_teacher_courses_on_child_added`)
- Метод `sync_on_child_removed` из `TeacherCoursesRepository`

**Добавлено:**
- Триггеры проверки отсутствия родителей перед привязкой (`trg_check_teacher_course_no_parents`, `trg_check_user_course_no_parents`)

**Новые правила:**
- Привязка преподавателей и студентов возможна только к курсам без родителей (корневым курсам)
- Автоматическая синхронизация при изменении иерархии курсов больше не выполняется
