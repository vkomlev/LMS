"""tsk-918: «сейчас» — фронт ученика, хвосты позади — отдельно.

Карточка Нуженко 12.09: «Последнее: Задание 5» и тут же «Сейчас: Задание 3
— Доля товара-лидера». Ученик пропустил одно задание в теме (0 попыток) и
ушёл на две темы вперёд; «сейчас» брало первый незакрытый элемент курса и
спорило с «последним».
"""
from app.services.course_position import position


def _item(kind: str, item_id: int, title: str, status: str, parent: int | None = None) -> dict:
    return {"item_type": kind, "item_id": item_id, "title": title, "status": status,
            "parent_course_id": parent}


ROOT = 112


def test_frontier_is_after_the_last_done_item():
    items = [
        _item("course", 3, "Задание 3", "IN_PROGRESS", ROOT),
        _item("task", 31, "Excel: сводная", "PASSED", 3),
        _item("task", 32, "Доля товара-лидера", "OPEN", 3),
        _item("course", 5, "Задание 5", "IN_PROGRESS", ROOT),
        _item("task", 51, "Минимальное число", "PASSED", 5),
        _item("task", 52, "Максимальное число", "OPEN", 5),
    ]
    # Якорь — последнее по времени: задание из Задания 5.
    pos = position(items, course_id=ROOT, anchor=("task", 51))
    assert pos.current_section_title == "Задание 5"
    assert pos.current_item_title == "Максимальное число"
    assert pos.behind_count == 1
    assert pos.behind_section_title == "Задание 3"
    assert pos.behind_item_title == "Доля товара-лидера"
    assert pos.percent_complete == 50


def test_nothing_ahead_means_the_tail_is_the_next_step():
    items = [
        _item("task", 1, "а", "OPEN", None),
        _item("task", 2, "б", "OPEN", None),
        _item("task", 3, "в", "PASSED", None),
    ]
    pos = position(items, course_id=ROOT, anchor=("task", 3))
    assert pos.current_item_title == "а"
    assert pos.behind_count == 1 and pos.behind_item_title == "б"


def test_no_progress_at_all_starts_from_the_beginning():
    items = [_item("material", 1, "Теория", "NOT_STARTED", None), _item("task", 2, "Задача", "OPEN", None)]
    pos = position(items, course_id=ROOT)
    assert pos.current_item_title == "Теория" and pos.behind_count == 0


def test_completed_course_has_no_current_item():
    items = [_item("task", 1, "а", "PASSED", None), _item("task", 2, "б", "SKIPPED", None)]
    pos = position(items, course_id=ROOT)
    assert pos.current_item_title is None and pos.behind_count == 0
    assert pos.percent_complete == 100


def test_far_ahead_item_done_in_class_is_not_the_anchor_by_itself():
    """Якорь задаёт время, а не позиция: одно задание, разобранное на уроке
    далеко впереди, не уносит фронт в конец курса (замер 12.09: 330
    «позади» у Литовкина при порядковом фронте)."""
    items = [
        _item("task", 1, "а", "PASSED", None),
        _item("task", 2, "б", "OPEN", None),
        _item("task", 3, "в", "OPEN", None),
        _item("task", 9, "далеко", "PASSED", None),
    ]
    # Последним по времени закрыто «а» — фронт сразу за ним, хвостов нет.
    pos = position(items, course_id=ROOT, anchor=("task", 1))
    assert pos.current_item_title == "б" and pos.behind_count == 0
