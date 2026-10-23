"""tsk-1255: файлы урока → черновик импорта (узлы, материалы, задания).

Черновик не хранится на сервере (решение оператора 23.10, поставка 1): ответ
разбора отдаётся экрану, методист правит его в предпросмотре и присылает
обратно на публикацию. Ключи внешних объектов (`external_uid`) здесь только
относительные — суффиксы узлов и номера; полный ключ собирает публикация от
курса-родителя, чтобы присланный JSON не мог задеть чужие курсы.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.lesson_import import answers
from app.services.lesson_import.homework import parse_homework
from app.services.lesson_import.konspekt import konspekt_html
from app.services.lesson_import.pretest import parse_pretest

PRETEST_SCORE = 1
HOMEWORK_SCORE = 2
#: Порядок узлов урока: пре-тест до → разбор и конспект → пре-тест после → ДЗ.
NODE_ORDER = {"PRE": 1, "KON": 2, "POST": 3, "HW": 4}


_TOKEN = re.compile(r"[0-9A-Za-zА-Яа-яЁё⁰-₟]+")
_HOMOGLYPH = set("АВСЕНКМОРТХ")
_INDEX = re.compile(r"[⁰-₟]")


def mixed_script_tokens(html: str) -> list[str]:
    """Формулы, где латинская буква набрана кириллицей: «Сl», «СuО», «Н₂О».

    Частая правка ручных скриптов (AUTHOR_FIXES): на вид не отличить, а
    ученик, набравший формулу латиницей, получит «неверно». Слово целиком
    кириллицей без индексов не трогаем — это обычный текст.
    """
    text = re.sub(r"<[^>]+>", " ", html)
    found: list[str] = []
    for tok in _TOKEN.findall(text):
        cyr = [c for c in tok if c in _HOMOGLYPH]
        if not cyr:
            continue
        if re.search(r"[A-Za-z]", tok) or _INDEX.search(tok):
            if tok not in found:
                found.append(tok)
    return found


def _nodes(num: int, has_pre: bool, has_kon: bool, has_hw: bool) -> list[dict[str, Any]]:
    """Узлы урока, у которых есть содержимое."""
    spec = [
        ("PRE", has_pre, f"Пре-тест к уроку {num}: до урока",
         "Ответьте до урока — один раз, исправить ответ нельзя. Так видно, что вы "
         "знали до занятия. После урока пройдёте тот же тест ещё раз.", "auto_check"),
        ("KON", has_kon, f"Урок {num}: разбор и конспект",
         "Видеозапись занятия и полный конспект.", "self_guided"),
        ("POST", has_pre, f"Пре-тест к уроку {num}: после урока",
         "Те же утверждения — теперь после урока. Один ответ на каждое: сравните с "
         "тем, что отвечали до занятия.", "auto_check"),
        ("HW", has_hw, f"Домашнее задание к уроку {num}",
         "Блоки по материалу урока. Часть проверяется сразу, часть — преподаватель.",
         "manual_check"),
    ]
    return [
        {"suffix": s, "title": t, "description": d, "access_level": a, "order_number": NODE_ORDER[s]}
        for s, ok, t, d, a in spec if ok
    ]


def _pretest_tasks(stmts: list, node: str) -> list[dict[str, Any]]:
    """Утверждения пре-теста как задания SC: одна попытка, режим диагностики."""
    prefix = "Пре-тест после урока" if node == "POST" else "Пре-тест"
    return [
        {
            "uid": f"{node}-{s.num:02d}",
            "node": node,
            "difficulty_uid": "theory",
            "max_score": PRETEST_SCORE,
            "requirement_level": "recommended",
            "max_attempts": 1,
            "key_confirmed": s.correct is not None,
            "task_content": {
                "type": "SC",
                "title": f"{prefix}, утверждение {s.num}",
                "stem": s.text,
                "options": [
                    {"id": "yes", "text": "Верно", "is_active": True},
                    {"id": "no", "text": "Неверно", "is_active": True},
                ],
            },
            "solution_rules": {
                "auto_check": True,
                "scoring_mode": "all_or_nothing",
                "correct_options": ["no" if s.correct is False else "yes"],
                "max_score": PRETEST_SCORE,
                "diagnostic": True,
            },
        }
        for s in stmts
    ]


def _homework_task(block) -> dict[str, Any]:
    """Блок ДЗ: есть ключ — SA с автосверкой, нет — развёрнутый ответ на ручную проверку."""
    body = "\n".join(block.html)
    item: dict[str, Any] = {
        "uid": f"HW-{block.num:02d}",
        "node": "HW",
        "difficulty_uid": "normal",
        "max_score": HOMEWORK_SCORE,
        "key_confirmed": False,
    }
    title = f"Домашнее задание, блок {block.num}"
    if block.key:
        item["task_content"] = {"type": "SA", "title": title,
                                "stem": body + answers.hint_for(block.key, body)}
        item["solution_rules"] = answers.short_answer_rules(block.key, HOMEWORK_SCORE)
    else:
        item["task_content"] = {"type": "TA", "title": title, "stem": body, "multiline_answer": True}
        item["solution_rules"] = {"auto_check": False, "manual_review_required": True,
                                  "max_score": HOMEWORK_SCORE}
    return item


def build_draft(
    *,
    lesson_num: int,
    lesson_title: str,
    konspekt: bytes | None = None,
    pretest: bytes | None = None,
    homework: bytes | None = None,
    video_url: str | None = None,
) -> dict[str, Any]:
    """Собрать черновик урока из присланных файлов; ничего не пишет в БД."""
    warnings: list[str] = []
    materials: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    if video_url:
        materials.append({"uid": "VIDEO", "node": "KON", "type": "video", "order_position": 1,
                          "title": f"Урок {lesson_num}. Видеозапись занятия",
                          "content": {"sources": [{"type": "url", "url": video_url}],
                                      "default_source": 0}})
    if konspekt is not None:
        materials.append({"uid": "KONSPEKT", "node": "KON", "type": "text", "order_position": 3,
                          "title": f"Конспект урока {lesson_num}",
                          "content": {"text": konspekt_html(konspekt), "format": "html"}})
    if pretest is not None:
        pre = parse_pretest(pretest)
        warnings += pre.warnings
        tasks += _pretest_tasks(pre.statements, "PRE") + _pretest_tasks(pre.statements, "POST")
    if homework is not None:
        hw = parse_homework(homework)
        warnings += hw.warnings
        tasks += [_homework_task(b) for b in hw.blocks]
    for t in tasks:
        bad = mixed_script_tokens(t["task_content"]["stem"])
        if bad:
            warnings.append(
                f"{t['task_content']['title']}: кириллица вместо латиницы в формулах — "
                + ", ".join(bad[:8])
            )
    if any(t["node"] == "HW" and t["task_content"]["type"] == "SA" for t in tasks):
        warnings.append("Ключи ДЗ взяты из файла автора и не сверены расчётом — проверьте каждый")
    return {
        "lesson_num": lesson_num,
        "lesson_title": lesson_title,
        "nodes": _nodes(lesson_num, any(t["node"] == "PRE" for t in tasks), bool(materials),
                        any(t["node"] == "HW" for t in tasks)),
        "materials": materials,
        "tasks": tasks,
        "warnings": warnings,
    }
