"""tsk-1255: ключи ответов автора → правила сверки задания.

Ключ из файла автора НЕ подтверждён: ручные скрипты сверяли каждый ключ
предметным расчётом (химия), в общий сервис это не переносится. Поэтому
черновик помечает ключ «не подтверждён», а методист смотрит его в предпросмотре.
"""

from __future__ import annotations

import re
from typing import Any

#: Подсказки к вводу ответа: в бланке это подразумевается, в кабинете нет.
MATCHING_HINT = (
    "<p><em>Ответ введите цифрами подряд, без пробелов и запятых — "
    "в том порядке, в каком идут буквы.</em></p>"
)
SELECT_HINT = (
    "<p><em>Ответ введите цифрами подряд, без пробелов и запятых — по "
    "возрастанию, если в задании не сказано иначе.</em></p>"
)
NUMBER_HINT = (
    "<p><em>Ответ — число с той точностью, что указана в условии. Дробную часть "
    "отделяйте запятой или точкой.</em></p>"
)

_KEY_RE = re.compile(r"[\d]+(?:[.,]\d+)?")


def clean_key(raw: str) -> str | None:
    """Ключ автора без пробелов; не цифры и не дробь — значит ключа нет."""
    value = raw.strip().replace(" ", "")
    return value if value and _KEY_RE.fullmatch(value) else None


def is_number(key: str) -> bool:
    """Ответ-число с дробной частью: у него своя нормализация."""
    return "," in key or "." in key


def normalization(key: str) -> list[str]:
    """Шаги сверки под вид ответа.

    Для дроби `strip_punctuation` не ставится: он съедает разделитель, и
    «67,2» превращался бы в «672» — завышенный вдесятеро ответ засчитался бы.
    Для цифр подряд он нужен: «5,3,1,2» → «5312».
    """
    if is_number(key):
        return ["trim", "lower"]
    return ["trim", "lower", "strip_punctuation", "collapse_spaces"]


def accepted_answers(key: str, score: int) -> list[dict[str, Any]]:
    """Принимаемые записи ответа: дробь с запятой и точкой, цифры слитно и через пробел."""
    values = {key}
    if is_number(key):
        values.add(key.replace(",", "."))
        values.add(key.replace(".", ","))
    else:
        values.add(" ".join(key))
    return [{"value": v, "score": score} for v in sorted(values)]


def hint_for(key: str, body_html: str) -> str:
    """Подсказка к вводу по виду ответа и по тому, есть ли в условии позиции А) Б)."""
    if is_number(key):
        return NUMBER_HINT
    if "<strong>А)</strong>" in body_html:
        return MATCHING_HINT
    return SELECT_HINT


def short_answer_rules(key: str, score: int) -> dict[str, Any]:
    """`solution_rules` задания SA по ключу автора."""
    return {
        "auto_check": True,
        "max_score": score,
        "short_answer": {
            "use_regex": False,
            "regex": None,
            "normalization": normalization(key),
            "accepted_answers": accepted_answers(key, score),
        },
    }
