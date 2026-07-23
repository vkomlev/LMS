from __future__ import annotations

from typing import Optional, Any, Dict, List, Sequence, Set, Tuple
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Tasks
from app.repos.tasks_repo import TasksRepository
from app.services.base import BaseService
from app.utils.exceptions import DomainError
from app.services.difficulty_levels_service import DifficultyLevelsService
from app.services.courses_service import CoursesService
from app.schemas.task_content import TaskContent
from app.schemas.solution_rules import SolutionRules

# tsk-347: сложность HARD и префикс course_uid подкурса сложных заданий.
# Подкурс заводится один на номерной курс ЕГЭ: 'lms:tsk347:hard:<course_id>'.
HARD_DIFFICULTY_ID = 4
HARD_TWIN_UID_PREFIX = "lms:tsk347:hard:"


class TasksService(BaseService[Tasks]):
    """
    Сервис для работы с заданиями.

    Базовый CRUD (create/get/update/delete/list/paginate) реализован
    в BaseService[Tasks]. Здесь добавляем доменные методы, связанные
    с импортом и внешним идентификатором.
    """

    def __init__(self, repo: TasksRepository = TasksRepository()) -> None:
        """
        Инициализирует сервис с репозиторием заданий.
        """
        super().__init__(repo)

    def _validate_task_data(
        self,
        task_content: Any,
        solution_rules: Any | None,
    ) -> Tuple[TaskContent, SolutionRules]:
        """
        Валидирует task_content и solution_rules, проверяя их соответствие.
        
        Args:
            task_content: Содержимое задачи (dict или TaskContent).
            solution_rules: Правила проверки (dict или SolutionRules).
            
        Returns:
            Кортеж (TaskContent, SolutionRules) - валидированные схемы.
            
        Raises:
            DomainError: При ошибках валидации.
        """
        try:
            # Валидация task_content
            if isinstance(task_content, dict):
                task_content_obj = TaskContent.model_validate(task_content)
            elif isinstance(task_content, TaskContent):
                task_content_obj = task_content
            else:
                raise DomainError(
                    detail="task_content должен быть словарем или объектом TaskContent",
                    status_code=400,
                )
            
            # Валидация solution_rules
            if solution_rules is None:
                raise DomainError(
                    detail="solution_rules обязателен для создания задачи",
                    status_code=400,
                )
            
            if isinstance(solution_rules, dict):
                solution_rules_obj = SolutionRules.model_validate(solution_rules)
            elif isinstance(solution_rules, SolutionRules):
                solution_rules_obj = solution_rules
            else:
                raise DomainError(
                    detail="solution_rules должен быть словарем или объектом SolutionRules",
                    status_code=400,
                )
            
            # Валидация соответствия correct_options и options[].id
            solution_rules_obj.validate_with_task_content(task_content_obj)
            
            return task_content_obj, solution_rules_obj
            
        except ValueError as e:
            raise DomainError(
                detail=f"Ошибка валидации данных задачи: {str(e)}",
                status_code=400,
            ) from e

    def _sync_max_score(
        self,
        obj_in: Dict[str, Any],
        solution_rules: SolutionRules,
    ) -> Dict[str, Any]:
        """
        Синхронизирует max_score из solution_rules в tasks.max_score.
        
        Если max_score не указан в obj_in, берется из solution_rules.
        Если указан в обоих местах, проверяется соответствие.
        
        Args:
            obj_in: Словарь данных для создания/обновления задачи.
            solution_rules: Валидированные правила проверки.
            
        Returns:
            Обновленный obj_in с синхронизированным max_score.
            
        Raises:
            DomainError: При несоответствии max_score.
        """
        solution_max_score = solution_rules.max_score
        task_max_score = obj_in.get("max_score")
        
        if task_max_score is None:
            # Если max_score не указан в задаче, берем из solution_rules
            obj_in["max_score"] = solution_max_score
        elif task_max_score != solution_max_score:
            # Если указаны оба, но не совпадают - ошибка
            raise DomainError(
                detail=(
                    f"max_score не совпадает: tasks.max_score={task_max_score}, "
                    f"solution_rules.max_score={solution_max_score}. "
                    f"Значения должны быть одинаковыми."
                ),
                status_code=400,
            )
        
        return obj_in

    async def create(
        self, db: AsyncSession, obj_in: Dict[str, Any]
    ) -> Tasks:
        """
        Создать задачу с валидацией task_content и solution_rules.
        
        Переопределяет базовый метод для добавления валидации и синхронизации max_score.
        """
        task_content = obj_in.get("task_content")
        solution_rules = obj_in.get("solution_rules")
        
        if task_content is not None and solution_rules is not None:
            # Валидация и синхронизация
            task_content_obj, solution_rules_obj = self._validate_task_data(
                task_content, solution_rules
            )
            obj_in = self._sync_max_score(obj_in, solution_rules_obj)
            # Обновляем obj_in валидированными данными
            obj_in["task_content"] = task_content_obj.model_dump()
            obj_in["solution_rules"] = solution_rules_obj.model_dump()
        
        return await super().create(db, obj_in)

    async def update(
        self, db: AsyncSession, db_obj: Tasks, obj_in: Dict[str, Any]
    ) -> Tasks:
        """
        Обновить задачу с валидацией task_content и solution_rules.
        
        Переопределяет базовый метод для добавления валидации и синхронизации max_score.
        """
        task_content = obj_in.get("task_content")
        solution_rules = obj_in.get("solution_rules")
        
        # Если обновляются task_content или solution_rules, нужно валидировать
        if task_content is not None or solution_rules is not None:
            # Берем текущие значения, если не указаны новые
            if task_content is None:
                task_content = db_obj.task_content
            if solution_rules is None:
                solution_rules = db_obj.solution_rules
            
            if task_content is not None and solution_rules is not None:
                # Валидация и синхронизация
                task_content_obj, solution_rules_obj = self._validate_task_data(
                    task_content, solution_rules
                )
                obj_in = self._sync_max_score(obj_in, solution_rules_obj)
                # Обновляем obj_in валидированными данными
                obj_in["task_content"] = task_content_obj.model_dump()
                obj_in["solution_rules"] = solution_rules_obj.model_dump()
        
        return await super().update(db, db_obj, obj_in)

    async def get_by_external_uid(
        self,
        db: AsyncSession,
        external_uid: str,
    ) -> Tasks:
        task = await self.repo.get_by_keys(
            db,
            {"external_uid": external_uid},
        )
        if task is None:
            raise DomainError(
                detail="Задача с указанным external_uid не найдена",
                status_code=404,
                payload={"external_uid": external_uid},
            )
        return task
    
    async def bulk_upsert(
        self,
        db: AsyncSession,
        items: Sequence[Dict[str, Any]],
    ) -> List[Tuple[str, str, int]]:
        """
        Массовый upsert задач по external_uid.

        Для каждого элемента:
        - если задача с таким external_uid не найдена → создаём (CREATE),
        - если найдена → обновляем поля (UPDATE).

        Валидирует task_content и solution_rules перед сохранением.

        Семантика «поле не передано» (ключа нет в словаре) — «не менять» —
        действует для ``order_position`` и ``requirement_level`` (tsk-377).
        Остальные поля UPDATE перезаписывает значением из payload.

        :param db: асинхронная сессия БД.
        :param items: список словарей с полями задачи
                      (external_uid, course_id, difficulty_id, task_content,
                       solution_rules, max_score).
        :return: список кортежей (external_uid, action, id), где
                 action ∈ {"created", "updated"}.
        """
        results: List[Tuple[str, str, int]] = []
        # tsk-345: курсы, где после этого батча order_position может нарушать
        # порядок THEORY→EASY→NORMAL→HARD→PROJECT — пересортируем в конце.
        courses_needing_reorder: Set[int] = set()

        # tsk-347: HARD-задания курса, у которого есть подкурс сложных, уходят
        # в этот подкурс, а сам подкурс держит уровень `recommended`.
        hard_twins, twin_course_ids = await self._resolve_hard_routing(db, items)

        for data in items:
            external_uid = data["external_uid"]
            twin_course_id = hard_twins.get(data.get("course_id"))
            if twin_course_id is not None and data.get("difficulty_id") == HARD_DIFFICULTY_ID:
                # Курсы, между которыми задание может переехать этим батчем,
                # обязаны пересортироваться: смена course_id без явной позиции
                # оставила бы задание на позиции из старого курса.
                courses_needing_reorder.add(data["course_id"])
                courses_needing_reorder.add(twin_course_id)
                data = {**data, "course_id": twin_course_id}
            # Решает КУРС НАЗНАЧЕНИЯ этого задания, а не наличие подкурса
            # сложных у курса батча: иначе не-HARD задание, приехавшее в одном
            # батче с HARD, тоже получило бы `recommended` (поймано тестом).
            if data.get("course_id") in twin_course_ids or data.get("course_id") == twin_course_id:
                # Уровень задаёт КЛАССИФИКАЦИЯ, а не payload: поля
                # `requirement_level` нет ни у одного клиента-конвейера, а API
                # подставляет дефолт `required` (schemas/tasks.py). Без этой
                # строки переиздание задания, уже лежащего в подкурсе сложных
                # (round-trip чтение→bulk-upsert в ContentBackbone
                # `lms_stem_hygiene`), вернуло бы его в основной поток.
                data = {**data, "requirement_level": "recommended"}
            task_content = data.get("task_content")
            solution_rules = data.get("solution_rules")

            # Валидация и синхронизация max_score
            if task_content is not None and solution_rules is not None:
                task_content_obj, solution_rules_obj = self._validate_task_data(
                    task_content, solution_rules
                )
                # Синхронизируем max_score
                data = self._sync_max_score(data, solution_rules_obj)
                # Обновляем данные валидированными значениями
                data["task_content"] = task_content_obj.model_dump()
                data["solution_rules"] = solution_rules_obj.model_dump()

            # Пытаемся найти существующую задачу по external_uid
            existing = await self.repo.get_by_keys(
                db,
                {"external_uid": external_uid},
            )

            if existing is None:
                # CREATE
                obj_in = {
                    "external_uid": external_uid,
                    "course_id": data["course_id"],
                    "difficulty_id": data["difficulty_id"],
                    "task_content": data["task_content"],
                    "solution_rules": data.get("solution_rules"),
                    "max_score": data.get("max_score"),
                    # CREATE: пробрасываем order_position как есть.
                    # None → триггер БД проставит MAX+1; явное число → сдвиг соседей.
                    "is_active": data.get("is_active", True),
                    "requirement_level": data.get("requirement_level", "required"),
                    "order_position": data.get("order_position"),
                }
                # используем наш переопределенный create для валидации
                task = await self.create(db, obj_in)
                results.append((external_uid, "created", task.id))
                if data.get("order_position") is None:
                    # Триггер поставил MAX+1 (в конец курса) — если задача не
                    # HARD/PROJECT-уровня, это ломает межгрупповой порядок (tsk-345).
                    courses_needing_reorder.add(data["course_id"])
            else:
                # Запоминаем состояние ДО перезаписи — для решения, нужен ли реордер.
                existing_course_id = existing.course_id
                existing_difficulty_id = existing.difficulty_id
                existing_type = (existing.task_content or {}).get("type")

                # UPDATE — перезаписываем основные поля из импорта
                obj_in = {
                    "course_id": data["course_id"],
                    "difficulty_id": data["difficulty_id"],
                    "task_content": data["task_content"],
                    "solution_rules": data.get("solution_rules"),
                    "max_score": data.get("max_score"),
                    "is_active": data.get("is_active", True),
                }
                # UPDATE: уровень обязательности перезаписываем ТОЛЬКО при явной
                # передаче — «ключа нет в payload» значит «не менять» (tsk-377),
                # та же семантика, что у order_position ниже. Ни один конвейер
                # поля не шлёт (TaskPayload в ContentBackbone, парсер Google
                # Sheets), а схема API подставляла дефолт `required` — поэтому
                # любое переиздание молча возвращало задание в основной поток
                # ученика (так эродировала простановка tsk-112). Отличить «не
                # передали» от «передали required» позволяет `exclude_unset=True`
                # на эндпоинте (api/v1/tasks_extra.py).
                if "requirement_level" in data:
                    obj_in["requirement_level"] = data["requirement_level"]
                elif existing_course_id != data["course_id"] and await self._is_hard_twin_course(
                    db, existing_course_id
                ):
                    # Исключение: задание ВЫХОДИТ из блока сложных (переклассификация
                    # HARD → более лёгкий уровень уводит его обратно в номерной курс).
                    # `recommended` там поставил блок, а не методист (tsk-347), поэтому
                    # уровень снимается вместе с блоком — иначе задание вернулось бы в
                    # основной курс, но осталось вне зачёта и вне next-item.
                    obj_in["requirement_level"] = "required"
                # UPDATE: order_position пробрасываем ТОЛЬКО при явном значении.
                # None в payload означает «поле не передано, позицию не менять».
                explicit_order_position = data.get("order_position") is not None
                if explicit_order_position:
                    obj_in["order_position"] = data["order_position"]
                # используем наш переопределенный update для валидации
                task = await self.update(db, existing, obj_in)
                results.append((external_uid, "updated", task.id))

                if not explicit_order_position:
                    new_type = (data.get("task_content") or {}).get("type")
                    reclassified = (
                        data["difficulty_id"] != existing_difficulty_id
                        or new_type != existing_type
                    )
                    if reclassified:
                        # Переклассификация (напр. THEORY-перетег tsk-318) без
                        # явной позиции — тот же класс дефекта, что и MAX+1 при
                        # CREATE: задача осталась на старом месте не той группы.
                        courses_needing_reorder.add(existing_course_id)
                        courses_needing_reorder.add(data["course_id"])

        for course_id in courses_needing_reorder:
            await self._reorder_tasks_by_difficulty(db, course_id)
        if courses_needing_reorder:
            await db.commit()

        return results

    async def _resolve_hard_routing(
        self, db: AsyncSession, items: Sequence[Dict[str, Any]]
    ) -> Tuple[dict[int, int], set[int]]:
        """Маршрутизация HARD-заданий в блок сложных (tsk-347).

        HARD-задания ЕГЭ вынесены в отдельный необязательный блок в конце
        программы: у номерного курса ``X`` подкурс сложных опознаётся по
        ``courses.course_uid = 'lms:tsk347:hard:X'``.

        Зачем инвариант в коде, а не только правка данных: ``bulk_upsert`` при
        UPDATE перезаписывает ``course_id`` (payload шлёт номерной курс) и
        ``requirement_level`` — причём поля уровня нет ни у одного
        клиента-конвейера (``TaskPayload`` в ContentBackbone), а API
        подставляет дефолт ``required`` (``schemas/tasks.py``). Без этого
        первая же доливка KompEGE/Крылова вернула бы задания в основной поток.
        Тот же класс регрессии, что чинил tsk-345: разовый снимок данных без
        инварианта разъезжается на следующем импорте.

        Связь берётся из ДАННЫХ, а не из списка id в коде: заведут блок сложных
        ещё одному курсу — он подхватится сам, править сервис не нужно.

        :return: кортеж из
            ``{course_id: hard_twin_course_id}`` — куда уводить HARD-задания
            курсов батча (только там, где подкурс сложных существует), и
            множества id курсов батча, которые САМИ являются подкурсами
            сложных (туда пишет round-trip «переиздание»: читает задание уже
            из подкурса и шлёт его обратно с тем же course_id).
        """
        hard_source_ids = {
            data["course_id"]
            for data in items
            if data.get("course_id") is not None
            and data.get("difficulty_id") == HARD_DIFFICULTY_ID
        }
        all_course_ids = {
            data["course_id"] for data in items if data.get("course_id") is not None
        }
        if not all_course_ids:
            return {}, set()
        rows = (
            await db.execute(
                text(
                    "SELECT id, course_uid FROM courses "
                    "WHERE course_uid = ANY(:twin_uids) OR "
                    "      (id = ANY(:ids) AND course_uid LIKE :prefix || '%')"
                ),
                {
                    "twin_uids": [f"{HARD_TWIN_UID_PREFIX}{cid}" for cid in sorted(hard_source_ids)],
                    "ids": sorted(all_course_ids),
                    "prefix": HARD_TWIN_UID_PREFIX,
                },
            )
        ).fetchall()

        twins: dict[int, int] = {}
        twin_course_ids: set[int] = set()
        for course_id, course_uid in rows:
            src_course_id = int(course_uid.rsplit(":", 1)[1])
            if src_course_id in hard_source_ids:
                twins[src_course_id] = int(course_id)
            if int(course_id) in all_course_ids:
                twin_course_ids.add(int(course_id))
        return twins, twin_course_ids

    async def _is_hard_twin_course(self, db: AsyncSession, course_id: int) -> bool:
        """Является ли курс подкурсом сложных заданий (tsk-347).

        Проверка по данным (``course_uid LIKE 'lms:tsk347:hard:%'``), а не по
        списку id в коде — заведут блок сложных ещё одному курсу, править сервис
        не нужно. Вызывается только при переезде задания между курсами, поэтому
        на обычной доливке лишнего запроса нет.

        :param course_id: id курса, в котором задание лежало ДО этого импорта.
        :return: True, если курс — подкурс сложных.
        """
        row = (
            await db.execute(
                text(
                    "SELECT 1 FROM courses "
                    "WHERE id = :course_id AND course_uid LIKE :prefix || '%'"
                ),
                {"course_id": course_id, "prefix": HARD_TWIN_UID_PREFIX},
            )
        ).first()
        return row is not None

    async def _reorder_tasks_by_difficulty(
        self, db: AsyncSession, course_id: int
    ) -> int:
        """
        Пересортировать ``order_position`` задач курса по правилу
        THEORY→EASY→NORMAL→HARD→PROJECT (``difficulty_id`` ASC), внутри уровня —
        по типу (SC/MC → TA/SA → SA_COM).

        Относительный порядок внутри группы (одинаковые difficulty_id + тип)
        сохраняется — тайбрейк по текущему ``order_position``, а не по ``id``,
        чтобы не откатывать ручной drag-and-drop реордер методиста
        (``POST /courses/{id}/tasks/reorder``).

        Отключает ``trg_set_task_order_position`` на время UPDATE через
        session-variable ``app.skip_task_order_trigger`` (``is_local=true`` —
        см. ``TasksRepository.reorder_tasks``, docs/database-triggers-contract.md
        §15), НЕ через ``ALTER TABLE ... DISABLE TRIGGER``: последнее берёт
        ACCESS EXCLUSIVE лок на всю таблицу ``tasks`` (простаивают live-запросы
        студентов по ВСЕМ курсам на время лока, не только по этому course_id).

        Вызывается автоматически из :meth:`bulk_upsert` для курсов, где батч
        мог сломать межгрупповой порядок (tsk-345): новая задача получила
        ``order_position`` автоматически (MAX+1 от триггера) либо существующая
        задача была переклассифицирована (``difficulty_id``/``type``) без явной
        позиции. Идемпотентно — на уже отсортированном курсе не меняет ничего.

        :return: количество задач, у которых изменился order_position.
        """
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )
        result = await db.execute(
            text(
                """
                WITH new_order AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            ORDER BY
                                difficulty_id ASC,
                                CASE task_content->>'type'
                                    WHEN 'SC' THEN 1
                                    WHEN 'MC' THEN 1
                                    WHEN 'TA' THEN 2
                                    WHEN 'SA' THEN 2
                                    WHEN 'SA_COM' THEN 3
                                    ELSE 99
                                END ASC,
                                order_position ASC NULLS LAST,
                                id ASC
                        ) AS new_op
                    FROM tasks
                    WHERE course_id = :course_id
                )
                UPDATE tasks t
                SET order_position = n.new_op
                FROM new_order n
                WHERE t.id = n.id
                  AND t.course_id = :course_id
                  AND (t.order_position IS DISTINCT FROM n.new_op)
                """
            ),
            {"course_id": course_id},
        )
        # is_local=true истекает только на COMMIT/ROLLBACK транзакции; сбрасываем
        # явно, чтобы не «протечь» в следующую операцию этой же транзакции,
        # если между реордером и коммитом окажется ещё один INSERT/UPDATE.
        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'false', true)")
        )
        return result.rowcount

    async def validate_task_import(
            self,
            db: AsyncSession,
            *,
            task_content: Any,
            solution_rules: Any | None,
            difficulty_code: str | None,
            difficulty_id: int | None,
            course_code: str | None,
            external_uid: str | None,
        ) -> tuple[bool, List[str]]:
            """
            Предварительная валидация задания перед импортом.

            Проверяем:
            - наличие external_uid;
            - базовую структуру task_content (через TaskContent схему);
            - наличие ключевых полей в solution_rules (через SolutionRules схему);
            - соответствие correct_options и options[].id;
            - уникальность options[].id;
            - существование difficulty по difficulty_code;
            - существование course по course_code.

            Ничего не записывает в БД, только возвращает список ошибок.
            """
            errors: List[str] = []

            # ---- external_uid ----
            if not external_uid:
                errors.append("external_uid not provided or empty")

            # ---- task_content и solution_rules ----
            if task_content is None:
                errors.append("task_content not provided")
            elif solution_rules is None:
                errors.append("solution_rules not provided")
            else:
                # Используем нашу валидацию для проверки структуры и соответствия
                try:
                    task_content_obj, solution_rules_obj = self._validate_task_data(
                        task_content, solution_rules
                    )
                    # Дополнительная проверка: валидация max_score
                    if solution_rules_obj.max_score <= 0:
                        errors.append("solution_rules.max_score must be positive")
                except DomainError as e:
                    errors.append(f"Validation error: {e.detail}")
                except Exception as e:
                    errors.append(f"Unexpected validation error: {str(e)}")

            # ---- difficulty_code / difficulty_id ----
            diff_service = DifficultyLevelsService()

            if difficulty_code:
                # Основной путь — проверка по коду сложности
                diff = await diff_service.repo.get_by_keys(db, {"code": difficulty_code})
                if diff is None:
                    errors.append(f"difficulty_id not found for code='{difficulty_code}'")
            elif difficulty_id is not None:
                # Резервный путь — если пришёл только ID сложности
                diff = await diff_service.get_by_id(db, difficulty_id)
                if diff is None:
                    errors.append(f"difficulty_id {difficulty_id} not found")
            else:
                errors.append("difficulty_code or difficulty_id not provided")

            # ---- course_code ----
            if not course_code:
                errors.append("course_code not provided")
            else:
                course_service = CoursesService()
                course = await course_service.repo.get_by_keys(
                    db,
                    {"course_uid": course_code},
                )
                if course is None:
                    errors.append(f"course_id not found for course_code='{course_code}'")

            is_valid = len(errors) == 0
            return is_valid, errors

    async def find_by_external_uids(
        self,
        db: AsyncSession,
        uids: list[str],
    ) -> list[tuple[str, int]]:
        """
        Массовый поиск задач по списку external_uid.

        Возвращает список кортежей (external_uid, id)
        только для тех uid, которые действительно найдены.
        """
        if not uids:
            return []

        stmt = (
            select(self.repo.model.external_uid, self.repo.model.id)
            .where(self.repo.model.external_uid.in_(uids))
        )

        rows = (await db.execute(stmt)).all()

        # rows: List[(external_uid, id)]
        return [(uid, id_) for uid, id_ in rows]

    async def get_by_course(
        self,
        db: AsyncSession,
        course_id: int,
        difficulty_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Tasks], int]:
        """
        Получить задачи курса с пагинацией.

        Порядок результата детерминирован:
        ``ORDER BY order_position NULLS LAST, id`` — совпадает с порядком,
        который использует Learning Engine ``next-item picker`` и
        отражает контракт триггеров ``trg_set_task_order_position`` /
        ``trg_reorder_tasks_after_delete`` (см. docs/database-triggers-contract.md
        разделы 13-14).

        Args:
            db: Асинхронная сессия БД.
            course_id: ID курса.
            difficulty_id: Опциональный фильтр по уровню сложности.
            limit: Максимум записей на странице.
            offset: Смещение.

        Returns:
            Кортеж (список задач, общее количество).
        """
        from sqlalchemy import func, select

        filters = [self.repo.model.course_id == course_id]
        if difficulty_id is not None:
            filters.append(self.repo.model.difficulty_id == difficulty_id)

        list_stmt = (
            select(self.repo.model)
            .where(*filters)
            .order_by(
                self.repo.model.order_position.asc().nulls_last(),
                self.repo.model.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        count_stmt = select(func.count()).select_from(self.repo.model).where(*filters)

        items = (await db.execute(list_stmt)).scalars().all()
        total = (await db.execute(count_stmt)).scalar() or 0
        return list(items), int(total)

    async def reorder_tasks(
        self,
        db: AsyncSession,
        course_id: int,
        task_orders: List[Dict[str, int]],
    ) -> List[Tasks]:
        """
        Массовое изменение порядка заданий курса.

        Зеркало ``MaterialsService.reorder_materials`` с расширенной валидацией:

        - **404** — курс ``course_id`` не найден;
        - **422** — обнаружены дубликаты ``task_id`` в теле запроса;
        - **422** — обнаружены дубликаты ``order_position`` в теле запроса;
        - **400** — ``task_id`` не принадлежит курсу ``course_id`` или не найден.

        Partial reorder допустим: можно прислать порядок только для подмножества
        заданий курса; остальные сохраняют свои текущие позиции.

        Атомарность обеспечивает ``TasksRepository.reorder_tasks`` через
        session-variable ``app.skip_task_order_trigger`` + bulk UPDATE + commit
        в одной транзакции.
        """
        from sqlalchemy.exc import IntegrityError

        if not task_orders:
            return []

        # 1. Курс существует
        courses_service = CoursesService()
        course = await courses_service.repo.get(db, course_id)
        if not course:
            raise DomainError(
                detail=f"Курс с ID {course_id} не найден",
                status_code=404,
            )

        # 2. Нет дубликатов task_id
        task_ids = [item["task_id"] for item in task_orders]
        if len(task_ids) != len(set(task_ids)):
            duplicates = sorted({tid for tid in task_ids if task_ids.count(tid) > 1})
            raise DomainError(
                detail=f"Обнаружены дубликаты task_id в теле запроса: {duplicates}",
                status_code=422,
            )

        # 3. Нет дубликатов order_position
        positions = [item["order_position"] for item in task_orders]
        if len(positions) != len(set(positions)):
            duplicates = sorted({p for p in positions if positions.count(p) > 1})
            raise DomainError(
                detail=f"Обнаружены дубликаты order_position в теле запроса: {duplicates}",
                status_code=422,
            )

        # 4. Все task_id принадлежат курсу
        ids_in_course = await self.repo.list_ids_by_course(db, course_id)
        for tid in task_ids:
            if tid not in ids_in_course:
                raise DomainError(
                    detail=(
                        f"Задание с ID {tid} не принадлежит курсу {course_id} "
                        f"или не найдено"
                    ),
                    status_code=400,
                )

        # 5. Bulk UPDATE через repo (атомарный commit внутри)
        try:
            return await self.repo.reorder_tasks(db, course_id, task_orders)
        except IntegrityError as e:
            raise DomainError(
                detail=f"Ошибка при изменении порядка заданий: {e!s}",
                status_code=400,
            )
