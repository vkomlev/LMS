"""tsk-662: обход дерева курса — порядок заперт, дерево считается один раз.

Задача правки — цена, а не поведение: обход стоил ДВУХ запросов на каждый узел
(`get_children` + `selectinload(parent_courses)`) и запускался заново на каждого
ученика. Замер на боевой базе (tsk-655): сводка занятия на 12 участников — 1093
запроса, `/me/last-position` одного ученика — 645-1597.

Поэтому тесты здесь проверяют не «стало быстрее», а что от ускорения НЕ поехал
порядок выдачи. На этом порядке висят два прод-дефекта, оба про «ученику выдали
не тот элемент»:

* `tsk-127` — обход обязан быть POST-ORDER: сперва подкурсы, материалы самого
  курса-контейнера в последнюю очередь;
* `tsk-261` — узел под несколькими родителями входит в обход РОВНО один раз,
  иначе `flat_courses.index(...)` берёт первое вхождение и отбрасывает ученика
  назад.

Плюс два инварианта самой правки: кеш живёт ровно одну сессию БД и сбрасывается,
когда иерархию правят в этой же сессии.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.repos.courses_repo import CoursesRepository, course_tree_cache
from app.services.learning_engine_service import LearningEngineService


async def _course(db, title: str) -> int:
    """Создать курс и вернуть его id."""
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": f"tsk662-{random.randint(10**8, 10**10)}"},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _link(db, *, course_id: int, parent_course_id: int, order_number: int | None) -> None:
    """Подвесить курс к родителю с явным порядковым номером (или без него).

    `order_number` пишется явно: триггер БД проставляет его сам, когда значение
    не задано, а тесту нужны в том числе NULL-порядки.
    """
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, :o) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "p": parent_course_id, "o": order_number},
    )
    await db.commit()


@pytest.fixture
async def tree(db):
    """Дерево, где порядок подкурсов НЕ совпадает с порядком их создания.

    ``root`` → ``second`` (подвешен первым, номер 2), ``first`` (подвешен
    вторым, номер 1 — триггер сдвигает ``second`` вправо), затем два узла,
    подвешенные без номера. У ``first`` — свой ребёнок ``leaf``.

    ⚠️ ``order_number`` в ``course_parents`` пустым не бывает: триггер
    ``trg_set_course_parent_order_number`` проставляет ``MAX+1`` вместо NULL
    (на боевой базе 0 пустых из 769 строк). Поэтому ветка «NULLS LAST» тут
    недостижима — она проверяется отдельно, на самом ключе сортировки.

    Ожидаемый обход: ``leaf, first, second, tail_first, tail_second, root``.
    """
    root = await _course(db, "tsk662 root")
    first = await _course(db, "tsk662 first (номер 1)")
    second = await _course(db, "tsk662 second (номер 2)")
    leaf = await _course(db, "tsk662 leaf")
    tail_first = await _course(db, "tsk662 хвост (подвешен раньше)")
    tail_second = await _course(db, "tsk662 хвост (подвешен позже)")

    await _link(db, course_id=second, parent_course_id=root, order_number=2)
    await _link(db, course_id=first, parent_course_id=root, order_number=1)
    await _link(db, course_id=tail_first, parent_course_id=root, order_number=None)
    await _link(db, course_id=tail_second, parent_course_id=root, order_number=None)
    await _link(db, course_id=leaf, parent_course_id=first, order_number=1)

    spare = await _course(db, "tsk662 запасной (подвесим позже)")

    data = {
        "root": root, "first": first, "second": second, "leaf": leaf,
        "tail_first": tail_first, "tail_second": tail_second, "spare": spare,
    }
    yield data

    ids = list(data.values())
    await db.execute(text("DELETE FROM course_parents WHERE course_id = ANY(:ids)"), {"ids": ids})
    await db.execute(text("DELETE FROM course_parents WHERE parent_course_id = ANY(:ids)"), {"ids": ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": ids})
    await db.commit()


@pytest.mark.asyncio
async def test_traversal_is_post_order_by_order_number(db, tree):
    """Порядок обхода: post-order, подкурсы по `order_number`, не по id.

    tsk-127: ребёнок раньше родителя — `leaf` перед `first`, `root` последний.
    Порядок берётся из `course_parents.order_number`: `second` создан РАНЬШЕ
    `first`, но идёт после него.
    """
    svc = LearningEngineService()
    flat = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001

    assert flat == [
        tree["leaf"], tree["first"], tree["second"],
        tree["tail_first"], tree["tail_second"], tree["root"],
    ], f"порядок обхода поехал: {flat}"


@pytest.mark.asyncio
async def test_python_sort_agrees_with_sql_ordering(db, tree):
    """Сортировка детей в Python совпадает с канонической сортировкой SQL.

    Обход сортирует детей в Python, а соседние выборки (материалы, задания,
    `get_children`) — в SQL. Разъедутся правила — ученик получит элементы в
    одном порядке, а разделы вокруг них в другом.
    """
    canonical = [
        row[0]
        for row in (
            await db.execute(
                text(
                    "SELECT course_id FROM course_parents WHERE parent_course_id = :p "
                    "ORDER BY order_number ASC NULLS LAST, course_id ASC"
                ),
                {"p": tree["root"]},
            )
        ).all()
    ]
    svc = LearningEngineService()
    flat = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    # Прямые дети корня в порядке обхода: `leaf` — внук, `root` — сам корень.
    walked_children = [cid for cid in flat if cid in set(canonical)]

    assert walked_children == canonical, f"Python и SQL разошлись: {walked_children} vs {canonical}"


def test_order_key_puts_nulls_last_then_sorts_by_id():
    """Ключ сортировки: заданный номер раньше пустого, пустые — по id.

    Пустой `order_number` в `course_parents` сегодня не появляется (триггер
    БД проставляет MAX+1), но правило живёт в общем ключе `_order_key` вместе
    с `order_position` материалов и заданий — а ТАМ пустые значения реальны.
    Поэтому ветка проверяется на самом ключе, а не через дерево.
    """
    key = LearningEngineService._order_key  # noqa: SLF001
    rows = [(None, 50), (2, 10), (None, 40), (1, 99)]

    assert sorted(rows, key=lambda r: key(r[0], r[1])) == [
        (1, 99), (2, 10), (None, 40), (None, 50),
    ]


@pytest.mark.asyncio
async def test_tree_walked_once_per_session(db, tree, monkeypatch):
    """Дерево одного корня опрашивается ОДИН раз на сессию БД.

    Первопричина заторов: `resolve_next_item` обходит корень дважды за вызов, а
    сводка занятия — на каждого из 7-12 участников группы, у которых дерево
    одно и то же.
    """
    svc = LearningEngineService()
    calls: list[int] = []
    original = CoursesRepository.get_child_rows

    async def counting(self, session, course_id):  # type: ignore[no-untyped-def]
        calls.append(course_id)
        return await original(self, session, course_id)

    monkeypatch.setattr(CoursesRepository, "get_child_rows", counting)

    first_walk = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    after_first = len(calls)
    second_walk = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001

    assert after_first > 0, "первый обход обязан читать БД"
    assert len(calls) == after_first, f"повторный обход снова пошёл в БД: {calls[after_first:]}"
    assert second_walk == first_walk, "кеш вернул другой порядок"


@pytest.mark.asyncio
async def test_cache_does_not_leak_between_sessions(db, tree, db_session_factory):
    """Кеш НЕ переживает сессию: соседний запрос обязан читать дерево заново.

    Сессия живёт один HTTP-запрос, и кеш обхода привязан к ней намеренно:
    переживи он сессию — методист переподвесил бы подкурс, а ученики до
    перезапуска процесса видели бы старое дерево. Проверяем, что у новой
    сессии кеш пустой, а не унаследованный.
    """
    svc = LearningEngineService()
    await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    assert course_tree_cache(db), "в своей сессии кеш обязан быть заполнен"

    async with db_session_factory() as other:
        assert not course_tree_cache(other), "кеш протёк в соседнюю сессию"
        flat = await svc._collect_courses_in_order(other, tree["root"])  # noqa: SLF001
        assert flat[-1] == tree["root"], "новая сессия обязана обойти дерево сама"


@pytest.mark.asyncio
async def test_cached_result_is_not_shared_mutable(db, tree):
    """Потребитель не может испортить кеш, изменив полученный список."""
    svc = LearningEngineService()
    walk = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    walk.append(-1)
    walk.reverse()

    again = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    assert -1 not in again and again[-1] == tree["root"], f"кеш испорчен извне: {again}"


@pytest.mark.asyncio
async def test_reparent_in_same_session_invalidates_cache(db, tree):
    """Правка иерархии в этой же сессии сбрасывает кеш обхода.

    Иначе методист, переподвесивший подкурс, до конца запроса видел бы старое
    дерево — а импорт-скрипты правят и читают в одной сессии.
    """
    svc = LearningEngineService()
    before = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    assert tree["spare"] not in before

    repo = CoursesRepository()
    await repo.set_parent_courses(db, course_id=tree["spare"], parent_course_ids=[tree["root"]])

    assert not course_tree_cache(db), "кеш не сброшен после правки иерархии"
    after = await svc._collect_courses_in_order(db, tree["root"])  # noqa: SLF001
    assert tree["spare"] in after, f"обход не увидел новый подкурс: {after}"
    assert after[-1] == tree["root"], "post-order сломан после пересчёта"


@pytest.mark.asyncio
async def test_child_rows_match_get_children(db, tree):
    """`get_child_rows` отдаёт то же и в том же порядке, что `get_children`.

    Замена метода в обходе не должна менять ни состав, ни порядок детей.
    """
    repo = CoursesRepository()
    heavy = await repo.get_children(db, tree["root"])
    light = await repo.get_child_rows(db, tree["root"])

    assert [(c.id, o) for c, o in heavy] == [(cid, o) for cid, o, _t in light]
    assert [c.title for c, _o in heavy] == [t for _cid, _o, t in light]


@pytest.mark.asyncio
async def test_get_children_still_loads_parents(db, tree):
    """`get_children` по-прежнему отдаёт родителей — на этом висит API.

    `CourseRead.parent_course_ids` читает связь, а свойство модели на
    незагруженной связи молча отдаёт `[]`: выключение подгрузки обнулило бы
    поле `GET /courses/{id}/children` БЕЗ ошибки.
    """
    repo = CoursesRepository()
    children = await repo.get_children(db, tree["root"])
    by_id = {c.id: c for c, _o in children}

    assert tree["first"] in by_id
    assert by_id[tree["first"]].parent_course_ids == [tree["root"]]
