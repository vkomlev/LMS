# -*- coding: utf-8 -*-
"""tsk-350: поиск групп дублей заданий ЕГЭ.

Критерий (двухфакторный, устойчивый к общей преамбуле — урок tsk-316):

  Внутри одной темы (номер задания ЕГЭ) у всех заданий общая «обвязка»
  («определите, какому столбцу таблицы истинности соответствует...»).
  Поэтому похожесть считается ТОЛЬКО по РЕДКИМ фрагментам текста —
  тем, что встречаются в теме у единиц заданий. Частая обвязка
  автоматически выпадает из подписи и на счёт похожести не влияет.

  1. Подпись задания = множество редких 5-словных фрагментов нормализованного
     текста (частота внутри темы <= порога). Это формула, числа таблицы,
     специфичные детали условия.
  2. ЭТАЛОННЫЙ ОТВЕТ (solution_rules) — обязательный второй фактор.
     Тот же вопрос => тот же ответ. Разные ответы при похожем тексте —
     это «та же база данных / та же преамбула, но другой вопрос», НЕ дубль.
     Ответ известен у 98,6% активных заданий.
  3. ЗНАЧИМЫЕ ЧИСЛА условия (числа от 2 знаков) — третий фактор.
     Ответ может совпасть случайно (у «Задания 13» ответ часто двузначный),
     но набор чисел условия (IP-адреса, маски, разряды) у разных заданий
     разный. Требуется ПОЛНОЕ вхождение меньшего набора в больший:
     лишние числа обёртки источника («задание 13_7017») допустимы,
     а изменённое число условия (172.16.168.0 -> 204.16.168.0) — нет.
  4. Дубль = совпал ответ И вложенность подписей >= 0.55 И полное
     совпадение значимых чисел И совпали приложенные файлы.
     Нет ответа хотя бы у одного — в спорные, оператору.

  Задания одного типа, отличающиеся только числами, дают РАЗНЫЕ редкие
  фрагменты (числа входят в текст) => не склеиваются.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from normalize import canon_text, payload_of, task_text

HERE = Path(__file__).parent
DUMP = HERE / "tasks_dump.json"

SHINGLE = 5
CONTAINMENT_MIN = 0.55
SIG_MIN = 4            # меньше — подпись недостоверна, группа уходит в спорные
NUM_MIN = 1.0          # значимые числа меньшего набора обязаны совпасть все
FORMULA_MIN = 0.60     # вложенность n-грамм формулы/полезной нагрузки
FORMULA_MIN_TOKENS = 12  # короче — нагрузки нет, фактор не применяется
SHORT_TEXT = 40        # слов; короткое условие — порог похожести поднимается
SHORT_CONTAINMENT = 0.95
RARE_FRACTION = 0.12   # доля заданий темы, выше которой фрагмент считается обвязкой

# соответствие подкурса «Сложные» -> базовый курс темы
HARD_RE = re.compile(r"^lms:tsk347:hard:(\d+)$")


def load() -> list[dict]:
    rows = json.loads(DUMP.read_text(encoding="utf-8"))
    for r in rows:
        for k in ("task_content", "solution_rules", "difficulty_provenance"):
            v = r.get(k)
            if isinstance(v, str):
                r[k] = json.loads(v) if v else None
        r["task_content"] = r["task_content"] or {}
    return rows


def theme_of(row: dict) -> int:
    """Номер темы = id базового курса (подкурс «Сложные» сводится к базовому)."""
    m = HARD_RE.match(row["course_uid"] or "")
    return int(m.group(1)) if m else int(row["course_id"])


def answer_of(row: dict) -> str:
    """Нормализованный эталонный ответ задания ('' — ответа нет)."""
    sr = row.get("solution_rules") or {}
    if not isinstance(sr, dict):
        return ""
    sa = sr.get("short_answer") or {}
    vals = []
    if isinstance(sa, dict):
        for a in sa.get("accepted_answers") or []:
            v = a.get("value") if isinstance(a, dict) else a
            if v not in (None, ""):
                vals.append(re.sub(r"\s+", "", str(v)).lower())
    if vals:
        return "SA:" + "|".join(sorted(set(vals)))
    if sr.get("text_answer"):
        return "TXT:" + re.sub(r"\s+", " ", str(sr["text_answer"])).strip().lower()
    co = sr.get("correct_options") or []
    if co:
        return "OPT:" + "|".join(sorted(map(str, co)))
    return ""


def formula_grams(payload: str, n: int = 4) -> set[str]:
    """Отпечаток формулы: n-граммы полезной нагрузки (символы, переменные, числа).

    Порядок важен: «x = !y > (x & w) = (z & !w)» и «x = (y > z) & (y = !(z > w))»
    состоят из одних и тех же символов, но это РАЗНЫЕ функции. Множество
    символов их не различает, последовательности из четырёх — различают.
    """
    toks = payload.split()
    if len(toks) < n:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def numbers_of(text: str) -> Counter:
    """Значимые числа условия: от двух знаков (однозначные — шум разметки)."""
    return Counter(m.group(0) for m in re.finditer(r"\d{2,}", text))


def num_containment(a: Counter, b: Counter) -> float:
    """Доля общих значимых чисел от меньшего набора."""
    if not a or not b:
        return 1.0  # чисел нет вообще — фактор неинформативен, решают другие
    common = sum((a & b).values())
    return common / min(sum(a.values()), sum(b.values()))


def files_of(row: dict) -> tuple:
    """Отпечаток приложенных файлов задания."""
    tc = row["task_content"]
    paths = tc.get("attached_file_paths") or []
    if isinstance(paths, str):
        paths = [paths]
    names = sorted(str(p).rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower() for p in paths)
    return tuple(names)


def shingles(text: str, n: int = SHINGLE) -> set[str]:
    toks = text.split()
    if len(toks) < n:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def build() -> tuple[list[dict], dict[int, list[dict]]]:
    rows = load()
    for r in rows:
        r["text"] = canon_text(task_text(r["task_content"]))
        r["payload"] = payload_of(r["text"])
        r["theme"] = theme_of(r)
        r["files"] = files_of(r)
        r["answer"] = answer_of(r)
        r["nums"] = numbers_of(r["text"])
        r["fgrams"] = formula_grams(r["payload"])
        r["shingles"] = shingles(r["text"])
    by_theme: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_theme[r["theme"]].append(r)
    return rows, by_theme


def signatures(group: list[dict]) -> None:
    """Оставить в подписи только редкие для темы фрагменты."""
    df: Counter[str] = Counter()
    for r in group:
        df.update(r["shingles"])
    limit = max(2, math.ceil(RARE_FRACTION * len(group)))
    for r in group:
        r["sig"] = {s for s in r["shingles"] if df[s] <= limit}


def containment(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class DSU:
    def __init__(self) -> None:
        self.p: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def find_groups(only_active: bool = True) -> tuple[list[dict], list[dict]]:
    rows, by_theme = build()
    groups: list[dict] = []
    weak: list[dict] = []

    for theme, grp in sorted(by_theme.items()):
        pool = [r for r in grp if r["is_active"]] if only_active else list(grp)
        if len(pool) < 2:
            continue
        signatures(grp)  # частоты считаем по всей теме, включая скрытые
        # обратный индекс по редким фрагментам — только реальные кандидаты
        inv: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(pool):
            for s in r["sig"]:
                inv[s].append(i)
        cand: set[tuple[int, int]] = set()
        for ids in inv.values():
            if len(ids) < 2 or len(ids) > 12:
                continue
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    cand.add((ids[i], ids[j]))

        dsu = DSU()
        pairs: list[tuple[int, int, float]] = []
        weak_pairs: list[tuple[int, int, float, str]] = []
        for i, j in cand:
            a, b = pool[i], pool[j]
            c = containment(a["sig"], b["sig"])
            if c < CONTAINMENT_MIN:
                continue

            # Все факторы считаем сразу — классификация ниже, а не «первый провал».
            # РАЗЛИЧАЮЩИЕ факторы: их провал => это РАЗНЫЕ задания. Не дубль и НЕ спорное
            # (иначе список спорных заполняется очевидными вариациями по числам).
            nc = num_containment(a["nums"], b["nums"])
            payload_len = min(len(a["payload"].split()), len(b["payload"].split()))
            fc = containment(a["fgrams"], b["fgrams"])
            fc_applies = payload_len >= FORMULA_MIN_TOKENS
            ans_diff = a["answer"] and b["answer"] and a["answer"] != b["answer"]

            # ПОДТВЕРЖДАЮЩИЕ факторы (их нехватка => спорное, если всё различающее сошлось)
            files_ok = a["files"] == b["files"]
            sig_ok = min(len(a["sig"]), len(b["sig"])) >= SIG_MIN
            words = min(len(a["text"].split()), len(b["text"].split()))
            short_ok = not (words < SHORT_TEXT and c < SHORT_CONTAINMENT)
            ans_both = bool(a["answer"] and b["answer"])

            # Различающее сошлось? (нужно для «пограничного» статуса)
            nums_ok = nc >= NUM_MIN
            formula_ok = (not fc_applies) or fc >= FORMULA_MIN

            if ans_diff:
                continue  # разный ответ — уверенно разные задания
            if not nums_ok and not ans_both:
                continue  # числа разошлись и ответом не подтвердить — разные задания
            if not formula_ok and not (ans_both and nums_ok):
                continue  # формула разошлась и нет сильного подтверждения — разные

            if files_ok and sig_ok and short_ok and ans_both and nums_ok and formula_ok:
                pairs.append((i, j, c))
                dsu.union(i, j)
                continue

            # Иначе — спорное. Причина = самый сильный сигнал сомнения.
            if not nums_ok:      # ответ совпал, но число условия изменено (Крылов-клон)
                why, metric = "числа условия расходятся при совпавшем ответе", f"числа {nc:.2f}"
            elif not formula_ok:  # текст+ответ сошлись, формула другая
                why, metric = "формула условия расходится", f"формула {fc:.2f}"
            elif not files_ok:
                why, metric = "разные приложенные файлы (остальное совпало)", ""
            elif not ans_both:
                why, metric = "нет эталонного ответа (числа и формула совпали)", ""
            elif not short_ok:
                why, metric = "короткое условие, похожесть неполная", ""
            else:
                why, metric = "слишком короткая подпись", ""
            weak_pairs.append((i, j, c, why, metric))

        clusters: dict[int, list[int]] = defaultdict(list)
        for i in range(len(pool)):
            if i in dsu.p:
                clusters[dsu.find(i)].append(i)
        score = {(i, j): c for i, j, c in pairs}
        for members in clusters.values():
            if len(members) < 2:
                continue
            groups.append({
                "theme": theme,
                "members": [pool[i] for i in sorted(members)],
                "min_containment": min(
                    (c for (i, j), c in score.items() if i in members and j in members),
                    default=0.0,
                ),
            })
        for i, j, c, why, metric in weak_pairs:
            weak.append({"theme": theme, "members": [pool[i], pool[j]],
                         "containment": c, "reason": why, "metric": metric})
    return groups, weak


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    gs, weak = find_groups()
    extra = sum(len(g["members"]) - 1 for g in gs)
    print(f"групп дублей: {len(gs)}, избыточных заданий: {extra}")
    print(f"спорных пар: {len(weak)}")
