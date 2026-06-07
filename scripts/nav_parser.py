# -*- coding: utf-8 -*-
"""
nav_parser.py — парсер страниц заданий навигатора ЕГЭ.

Читает страницу курса на victor-komlev.ru (раздел «Задания» с якорями
#prostye / #srednie / #slozhnye), сверяет каждую ссылку с LMS,
обновляет накопительный реестр пропущенных заданий.

Использование:
  python scripts/nav_parser.py \
    --url https://victor-komlev.ru/zadanie-2-ege-po-informatike-tablitsy-istinnosti/ \
    --course-id 148 \
    --task-num 2

  # или через страницу навигатора — скрипт сам найдёт ссылку на контент
  python scripts/nav_parser.py \
    --url https://victor-komlev.ru/navigator-po-zadaniyu-2-ege/ \
    --course-id 148 \
    --task-num 2

Реестр пропущенных: reviews/evidence/nav-missing-tasks.md
"""
import argparse
import io
import os
import re
import sys
from datetime import date

import psycopg2
import requests
from bs4 import BeautifulSoup

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Константы ─────────────────────────────────────────────────────────────────

# Якоря разделов сложности → difficulty_id LMS
# Разные страницы используют разные имена якорей
ANCHOR_DIFFICULTY = {
    "prostye":          2,
    "prostoy-uroven":   2,
    "prostoy_uroven":   2,
    "prostoi-uroven":   2,
    "prostoj-uroven":   2,
    "srednie":          3,
    "sredniy-uroven":   3,
    "sredniy_uroven":   3,
    "srednyaya":        3,
    "slozhnye":         4,
    "slozhnyy-uroven":  4,
    "slozhnyy_uroven":  4,
    "slozhnoj-uroven":  4,
    "slozhnyj-uroven":  4,
}

DIFF_NAMES = {2: "Простые", 3: "Средние", 4: "Сложные"}

# URL-паттерны → (source_key, group_index)
URL_PATTERNS = [
    ("kompege",  re.compile(r"kompege\.ru/task\?id=(\d+)")),
    ("sdamgia",  re.compile(r"sdamgia\.ru/problem\?id=(\d+)")),
    ("polyakov", re.compile(r"polyakov\.spb\.ru.+?topicId=(\d+)")),
    ("yandex",   re.compile(r"education\.yandex\.ru/ege/task/([0-9a-f\-]{36})")),
]

REGISTRY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "reviews", "evidence", "nav-missing-tasks.md")
)

# ── DSN ───────────────────────────────────────────────────────────────────────

def load_dsn() -> str:
    if dsn := os.environ.get("LMS_DB_DSN"):
        return dsn
    env = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    with open(env, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    raise RuntimeError("DATABASE_URL не найден в .env")

# ── Парсинг страницы ──────────────────────────────────────────────────────────

def _parse_url(href: str) -> tuple[str, str] | None:
    """(source, task_id) из URL задания, или None."""
    for source, pattern in URL_PATTERNS:
        if m := pattern.search(href):
            return source, m.group(1)
    return None


def _resolve_content_url(url: str, soup: BeautifulSoup) -> str | None:
    """Если страница навигатора (нет якорей) — вернуть URL контент-страницы."""
    # Проверяем все известные якоря — страница контента, если хоть один нашёлся
    if any(soup.find(id=anchor) for anchor in ANCHOR_DIFFICULTY):
        return None  # уже контент-страница
    # Ищем ссылки вида /zadanie-N-ege-.../#prostye
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(f"#{anchor}" in href for anchor in ANCHOR_DIFFICULTY):
            # Проверяем, что href — не относительная ссылка на ту же страницу (/#xxx)
            base = href.split("#")[0].rstrip("/")
            if base:
                return base + "/"
    return None


def fetch_sections(start_url: str) -> dict[int, list[dict]]:
    """
    Возвращает {difficulty_id: [{url, source, task_id}]}.
    source == 'yandex' отмечает задания, которые в LMS хранятся через wp_nav.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LMS-nav-parser/1.0)",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }

    def get(url: str) -> BeautifulSoup:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return BeautifulSoup(r.text, "html.parser")

    soup = get(start_url)

    # Переход на контент-страницу если нужно
    content_url = _resolve_content_url(start_url, soup)
    if content_url:
        print(f"  Навигатор → контент: {content_url}")
        soup = get(content_url)

    result: dict[int, list[dict]] = {2: [], 3: [], 4: []}

    for anchor_id, diff_id in ANCHOR_DIFFICULTY.items():
        section_el = soup.find(id=anchor_id)
        if not section_el:
            continue
        # Все <li> в ближайшем <ul> после заголовка
        ul = section_el.find_next_sibling("ul")
        if not ul:
            # Возможно ul вложен иначе — ищем до следующего заголовка
            ul = section_el.find_next("ul")
        if not ul:
            continue

        for li in ul.find_all("li", recursive=False):
            # Первая ссылка в li — ссылка на задание
            task_link = li.find("a")
            if not task_link:
                continue
            href = task_link.get("href", "")
            parsed = _parse_url(href)
            if parsed:
                source, task_id = parsed
                result[diff_id].append({"url": href, "source": source, "task_id": task_id})

    return result


# ── Проверка LMS ──────────────────────────────────────────────────────────────

def check_lms(sections: dict[int, list[dict]], course_id: int, cur) -> dict[int, list[dict]]:
    """Добавляет поля status, lms_course, lms_diff к каждому заданию."""
    for diff_id, tasks in sections.items():
        for t in tasks:
            source, task_id = t["source"], t["task_id"]

            # Первичный поиск: по external_uid (kompege/polyakov/sdamgia в не-wp_nav формате)
            if source != "yandex":
                pattern = f".*{re.escape(source)}.*:{re.escape(task_id)}$"
                cur.execute(
                    "SELECT course_id, difficulty_id, is_active FROM tasks "
                    "WHERE external_uid ~ %s AND external_uid NOT ILIKE 'wp_nav:%%' LIMIT 10",
                    (pattern,),
                )
                rows = cur.fetchall()
            else:
                rows = []

            # Вторичный поиск: по task_content->>'source_task_id' (wp_nav-обёртка)
            if not rows:
                cur.execute(
                    "SELECT course_id, difficulty_id, is_active FROM tasks "
                    "WHERE external_uid ILIKE 'wp_nav:%%' "
                    "  AND task_content->>'source_kind' = %s "
                    "  AND task_content->>'source_task_id' = %s LIMIT 10",
                    (source, task_id),
                )
                rows_wp = cur.fetchall()
                if rows_wp:
                    rows = rows_wp
                    t["via_wp_nav"] = True

            in_course = [r for r in rows if r[0] == course_id]
            via_wp = t.get("via_wp_nav", False)

            if not rows:
                t["status"] = "missing"
                t["lms_course"] = None
                t["lms_diff"] = None
            elif in_course:
                lms_diff = in_course[0][1]
                t["lms_diff"] = lms_diff
                t["lms_course"] = course_id
                if via_wp:
                    t["status"] = "wp_ok" if lms_diff == diff_id else "wp_wrong"
                else:
                    t["status"] = "ok" if lms_diff == diff_id else "wrong_diff"
            else:
                t["status"] = "other_course"
                t["lms_course"] = rows[0][0]
                t["lms_diff"] = rows[0][1]

    return sections


# ── Отчёт ─────────────────────────────────────────────────────────────────────

def print_report(sections: dict[int, list[dict]], course_id: int, task_num: int) -> list[tuple]:
    diff_lms = {2: "Легко", 3: "Средняя", 4: "Сложная"}
    status_mark = {
        "ok":           "[OK    ]",
        "wrong_diff":   "[DIFF  ]",
        "missing":      "[MISS  ]",
        "other_course": "[OTHER ]",
        "wp_nav":       "[WPNAV ]",
        "wp_ok":        "[WP_OK ]",
        "wp_wrong":     "[WP_DIF]",
    }

    missing: list[tuple] = []  # (diff_id, task_dict)
    wrong: list[tuple] = []

    for diff_id in (2, 3, 4):
        tasks = sections.get(diff_id, [])
        if not tasks:
            continue
        print(f"\n  {DIFF_NAMES[diff_id]} (навигатор: {len(tasks)} шт.):")
        for t in tasks:
            s = t["status"]
            mark = status_mark.get(s, "[?]")
            src = f"{t['source']}:{t['task_id']}"
            if s == "ok":
                detail = f"diff={diff_lms[diff_id]}"
            elif s == "wp_ok":
                detail = f"в wp_nav, diff={diff_lms[diff_id]}"
            elif s == "wrong_diff":
                detail = f"в LMS diff={t['lms_diff']} (нужно {diff_id}={diff_lms[diff_id]})"
                wrong.append((diff_id, t))
            elif s == "wp_wrong":
                detail = f"в wp_nav diff={t['lms_diff']} (нужно {diff_id}={diff_lms[diff_id]})"
                wrong.append((diff_id, t))
            elif s == "missing":
                detail = "НЕТ В LMS"
                missing.append((diff_id, t))
            elif s == "other_course":
                detail = f"в курсе {t['lms_course']}, diff={t['lms_diff']}"
            else:  # wp_nav (yandex без прямого uid)
                detail = "в wp_nav (yandex)"
            print(f"    {mark} {src:<40} {detail}")

    print(f"\n  Итого пропущено: {len(missing)}")
    if wrong:
        print(f"  Итого с неверной сложностью: {len(wrong)}")
    return missing, wrong


# ── Реестр ────────────────────────────────────────────────────────────────────

def _load_registry_keys() -> set[str]:
    """Ключи уже записанных строк: 'task_num|source|task_id'."""
    if not os.path.exists(REGISTRY_PATH):
        return set()
    keys = set()
    with open(REGISTRY_PATH, encoding="utf-8") as fh:
        for line in fh:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7 and parts[1].strip().isdigit():
                keys.add(f"{parts[1].strip()}|{parts[5].strip()}|{parts[6].strip()}")
    return keys


def update_registry(missing: list, course_id: int, task_num: int) -> None:
    if not missing:
        return

    today = date.today().isoformat()
    existing = _load_registry_keys()

    new_lines = []
    for diff_id, t in missing:
        key = f"{task_num}|{t['source']}|{t['task_id']}"
        if key in existing:
            continue
        new_lines.append(
            f"| {task_num} | {course_id} | {DIFF_NAMES[diff_id]} | "
            f"{diff_id} | {t['source']} | {t['task_id']} | {t['url']} | {today} |\n"
        )

    if not new_lines:
        print("\n  Реестр: новых записей нет (все уже зафиксированы).")
        return

    header_needed = not os.path.exists(REGISTRY_PATH)
    with open(REGISTRY_PATH, "a", encoding="utf-8") as fh:
        if header_needed:
            fh.write("# Реестр заданий навигатора, отсутствующих в LMS\n\n")
            fh.write(
                "| Задание | course_id | Раздел | diff_id | Источник | task_id | URL | Добавлено |\n"
            )
            fh.write(
                "|---------|-----------|--------|---------|----------|---------|-----|----------|\n"
            )
        for line in new_lines:
            fh.write(line)

    print(f"\n  Реестр обновлён: {REGISTRY_PATH} (+{len(new_lines)} строк)")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Парсер навигатора ЕГЭ")
    ap.add_argument("--url",       required=True, help="URL страницы задания или навигатора")
    ap.add_argument("--course-id", required=True, type=int, help="LMS course_id")
    ap.add_argument("--task-num",  required=True, type=int, help="Номер задания ЕГЭ (1-27)")
    args = ap.parse_args()

    print(f"Задание {args.task_num} (course_id={args.course_id})")
    print(f"URL: {args.url}")
    print("─" * 60)

    print("Загружаю страницу...")
    sections = fetch_sections(args.url)
    total_nav = sum(len(v) for v in sections.values())
    for d in (2, 3, 4):
        print(f"  {DIFF_NAMES[d]}: {len(sections[d])} заданий в навигаторе")
    print(f"  Итого в навигаторе: {total_nav}")

    conn = psycopg2.connect(load_dsn())
    cur = conn.cursor()
    try:
        check_lms(sections, args.course_id, cur)
    finally:
        cur.close()
        conn.close()

    print("\nСверка с LMS:")
    missing, wrong = print_report(sections, args.course_id, args.task_num)
    update_registry(missing, args.course_id, args.task_num)

    if wrong:
        print("\nЗадания с неверной сложностью (нужна правка):")
        for diff_id, t in wrong:
            print(f"  {t['source']}:{t['task_id']} — нужно diff={diff_id}, "
                  f"в LMS diff={t['lms_diff']}")


if __name__ == "__main__":
    main()
