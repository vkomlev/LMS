"""tsk-796, четвёртый проход: эталоны из НЕСКОЛЬКИХ слов.

Первые три прохода работали с эталоном-одним словом — так была очерчена
постановка. Но дефект тот же и у словосочетаний: на вопрос «Какая техника
тест-дизайна?» эталон записан как «граничные значения», и ученик, ответивший
«граничных значений», получает незачёт.

Отличие от одного слова: склонять надо не слово, а СЛОВОСОЧЕТАНИЕ, согласуя
части между собой. Разбор такой:

  * **вершина** — первое существительное, с которым согласуются все стоящие
    перед ним прилагательные и причастия («граничные ЗНАЧЕНИЯ»);
  * **зависимый хвост** после вершины не склоняется («критерии ПРИЁМКИ» →
    «критериев приёмки», а не «критериев приёмок»; «деление НА НОЛЬ»);
  * если согласования нет, вершина — первое слово. Так разводятся «граничные
    значения» (прилагательное + существительное, согласованы) и «переменные
    окружения» (существительное + родительный падеж: «переменные» именительный
    множественного, «окружения» родительный единственного — не согласованы);
  * союзы и предлоги внутри словосочетания не склоняются вовсе
    («прерванное ИЛИ повторное действие»).

Список заданий собран чтением условий. Не включены выводы программ
(«Загадано больше», «Да Нет»), названия произведений и курсов из условия,
перечисления глаголов и целые придаточные («критичных открытых дефектов не
осталось»).

Запуск (из корня LMS):
  python scripts/phrase_pass_tsk796.py
  python scripts/apply_wordforms_tsk796.py --plan scripts/tsk796_phrase_plan.json
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_wordforms_tsk796 import Morphology, norm, prod_dsn, yo_variants  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk796.phrase")

OUT_PATH = Path(__file__).resolve().parent / "tsk796_phrase_plan.json"

#: Задания с эталоном-словосочетанием, которые действительно спрашивают термин.
TASKS = [
    6394, 6519, 6801, 7101, 8010, 8012, 8170, 8176, 8189, 8191, 8193, 8194,
    8236, 8237, 8240, 8249, 8298, 8305, 8310, 8317, 8323, 8326, 8342, 8343,
    8349, 8355, 8356, 8382, 8431, 8445, 8453, 8458, 8476, 8479, 9163, 9174,
    9193, 9232, 9242, 9243, 9244, 9343, 9353, 9372, 9373, 9374, 9744,
]

#: Падежи, по которым гоняем словосочетание.
CASES = ("nomn", "gent", "datv", "accs", "ablt", "loct")

#: Части речи, которые внутри словосочетания не изменяются.
INVARIANT_POS = {"CONJ", "PREP", "PRCL", "ADVB", "NPRO"}

#: Части речи, которые согласуются с вершиной.
AGREEING_POS = {"ADJF", "PRTF"}


#: Вершина, названная руками там, где разбор её не находит.
#: task_id → (индекс слова, часть речи вершины).
HEAD_OVERRIDE: Dict[int, Tuple[int, str]] = {
    # «переменные» здесь субстантивировано, «окружения» — родительный падеж
    # при нём. Формально «окружения» тоже читается как именительный
    # множественного и «согласуется», поэтому автомат берёт вершиной его.
    6394: (0, "ADJF"),
    # «дорогой» имеет разбор существительным (творительный от «дорога»),
    # и автомат уходит по нему.
    9174: (1, "ADJF"),
}


def _parses(morph: Morphology, word: str, wanted: Optional[set] = None):
    """Разборы слова, при необходимости — только нужных частей речи.

    Пустой список, если подходящих нет: подстановка «первого попавшегося»
    делала вершиной прилагательное («первичного ключ»).
    """
    parses = morph._morph.parse(word)  # noqa: SLF001
    if wanted is None:
        return parses
    return [p for p in parses if p.tag.POS in wanted]


def _best(morph: Morphology, word: str, wanted: Optional[set] = None):
    """Первый подходящий разбор или None."""
    found = _parses(morph, word, wanted)
    return found[0] if found else None


def _agrees(morph: Morphology, before: List[str], head) -> bool:
    """Все слова перед вершиной либо согласуются с ней, либо неизменяемы."""
    for token in before:
        role = _best(morph, token)
        if role is None:
            return False
        if role.tag.POS in INVARIANT_POS:
            continue
        if role.tag.POS not in AGREEING_POS:
            # Существительное перед кандидатом — значит кандидат не вершина, а
            # зависимое («тур ДОСТАВКИ»). Проверять «есть ли хоть один разбор
            # прилагательным» нельзя: у «тур» их два десятка, и согласование
            # находилось всегда.
            return False
        variants = _parses(morph, token, AGREEING_POS)
        if not any(v.tag.case == head.tag.case and v.tag.number == head.tag.number
                   for v in variants):
            return False
    return True


def find_head(morph: Morphology, tokens: List[str], task_id: int):
    """Индекс и разбор вершины словосочетания.

    Вершина — первое существительное, с которым согласуются ВСЕ стоящие перед
    ним изменяемые слова. Перебираются ВСЕ разборы существительного: у
    «значения» первый разбор — родительный единственного, и по нему согласование
    с «граничные» не находится, хотя по именительному множественного находится.
    """
    override = HEAD_OVERRIDE.get(task_id)
    if override:
        idx, pos = override
        variants = _parses(morph, tokens[idx], {pos})
        # Именительный, если он есть: у «дорогой» первым идёт разбор
        # творительным падежом женского рода («дорогой» от «дорога»).
        parse = next((v for v in variants if v.tag.case == "nomn"), None)
        if parse is None and variants:
            parse = variants[0]
        if parse is not None:
            return idx, parse

    # Берём САМОЕ ПРАВОЕ существительное, с которым согласовано всё слева.
    # Левое подошло бы формально: у «неверный» есть разбор существительным
    # («неверный» как человек), и перед ним не стоит ничего — «согласование»
    # выполняется пусто, вершиной становится прилагательное.
    found = None
    for idx, token in enumerate(tokens):
        for head in _parses(morph, token, {"NOUN"}):
            if _agrees(morph, tokens[:idx], head):
                found = (idx, head)
                break
    if found:
        return found
    # Существительного-вершины нет: вершина — последнее прилагательное.
    for idx in range(len(tokens) - 1, -1, -1):
        parse = _best(morph, tokens[idx], AGREEING_POS)
        if parse is not None:
            return idx, parse
    return 0, _best(morph, tokens[0])


def inflect_phrase(morph: Morphology, phrase: str, task_id: int) -> List[str]:
    """Все падежные формы словосочетания (число вершины — как в эталоне и второе)."""
    tokens = phrase.split()
    if len(tokens) < 2:
        return []
    head_idx, head = find_head(morph, tokens, task_id)
    if head is None:
        return []

    numbers = [head.tag.number]
    if head.tag.POS == "NOUN" and head.tag.number == "sing":
        numbers.append("plur")
    elif head.tag.POS == "NOUN" and head.tag.number == "plur":
        numbers.append("sing")

    out: List[str] = []
    for number in numbers:
        for case in CASES:
            grammemes = {case}
            if number:
                grammemes.add(number)
            head_form = head.inflect(grammemes)
            if head_form is None:
                continue
            parts: List[str] = []
            broken = False
            for idx, token in enumerate(tokens):
                if idx == head_idx:
                    parts.append(head_form.word)
                    continue
                if idx > head_idx:
                    parts.append(token.lower())
                    continue
                parse = _best(morph, token, AGREEING_POS)
                if parse is None:
                    parts.append(token.lower())
                    continue
                agreed = {case}
                if head_form.tag.number:
                    agreed.add(head_form.tag.number)
                if head_form.tag.number == "sing" and head_form.tag.gender:
                    agreed.add(head_form.tag.gender)
                if case == "accs" and head_form.tag.animacy:
                    # Одушевлённость помечена у прилагательного ТОЛЬКО в
                    # винительном падеже. Без неё винительный расходится внутри
                    # словосочетания («тёплого круг» вместо «тёплый круг»), а
                    # если требовать её везде — согласование ломается совсем.
                    agreed.add(head_form.tag.animacy)
                mod = parse.inflect(agreed)
                if mod is None:
                    broken = True
                    break
                parts.append(mod.word)
            if broken:
                continue
            out.extend(yo_variants(" ".join(parts)))
    return out


def main() -> int:
    morph = Morphology()
    conn = psycopg2.connect(**prod_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    items: List[Dict[str, Any]] = []
    for task_id in TASKS:
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

        forms = inflect_phrase(morph, etalon, task_id)
        seen: set = set()
        new: List[str] = []
        for form in forms:
            key = norm(form)
            if key in existing or key in seen:
                continue
            seen.add(key)
            new.append(form)
        if not new:
            logger.info("%s [%s]: добавлять нечего", task_id, etalon)
            continue

        items.append({
            "task_id": task_id,
            "etalon": etalon,
            "lemma": etalon,
            "add": new,
            "before": accepted,
            "stem": (row["stem"] or "").strip(),
        })
        logger.info("%s [%s] +%d: %s", task_id, etalon, len(new), "; ".join(new))

    conn.close()
    OUT_PATH.write_text(
        json.dumps({"generated_at": date.today().isoformat(), "task": "tsk-796",
                    "kind": "phrase-pass", "apply": items, "skipped": []},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info("План: %s — заданий %d, форм %d",
                OUT_PATH, len(items), sum(len(i["add"]) for i in items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
