"""tsk-314: детерминированный сэмплинг заданий EASY/NORMAL по сложности.

Один и тот же (student_id, course_id) обязан давать один и тот же набор при
повторном заходе (решение оператора). Сид считается хэшем пары через
hashlib, НЕ Python `hash()` — тот рандомизирован `PYTHONHASHSEED` между
процессами/рестартами и не даёт стабильности между запросами.
"""
from __future__ import annotations

import hashlib
import random
from typing import Sequence


def deterministic_seed(student_id: int, course_id: int) -> int:
    """Стабильный (между процессами и рестартами) сид для пары студент+подкурс."""
    digest = hashlib.sha256(f"{student_id}:{course_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def sample_task_ids(
    *,
    easy_ids: Sequence[int],
    normal_ids: Sequence[int],
    threshold: int,
    easy_ratio: float,
    student_id: int,
    course_id: int,
) -> set[int]:
    """Отобрать подмножество EASY/NORMAL заданий детерминированно.

    Возвращает МНОЖЕСТВО id заданий, которые нужно ОСТАВИТЬ в обходе.
    THEORY и прочая сложность сюда не передаются вовсе — вызывающий код
    держит их всегда, независимо от результата этой функции.

    Если `easy_ids`+`normal_ids` в сумме не превышает `threshold` — выборка
    не нужна, возвращаются оба пула целиком.

    `easy_ratio` — доля EASY в итоговой выборке (0..1). Если одного из
    пулов не хватает на его долю, недостача добирается из другого пула —
    итоговый размер выборки остаётся равен `threshold`, пока суммарно
    заданий достаточно (гарантировано вызывающим условием total > threshold).
    """
    total = len(easy_ids) + len(normal_ids)
    if total <= threshold:
        return set(easy_ids) | set(normal_ids)

    avail_easy = len(easy_ids)
    avail_normal = len(normal_ids)

    target_easy = round(threshold * easy_ratio)
    target_normal = threshold - target_easy

    # Недостачу одного пула компенсируем другим, чтобы итог остался = threshold.
    deficit_easy = max(0, target_easy - avail_easy)
    deficit_normal = max(0, target_normal - avail_normal)
    target_easy = min(target_easy, avail_easy) + deficit_normal
    target_normal = min(target_normal, avail_normal) + deficit_easy
    # Финальный клэмп — страховка от пограничных случаев округления.
    target_easy = min(target_easy, avail_easy)
    target_normal = min(target_normal, avail_normal)

    seed = deterministic_seed(student_id, course_id)
    rnd = random.Random(seed)

    # sorted(...) — фиксированный порядок пула ДО сэмплинга: без него порядок
    # id в исходном списке мог бы плавать между вызовами (например, если
    # вызывающий код когда-нибудь начнёт передавать пулы не по возрастанию
    # id) и ломать стабильность набора при том же seed.
    picked_easy = rnd.sample(sorted(easy_ids), target_easy) if target_easy else []
    picked_normal = rnd.sample(sorted(normal_ids), target_normal) if target_normal else []
    return set(picked_easy) | set(picked_normal)
