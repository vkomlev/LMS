"""tsk-796, пятый проход: словосочетания, которых не увидел четвёртый.

Проверка остатка нашла две причины пропуска.

**Причина 1: фильтр четвёртого прохода требовал только кириллицу и пробелы.**
Под него не попадали словосочетания с латиницей («вкладку Network») и с
запятой («вымышленные, но правдоподобные данные»). Из-за этого «многословных
эталонов» насчиталось 63, а на деле их 184.

**Причина 2: искали словосочетание только ПЕРВЫМ эталоном.** У задания могло
быть ["ключевое", "первичное", "ключевое поле", "первичный ключ"] — первое
значение слово, словосочетания стоят дальше, и задание в выборку не попадало.

Из 137 непокрытых заданий термины спрашивают 19; остальное — вывод программы,
код на Python и Arduino, формулы Excel, строки VBA, SQL-запросы, названия
произведений и упорядоченные перечисления. Здесь список ФРАЗ на склонение
задан руками: у задания несколько равнозначных ответов, и склонять надо не
всякий («формат по образцу» — да, «по образцу» — нет, оно уже без вершины).

Запуск (из корня LMS):
  python scripts/phrase_pass2_tsk796.py
  python scripts/apply_wordforms_tsk796.py --plan scripts/tsk796_phrase2_plan.json
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_wordforms_tsk796 import Morphology, norm, prod_dsn  # noqa: E402
from phrase_pass_tsk796 import inflect_phrase  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk796.phrase2")

OUT_PATH = Path(__file__).resolve().parent / "tsk796_phrase2_plan.json"

#: task_id → словосочетания, которые надо просклонять.
PLAN: Dict[int, List[str]] = {
    # --- пропущены из-за латиницы и запятой в эталоне ---
    8434: ["вкладку Network"],
    8074: ["вымышленные данные"],          # второй эталон с запятой не трогаем
    8076: ["тестовый номер", "тестовые данные"],
    8436: ["клиентский рендеринг"],
    8486: ["соответствие с реальным миром"],
    8487: ["помощь в распознавании и исправлении ошибок"],
    8714: ["компонентное тестирование", "модульное тестирование"],
    9274: ["эмуляция в браузере"],
    9702: ["маркеры неопределённости"],
    9755: ["обратная связь"],
    9988: ["формат по образцу"],
    9995: ["маркер автозаполнения", "маркер заполнения"],
    # --- словосочетание стояло НЕ первым эталоном ---
    7546: ["область заметок"],
    7615: ["ключевое поле", "первичный ключ"],
    7636: ["Короткий текст"],
    7763: ["столбчатая диаграмма"],
    7764: ["столбчатая диаграмма"],
    8393: ["похожий механизм"],
    10289: ["ожидаемое поведение"],
    8311: ["одновременная работа двух пользователей с одной записью"],
}


def main() -> int:
    morph = Morphology()
    conn = psycopg2.connect(**prod_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    items: List[Dict[str, Any]] = []
    for task_id, phrases in sorted(PLAN.items()):
        cur.execute(
            "SELECT solution_rules->'short_answer'->'accepted_answers' AS accepted, "
            "       task_content->>'stem' AS stem "
            "FROM tasks WHERE id = %s AND is_active = true",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            logger.warning("%s: задание не найдено или выключено — пропуск", task_id)
            continue
        accepted = row["accepted"] or []
        if not accepted:
            logger.warning("%s: эталона нет — пропуск", task_id)
            continue
        existing = {norm(a.get("value") or "") for a in accepted}

        # Склоняемая фраза обязана уже стоять среди принимаемых ответов: иначе
        # это не расширение эталона, а подмена его другим словом.
        missing = [p for p in phrases if norm(p) not in existing]
        if missing:
            logger.warning("%s: фразы %s нет среди эталонов — пропуск", task_id, missing)
            continue

        new: List[str] = []
        seen: set = set()
        for phrase in phrases:
            for form in inflect_phrase(morph, phrase, task_id):
                key = norm(form)
                if key in existing or key in seen:
                    continue
                seen.add(key)
                new.append(form)
        if not new:
            logger.info("%s: добавлять нечего", task_id)
            continue

        items.append({
            "task_id": task_id,
            "etalon": accepted[0].get("value"),
            "lemma": "; ".join(phrases),
            "add": new,
            "before": accepted,
            "stem": (row["stem"] or "").strip(),
        })
        logger.info("%s [%s] +%d: %s", task_id, "; ".join(phrases), len(new), "; ".join(new))

    conn.close()
    OUT_PATH.write_text(
        json.dumps({"generated_at": date.today().isoformat(), "task": "tsk-796",
                    "kind": "phrase-pass-2", "apply": items, "skipped": []},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info("План: %s — заданий %d, форм %d",
                OUT_PATH, len(items), sum(len(i["add"]) for i in items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
