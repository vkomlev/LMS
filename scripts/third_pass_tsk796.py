"""tsk-796, третий проход: два класса, которые пропустили первые два.

Проверка «не осталось ли чего» по текущему состоянию базы нашла две дыры.

**Дыра 1: вопрос-«назови понятие» с блоком кода в условии.** Первый проход
считал любой `<pre>`/`<code>` признаком того, что ответ — вывод программы, и
выбрасывал задание целиком. Но урок часто показывает код и спрашивает НЕ его
вывод: «Данные, введённые через input(), возвращаются программе в виде…»,
«Функция, которая вызывает сама себя, называется… функцией». Падеж там так же
не важен, как и везде.

**Дыра 2: задания, у которых эталонов изначально было БОЛЬШЕ ОДНОГО.** Исходная
выборка бралась по `jsonb_array_length(...) = 1`, поэтому задание с парой
«отчёт»/«отчет» (только буква «ё») или с синонимами «материнская»/«системная»
не рассматривалось вовсе — хотя падежных форм у него нет ровно так же.

Таблица ниже — не правило, а список, собранный чтением условий: для каждого
задания названы слова и часть речи, от которых строится парадигма. У синонимов
парадигма строится для каждого слова отдельно.

Запуск (из корня LMS):
  python scripts/third_pass_tsk796.py
  python scripts/apply_wordforms_tsk796.py --plan scripts/tsk796_third_plan.json
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_wordforms_tsk796 import Morphology, norm, prod_dsn  # noqa: E402
from second_pass_tsk796 import paradigm  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk796.third")

OUT_PATH = Path(__file__).resolve().parent / "tsk796_third_plan.json"

#: task_id → [(начальная форма, часть речи)]. Несколько пар = синонимы.
PLAN: Dict[int, List[Tuple[str, str]]] = {
    # --- дыра 1: «назови понятие» при блоке кода в условии ---
    5838: [("словарь", "NOUN")],        # в какой структуре данных хранить
    6171: [("словарь", "NOUN")],        # то же задание в другом курсе
    5908: [("строка", "NOUN")],         # input() возвращает данные в виде…
    5914: [("неполный", "ADJF")],       # условный оператор без else
    5980: [("подпрограмма", "NOUN")],
    5983: [("массив", "NOUN")],
    6120: [("индекс", "NOUN")],
    6227: [("присваивание", "NOUN")],
    6237: [("рекурсивный", "ADJF")],
    6239: [("длина", "NOUN")],          # len() возвращает длину списка
    7739: [("командный", "ADJF")],      # командный режим
    7740: [("переменный", "ADJF")],   # «переменная» — субстантивированное прилагательное
    7772: [("умножение", "NOUN")],      # символ «*» означает умножение
    # --- дыра 1б: ложное срабатывание стоп-слова «поисков» ---
    9137: [("запрос", "NOUN")],         # «называются поисковым запросом»
    # --- дыра 2: заданиям с несколькими эталонами не хватает падежей ---
    7397: [("материнский", "ADJF"), ("системный", "ADJF")],
    7429: [("ввод", "NOUN")],
    7437: [("ввод", "NOUN")],
    7570: [("счётчик", "NOUN")],
    7588: [("отчёт", "NOUN")],
    7590: [("отчёт", "NOUN")],
    7603: [("распределённый", "ADJF")],
    7655: [("отмена", "NOUN")],         # «отменить» — глагол, формы не нужны
    7737: [("таблица", "NOUN")],
    8027: [("анимация", "NOUN")],
    8040: [("валидация", "NOUN")],
    8053: [("оплата", "NOUN")],
    8142: [("ключ", "NOUN")],
    8143: [("логический", "ADJF")],     # «булев»/«булево» уже перечислены
    8238: [("граница", "NOUN")],
    8253: [("разный", "ADJF")],
    8717: [("компонентный", "ADJF"), ("модульный", "ADJF")],
    8971: [("анимация", "NOUN")],
    9024: [("токен", "NOUN")],
    10119: [("линейный", "ADJF")],
    10170: [("сообщение", "NOUN")],
    10193: [("оплата", "NOUN")],
}


def main() -> int:
    morph = Morphology()
    conn = psycopg2.connect(**prod_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    items: List[Dict[str, Any]] = []
    for task_id, bases in sorted(PLAN.items()):
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

        forms: List[str] = []
        for base, pos in bases:
            forms.extend(paradigm(morph, base, pos))

        seen: set = set()
        new: List[str] = []
        for form in forms:
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
            "lemma": ", ".join(b for b, _ in bases),
            "add": new,
            "before": accepted,
            "stem": (row["stem"] or "").strip(),
        })
        logger.info("%s [%s] +%d: %s", task_id,
                    ", ".join(b for b, _ in bases), len(new), ", ".join(new))

    conn.close()
    OUT_PATH.write_text(
        json.dumps({"generated_at": date.today().isoformat(), "task": "tsk-796",
                    "kind": "third-pass", "apply": items, "skipped": []},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info("План: %s — заданий %d, форм %d",
                OUT_PATH, len(items), sum(len(i["add"]) for i in items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
