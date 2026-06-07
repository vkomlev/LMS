# -*- coding: utf-8 -*-
"""
nav_parser.py — парсер страниц заданий навигатора ЕГЭ.

Читает страницу курса на victor-komlev.ru (раздел «Задания» с якорями
#prostye / #srednie / #slozhnye), сверяет каждую ссылку с LMS.
Также парсит материалы из разделов 📖/👀/❓ и сверяет requirement_level
по иконкам (☝️ = required, 🔽 = skippable, нет = recommended).

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
from urllib.parse import urlparse

import psycopg2
import requests
from bs4 import BeautifulSoup

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Константы ─────────────────────────────────────────────────────────────────

# Якоря разделов сложности → difficulty_id LMS
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
    ("yandex",   re.compile(r"education\.yandex\.ru/ege/(?:inf/)?task/([0-9a-f\-]{36})")),
    ("yandex",   re.compile(r"education\.yandex\.ru/ege/(?:collections|variants)/([0-9a-f\-]{36})/task/(\d+)")),
]

REGISTRY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "reviews", "evidence", "nav-missing-tasks.md")
)

# Разделы материалов навигатора (эмодзи-префикс заголовка)
MATERIAL_HEADING_EMOJIS = frozenset({"📖", "👀", "❓"})

# Иконки обязательности в <li> → requirement_level
REQ_FROM_ICON = {"☝️": "required", "🔽": "skippable"}
REQ_DEFAULT = "recommended"

# HTTP-заголовки
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LMS-nav-parser/1.0)",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

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

# ── HTTP / утилиты ────────────────────────────────────────────────────────────

def _http_get(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def _slug_from_url(url: str) -> str:
    """Извлечь slug из victor-komlev.ru URL: /path/slug/ → slug."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if "/" in path else path


def _norm_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", title or "").strip().casefold()

# ── Парсинг задач ─────────────────────────────────────────────────────────────

def _parse_url(href: str) -> tuple[str, str] | None:
    """(source, task_id) из URL задания, или None."""
    for source, pattern in URL_PATTERNS:
        if m := pattern.search(href):
            task_id = ":".join(group for group in m.groups() if group is not None)
            return source, task_id
    return None


def _section_task_links(section_el) -> list[str]:
    """Ссылки раздела до следующего заголовка; поддерживает ul и отдельные p."""
    links: list[str] = []
    current = section_el
    while current:
        current = current.find_next_sibling()
        if current is None:
            break
        if current.name in ("h1", "h2", "h3", "h4"):
            break
        for a in current.find_all("a", href=True):
            href = a.get("href", "")
            if _parse_url(href):
                links.append(href)
    return links


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


def fetch_sections(start_url: str) -> tuple[dict[int, list[dict]], BeautifulSoup]:
    """
    Возвращает ({difficulty_id: [{url, source, task_id}]}, nav_soup).
    nav_soup — исходная страница (навигатор), используется для парсинга материалов.
    source == 'yandex' отмечает задания, которые в LMS хранятся через wp_nav.
    """
    nav_soup = _http_get(start_url)

    # Переход на контент-страницу если нужно
    content_url = _resolve_content_url(start_url, nav_soup)
    if content_url:
        print(f"  Навигатор → контент: {content_url}")
        content_soup = _http_get(content_url)
    else:
        content_soup = nav_soup

    result: dict[int, list[dict]] = {2: [], 3: [], 4: []}

    for anchor_id, diff_id in ANCHOR_DIFFICULTY.items():
        section_el = content_soup.find(id=anchor_id)
        if not section_el:
            continue
        for href in _section_task_links(section_el):
            parsed = _parse_url(href)
            if parsed:
                source, task_id = parsed
                result[diff_id].append({"url": href, "source": source, "task_id": task_id})

    return result, nav_soup

# ── Парсинг материалов ────────────────────────────────────────────────────────

def fetch_materials(nav_soup: BeautifulSoup, nav_url: str) -> list[dict]:
    """
    Парсит материалы из разделов 📖/👀/❓ страницы навигатора.

    Возвращает [{external_uid, page_slug, position, req_level, title, href}].

    Матчинг с LMS: external_uid = 'wp:mat:komlev:{page_slug}:{position}'.
    - Текстовые/вопросы (victor-komlev.ru href): page_slug из href
    - Видео (внешние ссылки VK/YouTube): page_slug = slug навигатора
    """
    result: list[dict] = []
    nav_slug = _slug_from_url(nav_url)

    entry = (
        nav_soup.find("div", class_="entry-content")
        or nav_soup.find("article")
        or nav_soup.find("main")
    )
    if not entry:
        return result

    # Счётчик позиций отдельный для каждого page_slug
    pos_per_slug: dict[str, int] = {}

    for ul in entry.find_all("ul"):
        # Ищем ближайший заголовок перед ul (братья-узлы, идём назад)
        heading_text = ""
        prev = ul.find_previous_sibling()
        while prev:
            if prev.name in ("h1", "h2", "h3", "h4", "p", "strong"):
                heading_text = prev.get_text(strip=True)
                break
            prev = prev.find_previous_sibling()

        # Обрабатываем только разделы материалов по эмодзи-заголовку
        if not any(e in heading_text[:5] for e in MATERIAL_HEADING_EMOJIS):
            continue

        list_items = []
        for li in ul.find_all("li", recursive=False):
            nested_ul = li.find("ul")
            if nested_ul:
                list_items.extend(nested_ul.find_all("li", recursive=False))
            else:
                list_items.append(li)

        for li in list_items:
            link = li.find("a")
            if not link:
                continue

            href = link.get("href", "")
            title = link.get_text(strip=True)

            # Определяем page_slug для внешнего uid
            if "victor-komlev.ru" in href:
                # Текстовый материал: slug страницы (отрезаем anchor #...)
                mat_slug = _slug_from_url(href.split("#")[0])
            else:
                # Внешняя ссылка (VK, YouTube) — видео на странице навигатора
                mat_slug = nav_slug

            # Позиция в рамках данного page_slug
            pos = pos_per_slug.get(mat_slug, 0)
            pos_per_slug[mat_slug] = pos + 1

            # Иконка обязательности: смотрим текстовые узлы ДО тега <a>
            pre_text = ""
            for node in li.children:
                if hasattr(node, "name") and node.name == "a":
                    break
                pre_text += str(node)

            req = REQ_DEFAULT
            for icon, level in REQ_FROM_ICON.items():
                if icon in pre_text:
                    req = level
                    break

            external_uid = f"wp:mat:komlev:{mat_slug}:{pos}"
            result.append(
                {
                    "external_uid": external_uid,
                    "page_slug": mat_slug,
                    "position": pos,
                    "req_level": req,
                    "title": title,
                    "href": href,
                }
            )

    return result


def material_heading_candidates(nav_soup: BeautifulSoup) -> list[str]:
    """Заголовки рядом с материалами для диагностики, если парсер ничего не нашёл."""
    entry = (
        nav_soup.find("div", class_="entry-content")
        or nav_soup.find("article")
        or nav_soup.find("main")
    )
    if not entry:
        return []

    headings: list[str] = []
    for h in entry.find_all(("h2", "h3", "h4")):
        text = h.get_text(" ", strip=True)
        if text and any(ch in text[:4] for ch in ("📖", "👀", "❓", "🔁", "💻")):
            headings.append(text)
    return headings

# ── Проверка LMS — материалы ──────────────────────────────────────────────────

def check_lms_materials(materials: list[dict], course_id: int, cur) -> list[dict]:
    """Добавляет lms_id, lms_req, lms_active, status к каждому материалу."""
    for mat in materials:
        cur.execute(
            "SELECT id, title, requirement_level, is_active "
            "FROM materials WHERE external_uid = %s AND course_id = %s",
            (mat["external_uid"], course_id),
        )
        row = cur.fetchone()
        if row and _norm_title(row[1]) != _norm_title(mat["title"]):
            cur.execute(
                "SELECT id, title, requirement_level, is_active "
                "FROM materials WHERE course_id = %s AND lower(title) = lower(%s) "
                "ORDER BY is_active DESC, id LIMIT 1",
                (course_id, mat["title"]),
            )
            title_row = cur.fetchone()
            if title_row:
                row = title_row
                mat["matched_by"] = "title"
        if row:
            mat["lms_id"] = row[0]
            mat["lms_title"] = row[1]
            mat["lms_req"] = row[2]
            mat["lms_active"] = row[3]
            if not row[3]:
                mat["status"] = "inactive"
            elif row[2] == mat["req_level"]:
                mat["status"] = "ok"
            else:
                mat["status"] = "diff"
        else:
            mat["lms_id"] = None
            mat["lms_title"] = None
            mat["lms_req"] = None
            mat["lms_active"] = None
            mat["status"] = "missing"
    return materials


def print_material_report(materials: list[dict]) -> list[dict]:
    """
    Печатает таблицу материалов (nav vs LMS).
    Возвращает список материалов со статусом 'diff' для последующей правки.
    """
    if not materials:
        print("  (материалов в навигаторе не найдено)")
        return []

    status_mark = {
        "ok":       "[OK    ]",
        "diff":     "[DIFF  ]",
        "missing":  "[MISS  ]",
        "inactive": "[INACT ]",
    }
    diff_list: list[dict] = []

    for mat in materials:
        s = mat["status"]
        mark = status_mark.get(s, "[?]    ")
        uid = mat["external_uid"]
        title = mat["title"][:45]
        nav_req = mat["req_level"]
        lms_req = mat.get("lms_req") or "—"
        lms_id  = mat.get("lms_id")  or "—"
        matched = f" via={mat['matched_by']}" if mat.get("matched_by") else ""
        print(f"  {mark} id={str(lms_id):<5} {uid:<65} nav={nav_req:<12} lms={lms_req}{matched}")
        if s == "diff":
            diff_list.append(mat)

    ok    = sum(1 for m in materials if m["status"] == "ok")
    diff  = sum(1 for m in materials if m["status"] == "diff")
    miss  = sum(1 for m in materials if m["status"] == "missing")
    inact = sum(1 for m in materials if m["status"] == "inactive")
    print(f"\n  Материалы: всего={len(materials)}  OK={ok}  DIFF={diff}  MISS={miss}  INACT={inact}")
    return diff_list

# ── Проверка LMS — задачи ──────────────────────────────────────────────────────

def check_lms(sections: dict[int, list[dict]], course_id: int, cur) -> dict[int, list[dict]]:
    """Добавляет поля status, lms_course, lms_diff к каждому заданию."""
    nav_diffs_by_task: dict[tuple[str, str], set[int]] = {}
    for diff_id, tasks in sections.items():
        for t in tasks:
            key = (t["source"], t["task_id"])
            nav_diffs_by_task.setdefault(key, set()).add(diff_id)

    for diff_id, tasks in sections.items():
        for t in tasks:
            source, task_id = t["source"], t["task_id"]
            nav_diffs = nav_diffs_by_task[(source, task_id)]
            if len(nav_diffs) > 1:
                t["duplicate_nav_diffs"] = sorted(nav_diffs)

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
                diff_ok = lms_diff == diff_id or lms_diff in nav_diffs
                if via_wp:
                    t["status"] = "wp_ok" if diff_ok else "wp_wrong"
                else:
                    t["status"] = "ok" if diff_ok else "wrong_diff"
            else:
                t["status"] = "other_course"
                t["lms_course"] = rows[0][0]
                t["lms_diff"] = rows[0][1]

    return sections

# ── Отчёт — задачи ────────────────────────────────────────────────────────────

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
            duplicate_diffs = t.get("duplicate_nav_diffs")
            if s == "ok":
                if duplicate_diffs:
                    variants = "/".join(str(d) for d in duplicate_diffs)
                    detail = f"дубль в разделах {variants}, в LMS diff={t['lms_diff']}"
                else:
                    detail = f"diff={diff_lms[diff_id]}"
            elif s == "wp_ok":
                if duplicate_diffs:
                    variants = "/".join(str(d) for d in duplicate_diffs)
                    detail = f"в wp_nav, дубль в разделах {variants}, в LMS diff={t['lms_diff']}"
                else:
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
    sections, nav_soup = fetch_sections(args.url)
    total_nav = sum(len(v) for v in sections.values())
    for d in (2, 3, 4):
        print(f"  {DIFF_NAMES[d]}: {len(sections[d])} заданий в навигаторе")
    print(f"  Итого заданий в навигаторе: {total_nav}")

    print("\nПарсинг материалов...")
    materials = fetch_materials(nav_soup, args.url)
    print(f"  Найдено материалов в навигаторе: {len(materials)}")
    if not materials:
        headings = material_heading_candidates(nav_soup)
        if headings:
            print("  Диагностика: найденные заголовки-кандидаты материалов:")
            for heading in headings:
                print(f"    - {heading}")

    conn = psycopg2.connect(load_dsn())
    cur = conn.cursor()
    try:
        check_lms(sections, args.course_id, cur)
        check_lms_materials(materials, args.course_id, cur)
    finally:
        cur.close()
        conn.close()

    print("\nСверка заданий с LMS:")
    missing, wrong = print_report(sections, args.course_id, args.task_num)
    update_registry(missing, args.course_id, args.task_num)

    print("\nСверка материалов с LMS:")
    diff_mats = print_material_report(materials)

    if wrong:
        print("\nЗадания с неверной сложностью (нужна правка):")
        for diff_id, t in wrong:
            print(f"  {t['source']}:{t['task_id']} — нужно diff={diff_id}, "
                  f"в LMS diff={t['lms_diff']}")

    if diff_mats:
        print("\nМатериалы с расхождением req_level (нужна правка):")
        for mat in diff_mats:
            print(f"  id={mat['lms_id']}  {mat['external_uid']}")
            print(f"    nav={mat['req_level']}  lms={mat['lms_req']}")
            print(f"    «{mat['title']}»")


if __name__ == "__main__":
    main()
