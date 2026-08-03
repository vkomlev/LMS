"""tsk-314: юнит-тесты чистого сэмплера (без БД) — `app/services/task_sampling.py`.

Движок (learning_engine_service._sampled_out_task_ids) отвечает за то, ЧТО
подаётся на вход (пулы EASY/NORMAL, конфиг курса); эти тесты — за то, что сам
алгоритм отбора детерминирован и соблюдает соотношение/размер выборки при
любых пулах, без обращения к БД.
"""
from __future__ import annotations

from app.services.task_sampling import deterministic_seed, sample_task_ids


def test_deterministic_seed_stable_across_calls():
    """Тот же (student_id, course_id) -> тот же сид при повторных вызовах."""
    assert deterministic_seed(142, 138) == deterministic_seed(142, 138)


def test_deterministic_seed_differs_by_input():
    """Разные пары дают разные сиды (иначе разные ученики/курсы делили бы набор)."""
    assert deterministic_seed(142, 138) != deterministic_seed(143, 138)
    assert deterministic_seed(142, 138) != deterministic_seed(142, 139)


def test_sample_below_threshold_returns_all():
    """Заданий не больше порога — выборка не нужна, отдаются оба пула целиком."""
    easy = list(range(1, 6))
    normal = list(range(101, 104))
    kept = sample_task_ids(
        easy_ids=easy, normal_ids=normal, threshold=10, easy_ratio=0.5,
        student_id=1, course_id=1,
    )
    assert kept == set(easy) | set(normal)


def test_sample_exceeding_threshold_respects_size_and_ratio():
    """Превышение порога -> ровно threshold заданий, поровну EASY/NORMAL при ratio=0.5."""
    easy = list(range(1, 21))  # 20
    normal = list(range(101, 121))  # 20
    kept = sample_task_ids(
        easy_ids=easy, normal_ids=normal, threshold=10, easy_ratio=0.5,
        student_id=142, course_id=138,
    )
    assert len(kept) == 10
    assert len({i for i in kept if i in easy}) == 5
    assert len({i for i in kept if i in normal}) == 5


def test_sample_respects_custom_ratio():
    """Настраиваемая доля (не 50/50): easy_ratio=0.3 -> ~30% EASY в выборке."""
    easy = list(range(1, 21))
    normal = list(range(101, 121))
    kept = sample_task_ids(
        easy_ids=easy, normal_ids=normal, threshold=10, easy_ratio=0.3,
        student_id=142, course_id=138,
    )
    assert len(kept) == 10
    assert len({i for i in kept if i in easy}) == 3
    assert len({i for i in kept if i in normal}) == 7


def test_sample_is_deterministic_between_calls():
    """Повторный вызов с теми же (student_id, course_id) и теми же пулами ->
    ТОТ ЖЕ набор (стабильность за учеником, решение оператора tsk-314)."""
    easy = list(range(1, 21))
    normal = list(range(101, 121))
    kwargs = dict(
        easy_ids=easy, normal_ids=normal, threshold=8, easy_ratio=0.5,
        student_id=7, course_id=1379,
    )
    first = sample_task_ids(**kwargs)
    second = sample_task_ids(**kwargs)
    assert first == second


def test_sample_deficit_in_one_pool_borrows_from_the_other():
    """EASY не хватает на свою долю -> недостача добирается из NORMAL, итог = threshold."""
    easy = [1, 2]  # только 2 доступно
    normal = list(range(101, 121))  # 20 доступно
    kept = sample_task_ids(
        easy_ids=easy, normal_ids=normal, threshold=10, easy_ratio=0.5,
        student_id=1, course_id=1,
    )
    assert len(kept) == 10
    assert {i for i in kept if i in easy} == {1, 2}
    assert len({i for i in kept if i in normal}) == 8
