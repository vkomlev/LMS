"""tsk-314: детерминированный сэмплинг заданий EASY/NORMAL по сложности.

Один и тот же (student_id, course_id) обязан давать один и тот же набор при
повторном заходе (решение оператора). Сид считается хэшем пары через
hashlib, НЕ Python `hash()` — тот рандомизирован `PYTHONHASHSEED` между
процессами/рестартами и не даёт стабильности между запросами.

**tsk-798: наборы вложены по размеру.** Отбор идёт не `random.sample`, а
перестановкой пула по сиду и взятием головы. Разница видна только когда
порог МЕНЯЕТСЯ: `sample(pool, 5)` и `sample(pool, 8)` при одном сиде дают
разные наборы, а не первый внутри второго. Для адаптивного объёма это
недопустимо — порог там растёт вслед за темпом ученика, и на пересобранном
наборе человек увидел бы, как уже решённые задания исчезают из программы, а
незнакомые появляются вместо них. С перестановкой рост порога только
ДОБАВЛЯЕТ задания к тому, что уже было.

Менять механику безопасно: на проде выборка не включена ни на одном подкурсе
(`sampling_config IS NOT NULL` = 0 строк на 05.09), то есть ничьи наборы этой
правкой не переписываются.
"""
from __future__ import annotations

import hashlib
import random
from typing import Iterable, Optional, Sequence


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
    keep_ids: Optional[Iterable[int]] = None,
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

    **`keep_ids` — задания, которые ученик уже прошёл** (tsk-798). Они входят
    в выборку в первую очередь и занимают место в пределах `threshold`. Два
    разных повода, и оба обязательные:

    1. Выборка стала включаться людям, которые давно учатся, а не только
       новичкам. Выбросив решённое, мы получили бы числитель прогресса больше
       знаменателя: `compute_course_state` вычитает вырезанное из общего числа
       заданий, а пройденные считает как есть.
    2. Для человека это выглядело бы как пропажа сделанной работы.

    `threshold` при этом остаётся ПОЛНЫМ размером выборки, а не добавкой к
    решённому: иначе каждая сдача добавляла бы новое задание взамен, и набор
    рос бы бесконечно — подкурс не закрылся бы никогда.

    Порядок считается по полному пулу: решённое задание не меняет
    перестановку, поэтому список оставшихся не пересобирается после сдачи.
    """
    keep = {int(i) for i in (keep_ids or ())}
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

    # Пол по уже решённому: выборка не может быть меньше того, что человек
    # прошёл. Порог ниже этого числа приходит, когда его правят руками (или
    # ученик успел больше, чем планировалось) — выбрасывать за это его работу
    # нельзя, поэтому выборка растягивается, а не режет пройденное.
    target_easy = max(target_easy, len([i for i in easy_ids if i in keep]))
    target_normal = max(target_normal, len([i for i in normal_ids if i in keep]))

    seed = deterministic_seed(student_id, course_id)

    def head(ids: Sequence[int], take: int, salt: int) -> list[int]:
        """Первые `take` из пула, перетасованного по сиду ученика.

        Перестановка вместо `random.sample` — чтобы набор при БОЛЬШЕМ `take`
        включал набор при меньшем (см. докстринг модуля). `salt` разводит
        порядок пулов: без него EASY и NORMAL тасовались бы одинаково, и на
        пулах равной длины выбирались бы задания одних и тех же позиций.

        `sorted(...)` — фиксированный порядок пула ДО перестановки: без него
        порядок id в исходном списке мог бы плавать между вызовами и ломать
        стабильность набора при том же сиде.
        """
        if take <= 0:
            return []
        pool = sorted(ids)
        random.Random(seed + salt).shuffle(pool)
        # Решённые идут первыми, остальные — в порядке перестановки. Сортировка
        # УСТОЙЧИВАЯ, поэтому взаимный порядок нерешённых не меняется: сдача
        # задания не пересобирает список оставшихся.
        pool.sort(key=lambda i: i not in keep)
        return pool[:take]

    picked_easy = head(easy_ids, target_easy, salt=0)
    picked_normal = head(normal_ids, target_normal, salt=1)
    return set(picked_easy) | set(picked_normal)
