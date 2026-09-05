"""tsk-796, второй проход: то, что первый прогон отложил, а человек разобрал.

Решение оператора 05.09 по итогам первого прохода:
  * группу «условие само требует падеж/число» — **расширять** (ученик, знающий
    понятие, не должен терять балл за форму; инструкция в условии остаётся
    подсказкой, а не капканом);
  * пройти вручную по 96 заданиям, где вопрос не опознался как «назови понятие»
    (курсы по тестированию формулируют «Ответь одним словом», «К какой категории
    относится», а не «называется»);
  * добить задания с неоднозначной леммой и нераспознанной частью речи.

Списки ниже собраны ЧТЕНИЕМ УСЛОВИЙ, а не правилом: автомат этот класс не берёт
именно потому, что формулировки разнородны. В `AUTO` — задания, где морфология
сама даёт верную лемму; в `OVERRIDE` — где лемму приходится назвать руками
(«бит» разбирается как краткое прилагательное от «битый», «прямым» — как
наречие, «разветвляющимся» — причастие).

Не включены и включены не будут:
  * ответ-точная строка (вывод программы, слово, вычеркнутое из текста, имя
    папки из пути, слово из поискового запроса);
  * глаголы и инфинитивы («обезличить», «дублировать»): нужную ученику
    альтернативу даёт другой ВИД глагола, то есть другая лемма, — морфология
    одной леммы тут не помогает;
  * несклоняемое («видео», «юзабилити», «бадди») и аббревиатуры («Гц», «СУБД»);
  * сравнительная степень («больше», «дешевле») — форм у неё нет.

Запуск (из корня LMS):
  python scripts/second_pass_tsk796.py
  python scripts/apply_wordforms_tsk796.py --plan scripts/tsk796_second_plan.json
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
from gen_wordforms_tsk796 import Morphology, norm, prod_dsn, yo_variants  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk796.second")

OUT_PATH = Path(__file__).resolve().parent / "tsk796_second_plan.json"

#: Группа A — условие само называет падеж или число. Оператор решил расширять.
GROUP_A = [
    5431, 7271, 7406, 7418, 7484, 7499, 7591, 7592, 7593, 7607, 7608, 7609,
    7610, 7612, 7624, 7638, 7669, 7677, 7758, 7784, 7785, 7821, 7829, 7839,
    7934, 8353, 8468, 9081, 10068, 10082,
]

#: Группа E — вопрос-«назови понятие», не пойманный шаблоном. Отобрано чтением.
GROUP_E = [
    6395, 6449, 6465, 6648, 6661, 6663, 7006, 7032, 7034, 7081,
    7468, 7473, 7474, 7475, 7477, 7571, 7572, 7573, 7574, 7665,
    8024, 8039, 8062, 8065, 8073, 8083, 8084, 8098, 8109, 8117,
    8118, 8120, 8152, 8154, 8177, 8180, 8216, 8217, 8218, 8241,
    8286, 8287, 8299, 8344, 8379, 8391, 8401, 8402, 8404, 8422,
    8423, 8447, 8455, 8490, 8556, 8557, 8585, 8690, 8716, 8733,
    9013, 9014, 9032, 9254, 9263, 9283, 9312, 9314, 9384, 9392,
    10150,
]

#: Лемма, названная руками: морфология выбрала не ту часть речи или не то слово.
#: task_id → начальная форма, от которой строится парадигма.
OVERRIDE: Dict[int, Tuple[str, str]] = {
    # группа A
    6100: ("столбец", "NOUN"),          # разобрано как лемма «столбцы»
    # группа E, неоднозначная лемма
    6915: ("красный", "ADJF"),          # «красная зона»
    9382: ("полнота", "NOUN"),          # «ничего не говорит о его полноте»
    8202: ("продакшен", "NOUN"),        # слова нет в словаре, формы — руками
    # группа F — неоднозначная лемма
    5780: ("бит", "NOUN"),              # первым разбором идёт краткое «битый»
    5476: ("раздел", "NOUN"),
    5507: ("растр", "NOUN"),
    7321: ("данные", "NOUN"),           # формы — руками, см. MANUAL_FORMS
    8922: ("логика", "NOUN"),
    6061: ("прямой", "ADJF"),           # «прямым кодом», не геометрическая прямая
    6150: ("авторский", "ADJF"),
    8522: ("свёрнутый", "PRTF"),
    8693: ("ориентированный", "PRTF"),
    8768: ("численный", "ADJF"),
    8817: ("прямой", "ADJF"),           # «по прямой связи»
    7688: ("основной", "ADJF"),
    # группа H — часть речи распозналась неверно
    5899: ("разветвляющийся", "PRTF"),  # причастие в роли прилагательного
    8866: ("развёрнутый", "PRTF"),
    7808: ("минус", "NOUN"),            # разобрано как предлог
    7809: ("плюс", "NOUN"),
}


#: Слова, у которых парадигму пишем целиком руками.
MANUAL_FORMS: Dict[int, List[str]] = {
    # «Продакшена» нет в словаре — морфология угадывала «продакшено».
    8202: ["продакшена", "продакшену", "продакшеном", "продакшене",
           "продакшены", "продакшенов", "продакшенам", "продакшенами", "продакшенах"],
    # «Данные» в значении «сведения» — существительное только во множественном
    # числе. Морфология считает его прилагательным «данный» и предлагает
    # «данная», «данное» — для термина это не формы, а другое слово.
    7321: ["данных", "данным", "данными"],
}

#: Граммемы, которые обязаны совпасть с исходной формой. У причастия лексема
#: тянет ВСЕ причастия глагола — «ориентированный» и «ориентирующий» лежат в
#: одной лексеме, но это разные слова, а не разные падежи одного.
_FIXED_GRAMMEMES = ("Supr", "actv", "pssv", "past", "pres", "perf", "impf")


def paradigm(morph: Morphology, base: str, pos: str) -> List[str]:
    """Формы слова, заданного руками: часть речи названа явно.

    Автовыбор здесь не годится ни в каком виде: у «бит» первый разбор — краткое
    прилагательное от «битый», у «минус» — предлог, а если предпочесть
    существительное, то «прямой» уедет в геометрическую «прямую» вместо
    «прямого кода». Часть речи знает только человек, читавший условие.
    """
    parses = morph._morph.parse(base)  # noqa: SLF001 — намеренный доступ к анализатору
    parse = next((p for p in parses if p.tag.POS == pos), None)
    if parse is None:
        raise SystemExit(f"у слова {base!r} нет разбора как {pos}")
    signature = {g for g in _FIXED_GRAMMEMES if g in parse.tag}
    forms: List[str] = []
    for item in parse.lexeme:
        if item.tag.POS != pos:
            continue
        if {g for g in _FIXED_GRAMMEMES if g in item.tag} != signature:
            continue
        forms.extend(yo_variants(item.word))
    return forms


def main() -> int:
    morph = Morphology()
    conn = psycopg2.connect(**prod_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    task_ids = sorted(set(GROUP_A) | set(GROUP_E) | set(OVERRIDE))
    items: List[Dict[str, Any]] = []

    for task_id in task_ids:
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
        etalon = (accepted[0].get("value") or "").strip()
        existing = {norm(a.get("value") or "") for a in accepted}

        override = OVERRIDE.get(task_id)
        base = override[0] if override else None
        if task_id in MANUAL_FORMS:
            forms = MANUAL_FORMS[task_id]
            lemma = base or etalon
        elif override:
            forms = paradigm(morph, override[0], override[1])
            lemma = base
        else:
            lemma, forms, reasons = morph.analyze(etalon)
            if reasons:
                logger.warning("%s [%s]: морфология отказала (%s) — пропуск",
                               task_id, etalon, "; ".join(reasons))
                continue

        new = [f for f in forms if norm(f) not in existing]
        seen: set = set()
        new = [f for f in new if not (norm(f) in seen or seen.add(norm(f)))]
        if not new:
            logger.info("%s [%s]: добавлять нечего", task_id, etalon)
            continue

        items.append({
            "task_id": task_id,
            "etalon": etalon,
            "lemma": lemma,
            "add": new,
            "before": accepted,
            "stem": (row["stem"] or "").strip(),
            "manual_lemma": bool(base),
        })
        logger.info("%s [%s%s] +%d: %s", task_id, etalon,
                    f" → {base}" if base else "", len(new), ", ".join(new))

    conn.close()
    OUT_PATH.write_text(
        json.dumps({"generated_at": date.today().isoformat(), "task": "tsk-796",
                    "kind": "second-pass", "apply": items, "skipped": []},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info("План: %s — заданий %d, форм %d",
                OUT_PATH, len(items), sum(len(i["add"]) for i in items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
