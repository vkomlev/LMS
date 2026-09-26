"""tsk-1130: план синонимов для SA-заданий курса qa-manual (офлайн, без БД).

Синонимы выбраны вручную по смыслу стема и урока (разбор — в tsk-1130; спорные
8404/8109/8165 решены оператором 2026-09-26). Скрипт только порождает все формы
каждой леммы (pymorphy3, приём tsk-796), варианты с «ё» и без, и пишет
`scripts/tsk1130_synonyms_plan.json` для `tsk1130_apply_synonyms.py`.

Запуск: .venv/Scripts/python.exe scripts/tsk1130_build_synonyms_plan.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

import pymorphy3

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1130.plan")

OUT = Path(__file__).resolve().parent / "tsk1130_synonyms_plan.json"

#: задание → (леммы, которые склоняются целиком; слова, которые пишутся как есть)
SYNONYMS: Dict[int, Dict[str, List[str]]] = {
    8379: {"lemmas": ["неинформативный", "неудачный"], "literal": ["плохо", "неинформативно"]},
    8404: {"lemmas": ["низкий"], "literal": ["низко", "minor", "major", "medium", "moderate"]},
    8109: {"lemmas": ["запрос", "код"], "literal": []},
    8161: {"lemmas": [], "literal": ["смержить", "замержить", "смерджить", "замерджить",
                                      "мержить", "мерджить", "merge"]},
    # «ожидаемый» pymorphy разбирает как причастие глагола «ожидать» — его лексема
    # тянет все глагольные формы, поэтому формы причастия перечислены вручную.
    8165: {"lemmas": ["закономерный"], "literal": [
        "закономерно", "ожидаемый", "ожидаемая", "ожидаемое", "ожидаемые", "ожидаем",
        "ожидаема", "ожидаемы", "ожидаемого", "ожидаемой", "ожидаемым", "ожидаемом",
        "ожидаемую", "ожидаемых", "ожидаемыми", "ожидаемому"]},
    9383: {"lemmas": [], "literal": ["добавить", "дописать", "расширить", "доработать", "пополнить"]},
}


def lexeme(morph: pymorphy3.MorphAnalyzer, lemma: str) -> List[str]:
    """Все формы леммы (у прилагательных — полные и краткие, без сравнительных)."""
    parse = next(p for p in morph.parse(lemma) if p.normal_form.replace("ё", "е") == lemma.replace("ё", "е"))
    forms: List[str] = []
    for form in parse.lexeme:
        if form.tag.POS not in {"NOUN", "ADJF", "ADJS", "PRTF", "PRTS"} or "Supr" in form.tag:
            continue
        for variant in (form.word, form.word.replace("ё", "е")):
            if variant not in forms:
                forms.append(variant)
    return forms


def main() -> int:
    """Собрать план и записать JSON."""
    morph = pymorphy3.MorphAnalyzer()
    plan: Dict[str, List[str]] = {}
    for task_id, spec in SYNONYMS.items():
        forms: List[str] = []
        for lemma in spec["lemmas"]:
            forms += [f for f in lexeme(morph, lemma) if f not in forms]
        forms += [w for w in spec["literal"] if w not in forms]
        plan[str(task_id)] = forms
        logger.info("%s: %d форм", task_id, len(forms))
    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("План: %s", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
