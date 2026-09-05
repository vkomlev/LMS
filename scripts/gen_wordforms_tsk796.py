"""tsk-796: порождение словоформ для эталонов заданий «впиши слово» (офлайн).

Зачем. Вопрос «Все цифры системы счисления вместе называют её…» грамматически
требует творительного падежа, эталон записан как «алфавитом», а ученик отвечает
словарной формой «алфавит» — и получает незачёт, будучи прав по-русски. Движок
проверки (`CheckingService._check_short_answer`) сравнивает строки и морфологии
не знает — по решению оператора (вариант А, tsk-796) он и не должен: словоформы
порождаются здесь, ОДИН раз, вычитываются человеком и ложатся в
`solution_rules.short_answer.accepted_answers`.

Что делает скрипт (read-only, ничего не пишет):
  1. снимает с боевой базы активные задания, у которых ровно один эталон и он —
     одно слово на кириллице;
  2. разбирает эталон морфологией (pymorphy3) и порождает формы ТОЙ ЖЕ леммы;
  3. отсеивает опасное: неоднозначную лемму («вести»/«весть», «три»),
     столкновение порождённой формы со СЛОВОМ ДРУГОЙ ЛЕММЫ, которое уже служит
     эталоном где-то в банке (та самая «омонимия предметной области»);
  4. кладёт план в JSON и отчёт для вычитки человеком в docs/qa/.

Синонимы НЕ порождаются: только формы одной леммы. Существующие эталоны не
удаляются и не переписываются — план строго на добавление, балл добавленной
формы равен баллу исходного эталона.

Зависимость `pymorphy3` — офлайн-инструмент авторинга, ставится в venv LMS и
СОЗНАТЕЛЬНО не добавлена в requirements.txt: боевой код морфологию не вызывает.

Запуск (из корня LMS):
  python scripts/gen_wordforms_tsk796.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger("tsk796.gen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = PROJECT_ROOT / "scripts" / "tsk796_wordforms_plan.json"
REPORT_PATH = PROJECT_ROOT / "docs" / "qa" / "2026-09-05-tsk796-wordforms-review.md"

#: Эталон-кандидат: одно слово кириллицей, минимум две буквы.
WORD_RE = re.compile(r"^[а-яёА-ЯЁ]{2,}$")

#: Разборы слабее этого веса — шум морфологии, лемму по ним не считаем спорной.
AMBIGUITY_SCORE_FLOOR = 0.05

#: Части речи, для которых порождение форм безопасно и осмысленно.
SAFE_POS = {"NOUN", "ADJF"}

#: Вопрос просит НАЗВАТЬ ПОНЯТИЕ — только здесь падеж ответа не важен.
NAMING_RE = re.compile(
    r"называ|носит название|термин|[…]\s*(<[^>]+>\s*)?впиши|"
    r"(впиши|напиши|введи)\s+(одно\s+)?слово|впиши\s+его",
    re.I,
)

#: Вопрос требует ТОЧНОЙ строки, а не понятия: вывод программы, расшифровка,
#: слово, удалённое из текста (там от числа букв зависит сам расчёт), имя из
#: приложенного файла. Здесь другая словоформа — прямо неверный ответ.
EXACT_ANSWER_RE = re.compile(
    r"<pre|<code|print\(|выведет|вернёт|вернет|расшифру|закодир|шифр|удалил|"
    r"кодировк|файл к заданию|заглавными буквами|поисков|получите слово",
    re.I,
)

#: Вопрос сам требует определённой грамматической формы — расширять её молча
#: нельзя, это решение методиста, а не морфологии.
FORM_REQUIRED_RE = re.compile(
    r"в\s+именительном\s+падеж|в\s+единственном\s+числ|во\s+множественном\s+числ|"
    r"в\s+родительном\s+падеж|в\s+начальной\s+форм",
    re.I,
)


def prod_dsn() -> Dict[str, Any]:
    """Параметры подключения к боевой базе из `.mcp.json` (пароль не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    parsed = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    return dict(
        host=parsed.hostname,
        port=parsed.port or 5432,
        dbname=(parsed.path or "").lstrip("/"),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def norm(value: str) -> str:
    """Нормализация «как в движке» для сравнения одиночных слов.

    Полный конвейер — `CheckingService._normalize_text` (trim → lower →
    strip_punctuation → collapse_spaces). Для одного слова без знаков это
    сводится к trim+lower, поэтому здесь ровно оно: дедуп форм и сверка
    столкновений идут по той же строке, которую сравнит движок.
    """
    return value.strip().lower()


def fetch_tasks(cur: psycopg2.extras.RealDictCursor) -> List[Dict[str, Any]]:
    """Активные задания ровно с одним эталоном-словом на кириллице."""
    cur.execute(
        """
        SELECT id,
               course_id,
               max_score,
               task_content->>'type'  AS task_type,
               task_content->>'stem'  AS stem,
               task_content->>'title' AS title,
               solution_rules->'short_answer'->'accepted_answers' AS accepted,
               solution_rules->'short_answer'->'normalization'    AS normalization,
               coalesce((solution_rules->'short_answer'->>'use_regex')::bool, false) AS use_regex
        FROM tasks
        WHERE is_active = true
          AND jsonb_typeof(solution_rules->'short_answer'->'accepted_answers') = 'array'
          AND jsonb_array_length(solution_rules->'short_answer'->'accepted_answers') = 1
        ORDER BY id
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if WORD_RE.match((r["accepted"][0].get("value") or "").strip())]


def fetch_vocabulary(cur: psycopg2.extras.RealDictCursor) -> Set[str]:
    """Словарь предметной области: все эталоны-слова банка (нормализованные).

    Нужен для проверки столкновений: если порождённая форма совпала со словом,
    которое где-то в банке само служит эталоном, а лемма у него другая — это
    ровно тот случай, ради которого постановка требует вычитки. Сверка идёт
    именно по ЛЕММЕ: совпадение по строке само по себе ничего не значит —
    «алфавитом» и «алфавит» стоят эталонами в разных заданиях, но это одно
    слово, и ради него задача и заведена.
    """
    cur.execute(
        """
        SELECT jsonb_array_elements(solution_rules->'short_answer'->'accepted_answers')->>'value' AS value
        FROM tasks
        WHERE is_active = true
          AND jsonb_typeof(solution_rules->'short_answer'->'accepted_answers') = 'array'
        """
    )
    vocab: Set[str] = set()
    for row in cur.fetchall():
        value = (row["value"] or "").strip()
        if WORD_RE.match(value):
            vocab.add(norm(value))
    return vocab


def yo_variants(word: str) -> List[str]:
    """Слово + его вариант через «е» вместо «ё».

    Движок «ё» и «е» не отождествляет, а ученик печатает «е» почти всегда
    («алгоритм чётности» → «четности»). Вариант добавляется, потому что правило
    задачи — не сужать: обе записи должны приниматься.
    """
    out = [word]
    if "ё" in word:
        out.append(word.replace("ё", "е"))
    return out


class Morphology:
    """Обёртка над pymorphy3: разбор эталона и порождение форм одной леммы."""

    def __init__(self) -> None:
        try:
            import pymorphy3
        except ImportError as exc:  # pragma: no cover - среда авторинга
            raise SystemExit(
                "Нужен pymorphy3: .venv\\Scripts\\python.exe -m pip install "
                "pymorphy3 pymorphy3-dicts-ru"
            ) from exc
        self._morph = pymorphy3.MorphAnalyzer()

    def lemmas(self, word: str) -> Set[str]:
        """Все правдоподобные леммы слова (для сверки столкновений)."""
        parses = self._morph.parse(word)
        strong = [p for p in parses if p.score >= AMBIGUITY_SCORE_FLOOR] or parses[:1]
        return {p.normal_form.replace("ё", "е") for p in strong}

    def analyze(self, word: str) -> Tuple[Optional[str], List[str], List[str]]:
        """Разобрать слово.

        :returns: (лемма или None, список форм, список причин отказа)
        """
        parses = self._morph.parse(word)
        if not parses:
            return None, [], ["морфология не разобрала слово"]

        strong = [p for p in parses if p.score >= AMBIGUITY_SCORE_FLOOR] or parses[:1]
        lemmas = {p.normal_form.replace("ё", "е") for p in strong}
        reasons: List[str] = []
        if len(lemmas) > 1:
            reasons.append(
                "неоднозначная лемма: " + ", ".join(sorted(p.normal_form for p in strong))
            )

        best = strong[0]
        pos = best.tag.POS
        if pos not in SAFE_POS:
            reasons.append(f"часть речи {pos or '?'} — формы порождать небезопасно")
        if not best.is_known:
            # Слова нет в словаре — разбор УГАДАН по окончанию, и склонять
            # результат нельзя. Сюда попадают ответы-последовательности ЕГЭ
            # («АДВБГ» → «адвбгами») и несклоняемые заимствования
            # («юзабилити» → «юзабилитью»).
            reasons.append("слово вне словаря — разбор угадан")
        if "Fixd" in best.tag:
            reasons.append("несклоняемое слово")

        if reasons:
            return best.normal_form, [], reasons

        is_supr = "Supr" in best.tag
        forms: List[str] = []
        for item in best.lexeme:
            if item.tag.POS != pos:
                # У прилагательного лексема тянет краткие формы и сравнительную
                # степень («логичен», «логичнее») — это уже не склонение.
                continue
            if ("Supr" in item.tag) != is_supr:
                # Превосходная степень — другое понятие, а не другой падеж:
                # «кратчайший путь» и «краткий путь» — не одно и то же, а
                # лексема «плохой» тянет ещё и супплетивное «худший».
                continue
            forms.extend(yo_variants(item.word))
        return best.normal_form, forms, []


def build_plan(tasks: List[Dict[str, Any]], vocab: Set[str], morph: Morphology) -> Dict[str, Any]:
    """Собрать план добавления форм и список заданий, уходящих на глаза оператору."""
    apply_items: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    stats: Counter = Counter()

    for task in tasks:
        accepted = task["accepted"]
        etalon = accepted[0]["value"].strip()
        score = accepted[0].get("score")
        existing = {norm(a.get("value") or "") for a in accepted}

        lemma, forms, reasons = morph.analyze(etalon)

        # Класс вопроса решает раньше морфологии. «Как это называют?» — падеж
        # ответа не важен. «Что выведет программа?», «какое слово удалили из
        # текста?», «расшифруй сообщение» — ответ обязан быть точной строкой,
        # и «Казани» вместо «Казань» там просто неверно.
        stem = task["stem"] or ""
        if etalon.isupper():
            # Заглавными пишут не слово, а имя: функция Excel «СУММ», ответ-
            # последовательность ЕГЭ. Склонять имя нельзя.
            reasons.append("эталон записан заглавными — это имя/код, а не слово")
        if re.search(r"[«\"']\s*" + re.escape(etalon) + r"\s*[»\"']", stem, re.I):
            # Эталон стоит в условии в кавычках — это имя объекта из задания
            # (таблица «Сотрудники»), и ответ обязан повторить его точно.
            reasons.append("эталон закавычен в условии — это имя объекта из задания")
        if FORM_REQUIRED_RE.search(stem):
            reasons.append("условие само требует определённой формы — решает методист")
        if EXACT_ANSWER_RE.search(stem):
            reasons.append("вопрос требует точной строки (вывод кода/расшифровка/подсчёт), а не понятия")
        elif not NAMING_RE.search(stem):
            reasons.append("вопрос не распознан как «назови понятие» — падеж может быть существен")

        # Столкновение с ЧУЖИМ словом банка: форма совпала с эталоном другого
        # задания, и лемма у того эталона другая. Совпадение с иной формой той же
        # леммы («алфавитом» ↔ «алфавит») столкновением не является.
        lemma_key = (lemma or "").replace("ё", "е")
        collisions = sorted(
            {
                f
                for f in forms
                if norm(f) in vocab
                and norm(f) not in existing
                and lemma_key not in morph.lemmas(norm(f))
            }
        )
        if collisions:
            reasons.append("формы совпали с эталонами банка: " + ", ".join(collisions))

        new_forms: List[str] = []
        seen = set(existing)
        for form in forms:
            key = norm(form)
            if key in seen:
                continue
            seen.add(key)
            new_forms.append(form)

        record = {
            "task_id": task["id"],
            "course_id": task["course_id"],
            "task_type": task["task_type"],
            "title": task["title"],
            "stem": (task["stem"] or "").strip(),
            "etalon": etalon,
            "lemma": lemma,
            "score": score,
            "max_score": task["max_score"],
            "normalization": task["normalization"],
            "nominative_added": bool(lemma and norm(lemma) not in existing),
            "add": new_forms,
            "before": accepted,
        }

        if reasons:
            record["reasons"] = reasons
            skipped.append(record)
            stats["на глаза оператору"] += 1
            continue
        if not new_forms:
            stats["уже полный (нечего добавлять)"] += 1
            continue

        apply_items.append(record)
        stats["в план"] += 1
        if record["nominative_added"]:
            stats["в т.ч. добавлен именительный"] += 1

    return {
        "generated_at": date.today().isoformat(),
        "task": "tsk-796",
        "stats": dict(stats),
        "apply": apply_items,
        "skipped": skipped,
    }


def write_report(plan: Dict[str, Any]) -> None:
    """Отчёт для вычитки человеком: задание → эталон → что добавляем."""
    lines: List[str] = [
        "# tsk-796 — словоформы эталонов: список на вычитку",
        "",
        f"Сгенерирован {plan['generated_at']} скриптом `scripts/gen_wordforms_tsk796.py` "
        "(read-only снимок боевой базы).",
        "",
        "Правила порождения: только формы ТОЙ ЖЕ леммы (pymorphy3), синонимы не "
        "порождаются, существующие эталоны не трогаются, балл добавленной формы "
        "равен баллу исходного эталона.",
        "",
        "## Итог",
        "",
    ]
    for key, value in plan["stats"].items():
        lines.append(f"- {key}: **{value}**")
    total_forms = sum(len(i["add"]) for i in plan["apply"])
    lines += [
        f"- всего добавляемых форм: **{total_forms}**",
        "",
        "## План применения",
        "",
        "| Задание | Курс | Эталон | Вопрос | Добавляем |",
        "|---|---|---|---|---|",
    ]
    for item in plan["apply"]:
        stem = (item["stem"] or "").replace("|", "\\|").replace("\n", " ")
        if len(stem) > 110:
            stem = stem[:110] + "…"
        mark = " **(+им.п.)**" if item["nominative_added"] else ""
        lines.append(
            f"| {item['task_id']} | {item['course_id']} | `{item['etalon']}`{mark} | {stem} | "
            + ", ".join(f"`{f}`" for f in item["add"])
            + " |"
        )
    lines += [
        "",
        "## Не применяем автоматически — на глаза оператору",
        "",
        "| Задание | Эталон | Вопрос | Почему |",
        "|---|---|---|---|",
    ]
    for item in plan["skipped"]:
        stem = (item["stem"] or "").replace("|", "\\|").replace("\n", " ")
        if len(stem) > 110:
            stem = stem[:110] + "…"
        lines.append(
            f"| {item['task_id']} | `{item['etalon']}` | {stem} | "
            + "; ".join(item.get("reasons", []))
            + " |"
        )
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    conn = psycopg2.connect(**prod_dsn())
    conn.set_session(readonly=True, autocommit=True)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        tasks = fetch_tasks(cur)
        vocab = fetch_vocabulary(cur)
    finally:
        conn.close()

    logger.info("заданий-кандидатов: %d, словарь эталонов банка: %d слов", len(tasks), len(vocab))
    plan = build_plan(tasks, vocab, Morphology())
    PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(plan)

    for key, value in plan["stats"].items():
        logger.info("  %s: %d", key, value)
    logger.info("план: %s", PLAN_PATH)
    logger.info("отчёт на вычитку: %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
