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


def test_bigger_threshold_only_adds_tasks():
    """tsk-798: набор при большем пороге ВКЛЮЧАЕТ набор при меньшем.

    Объём программы теперь подстраивается под темп ученика, то есть порог
    растёт по ходу года. Если бы наборы пересобирались, человек увидел бы,
    как решённые задания исчезают из программы, а вместо них появляются
    незнакомые — за неделю до срока это выглядит как потеря работы.
    """
    easy = list(range(1, 31))
    normal = list(range(101, 131))
    common = dict(easy_ids=easy, normal_ids=normal, easy_ratio=0.5,
                  student_id=42, course_id=112)

    small = sample_task_ids(threshold=10, **common)
    medium = sample_task_ids(threshold=20, **common)
    large = sample_task_ids(threshold=40, **common)

    assert small < medium < large
    assert len(small) == 10 and len(medium) == 20 and len(large) == 40


def test_pools_are_shuffled_independently():
    """EASY и NORMAL тасуются по-разному.

    Пулы одной длины при одном сиде дали бы задания одних и тех же позиций —
    выборка перестала бы быть случайной по номеру внутри подкурса.
    """
    easy = list(range(1, 21))
    normal = list(range(1, 21))  # намеренно те же номера
    kept = sample_task_ids(
        easy_ids=easy, normal_ids=normal, threshold=10, easy_ratio=0.5,
        student_id=5, course_id=9,
    )
    # Если бы порядок совпадал, обе половины дали бы один и тот же набор из 5.
    assert len(kept) > 5


def test_solved_tasks_are_never_dropped():
    """tsk-798: решённое остаётся в выборке всегда и в порог не считается.

    Выборка стала включаться людям, которые давно учатся. Выбросив решённое,
    мы получили бы числитель прогресса больше знаменателя — подкурс не
    закрылся бы никогда, — а для человека это выглядит как пропажа работы.
    """
    easy = list(range(1, 51))
    normal = list(range(101, 151))
    solved = set(easy[:20]) | set(normal[:20])

    kept = sample_task_ids(
        easy_ids=easy, normal_ids=normal, threshold=10, easy_ratio=0.5,
        student_id=3, course_id=77, keep_ids=solved,
    )

    assert solved <= kept, "решённое выброшено из программы"
    # `threshold` — полный размер выборки, а не добавка к решённому: иначе
    # каждая сдача добавляла бы новое задание взамен и набор рос бы вечно.
    # Здесь порог (10) ниже числа решённых (40) — выборка растягивается до
    # пройденного, а не режет его.
    assert len(kept) == 40


def test_solving_a_task_does_not_reshuffle_the_rest():
    """Сдача задания не меняет остальной набор.

    Порядок считается по полному пулу, поэтому переход задания в «решённые»
    не пересобирает перестановку. Иначе после каждой сдачи ученик получал бы
    другой список оставшихся заданий.
    """
    easy = list(range(1, 41))
    normal = list(range(101, 141))
    common = dict(easy_ids=easy, normal_ids=normal, threshold=12,
                  easy_ratio=0.5, student_id=8, course_id=15)

    before = sample_task_ids(**common, keep_ids=set())
    # Ученик решил два задания ИЗ ВЫБОРКИ — набор не должен перетасоваться.
    solved = set(list(before)[:2])
    after = sample_task_ids(**common, keep_ids=solved)

    assert solved <= after
    assert len(after) == len(before), "набор не должен расти после сдачи"
    assert after == before, "сдача пересобрала выборку"


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
