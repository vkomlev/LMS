# -*- coding: utf-8 -*-
"""tsk-418: read-only отчёт-предложение по requirement_level материалов/заданий ЕГЭ/Python.

НИЧЕГО не пишет в БД. Сопоставляет WP-приоритет (☝️=required/🔽=skippable/
нет значка=recommended), выгруженный ContentBackbone-скриптом
`scripts/wp_operator_export.py` (см. tsk418_wp_export.json), с текущим
requirement_level в LMS (прод, read-only) и строит markdown-отчёт
"было -> стало" со ссылками на объект в SPW — для подтверждения оператором
ПЕРЕД любой правкой (курс правился много раз, доверять авто-сопоставлению
без проверки нельзя, см. плейбук §9 "поштучно, не агрегатом").

Вход: JSON, полученный из xlsx wp_operator_export.py через dump_wp_export_json.py
  (структура {"Курсы": [...], "Материалы": [...], "Задания": [...]}).

Запуск:
  python scripts/tsk418_requirement_level_report.py <путь_к_json> [--out reviews/...md]
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Optional

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITE_BASE = "https://learn.victor-komlev.ru"

# task_n -> WP course_uid (ЕГЭ по информатике, взято из ContentBackbone
# scripts/build_prod_lms_import.py TASK_N_TO_COURSE — переиспользуем канон,
# не пересчитываем заново). Инвертируем ниже в course_uid -> task_n.
TASK_N_TO_COURSE: dict[int, str] = {
    1: "wp:zadanie-1-ege-po-informatike-informatsionnye-modeli",
    2: "wp:zadanie-2-ege-po-informatike-tablitsy-istinnosti",
    3: "wp:ege-po-informatike-zadanie-3-bazy-dannyh-v-excel",
    4: "wp:zadanie-4-ege-po-informatike-neravnomernoe-kodirovanie-i-uslovie-fano",
    5: "wp:zadanie-5-ege-analiz-algoritmov-dlya-ispolnitelej",
    6: "wp:zadanie-6-ege-po-informatike-ispolnitel-cherepaha",
    7: "wp:zadanie-7-ege-kodirovanie-razlichnyh-vidov-informatsii-peredacha-informatsii",
    8: "wp:zadanie-8-ege-po-informatike-kombinatorika",
    9: "wp:zadanie-9-ege-po-informatike-agregatnye-funktsii-i-vychisleniya-v-elektronnyh-tablitsah",
    10: "wp:zadanie-10-ege-po-informatike-poisk-informatsii-v-dokumentah",
    11: "wp:zadanie-11-ege-po-informatike-vychislenie-obema-informatsii",
    12: "wp:zadanie-12-ege-po-informatike-mashina-tyuringa",
    13: "wp:reshenie-zadanij-13-ege-organizatsiya-kompyuternyh-setej-i-adresatsiya",
    14: "wp:zadanie-14-ege-po-informatike-pozitsionnye-sistemy-schisleniya",
    15: "wp:zadanie-15-ege-po-informatike-logicheskie-operatsii",
    16: "wp:zadanie-16-ege-po-informatike-rekursivnye-funktsii",
    17: "wp:zadanie-17-ege-po-informatike-obrabotka-chislovyh-posledovatelnostej",
    18: "wp:zadanie-18-ege-po-informatike-zhadnye-algoritmy",
    19: "wp:zadanie-19-21-ege-po-informatike-teoriya-igr",
    20: "wp:zadanie-19-21-ege-po-informatike-teoriya-igr",
    21: "wp:zadanie-19-21-ege-po-informatike-teoriya-igr",
    22: "wp:zadanie-22-ege-po-informatike-parallelnye-protsessy",
    23: "wp:zadanie-23-ege-po-informatike-rekursivnyj-obhod-dereva-2",
    24: "wp:zadanie-24-ege-po-informatike-obrabotka-teksta",
    25: "wp:zadanie-25-ege-po-informatike-obrabotka-chislovyh-dannyh",
    26: "wp:zadanie-26-ege-po-informatike-obrabotka-dannyh",
    27: "wp:zadanie-27-ege-po-informatike-analiz-dannyh",
}
COURSE_UID_TO_TASK_N: dict[str, int] = {}
for _n, _uid in TASK_N_TO_COURSE.items():
    COURSE_UID_TO_TASK_N.setdefault(_uid, _n)

WP_PRIORITY_TO_LEVEL = {
    "required": "required",
    "recommended": "recommended",
    "optional": "skippable",  # CB Priority использует "optional", LMS enum — "skippable"
}

_WS_RE = re.compile(r"\s+")
_TRAILING_URL_CHARS = ".,;:!?)]}»”'"


def _norm_text(s: str | None) -> str:
    return _WS_RE.sub(" ", (s or "").strip().casefold())


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _normalize_task_url(url: str) -> str:
    """Копия monolith.external_tasks.wp_nav_import.normalize_task_url (без импорта CB-пакета в LMS venv)."""
    cleaned = (url or "").strip().rstrip(_TRAILING_URL_CHARS)
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = parsed.query or ""
    out = f"{scheme}://{netloc}{path}"
    if query:
        out += f"?{query}"
    return out


def _build_wp_nav_external_uid(task_n: int, normalized_url: str) -> str:
    return f"wp_nav:{task_n}:{_sha8(normalized_url)}"


def _slug_from_course_uid_field(value: str | None) -> Optional[str]:
    """course_uid поля в экспорте всегда имеют вид wp:{target_slug}[:anchor...]."""
    if not value or not value.startswith("wp:"):
        return None
    parts = value.split(":")
    return parts[1] if len(parts) > 1 else None


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (PROJECT_ROOT / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def _material_url(course_uid: str, material_id: int) -> str:
    return f"{SITE_BASE}/courses/{course_uid.replace(':', '%3A')}/material/{material_id}"


def _task_url(course_uid: str, external_uid: str) -> str:
    from urllib.parse import quote

    return f"{SITE_BASE}/courses/{quote(course_uid, safe='')}/task/{quote(external_uid, safe='')}"


STEM_MATCH_THRESHOLD = 0.85
TITLE_MATCH_EXACT_ONLY = True  # заголовки короткие — фаззи-совпадение даёт ложные пары чаще, чем помогает


async def load_courses(conn: asyncpg.Connection) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT id, course_uid, title FROM courses WHERE course_uid IS NOT NULL"
    )
    return {r["course_uid"]: {"id": r["id"], "title": r["title"]} for r in rows}


async def load_materials(conn: asyncpg.Connection, course_ids: list[int]) -> dict[int, list[dict]]:
    rows = await conn.fetch(
        "SELECT id, course_id, title, requirement_level, order_position "
        "FROM materials WHERE course_id = ANY($1::int[])",
        course_ids,
    )
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["course_id"], []).append(dict(r))
    return out


async def load_tasks(conn: asyncpg.Connection, course_ids: list[int]) -> dict[int, list[dict]]:
    rows = await conn.fetch(
        "SELECT id, course_id, external_uid, requirement_level, "
        "task_content->>'stem' AS stem "
        "FROM tasks WHERE course_id = ANY($1::int[]) AND is_active"
    , course_ids)
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["course_id"], []).append(dict(r))
    return out


def match_material(wp_row: dict, candidates: list[dict]) -> tuple[Optional[dict], str]:
    title_norm = _norm_text(wp_row.get("title"))
    if not title_norm:
        return None, "у WP-материала пустой заголовок"
    exact = [m for m in candidates if _norm_text(m["title"]) == title_norm]
    if len(exact) == 1:
        return exact[0], "exact-title"
    if len(exact) > 1:
        return None, f"неоднозначно: {len(exact)} материалов с тем же заголовком в курсе"
    # fuzzy fallback, только если единственный явный лидер с большим отрывом
    scored = sorted(
        ((difflib.SequenceMatcher(None, title_norm, _norm_text(m["title"])).ratio(), m) for m in candidates),
        key=lambda x: -x[0],
    )
    if scored and scored[0][0] >= 0.90 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return scored[0][1], f"fuzzy-title({scored[0][0]:.2f})"
    return None, "нет надёжного совпадения по заголовку (возможно, материал переименован/удалён после импорта)"


def match_task_by_stem(wp_stem: str, candidates: list[dict]) -> tuple[Optional[dict], str]:
    stem_norm = _norm_text(wp_stem)[:400]
    if not stem_norm:
        return None, "у WP-задачи пустой stem"
    scored = sorted(
        (
            (difflib.SequenceMatcher(None, stem_norm, _norm_text(c["stem"])[:400]).ratio(), c)
            for c in candidates
            if c.get("stem")
        ),
        key=lambda x: -x[0],
    )
    if not scored:
        return None, "в курсе нет wp:task:* с непустым stem"
    best_score, best = scored[0]
    if best_score >= STEM_MATCH_THRESHOLD and (len(scored) == 1 or best_score - scored[1][0] >= 0.08):
        return best, f"stem-similarity({best_score:.2f})"
    return None, f"нет надёжного совпадения по stem (лучший={best_score:.2f})"


async def load_scope(conn: asyncpg.Connection) -> tuple[dict, dict, dict]:
    """Общий read-only заход для отчёта и бэкфилла: курсы + материалы + задачи в периметре tsk-418."""
    course_by_uid = await load_courses(conn)
    # ограничиваем periметр: только курсы дерева 88 (Python) и 112 (ЕГЭ-инф),
    # HARD-блок 1378..1403 уже recommended (tsk-347) — не трогаем.
    hard_ids = set(range(1379, 1404))
    relevant_course_ids = [
        c["id"] for c in course_by_uid.values() if c["id"] not in hard_ids
    ]
    materials_by_course = await load_materials(conn, relevant_course_ids)
    tasks_by_course = await load_tasks(conn, relevant_course_ids)
    return course_by_uid, materials_by_course, tasks_by_course


def compute_proposals(
    data: dict,
    course_by_uid: dict,
    materials_by_course: dict,
    tasks_by_course: dict,
) -> tuple[list[dict], list[dict]]:
    """Чистая функция сопоставления WP-приоритетов с текущим requirement_level.

    Без побочных эффектов (без DB-записи, без файлов) — используется и отчётом,
    и бэкфиллом, чтобы оба видели ОДИНАКОВЫЙ набор предложений.
    """
    proposals: list[dict] = []
    unresolved: list[dict] = []

    # ---- материалы (текст + видео) ----
    for wp_mat in data.get("Материалы", []):
        slug = _slug_from_course_uid_field(wp_mat.get("course_uid"))
        if not slug:
            unresolved.append({"kind": "material", "reason": "не удалось извлечь slug курса", "wp": wp_mat})
            continue
        course = course_by_uid.get(f"wp:{slug}")
        if not course:
            unresolved.append({"kind": "material", "reason": f"курс с course_uid=wp:{slug} не найден в LMS", "wp": wp_mat})
            continue
        wp_priority = wp_mat.get("_priority") or "recommended"
        proposed_level = WP_PRIORITY_TO_LEVEL.get(wp_priority, "recommended")
        candidates = materials_by_course.get(course["id"], [])
        match, reason = match_material(wp_mat, candidates)
        if not match:
            unresolved.append({
                "kind": "material", "reason": reason, "wp": wp_mat,
                "course_id": course["id"], "course_title": course["title"],
            })
            continue
        if match["requirement_level"] == proposed_level:
            continue  # уже совпадает — предлагать нечего
        proposals.append({
            "kind": "material",
            "course_id": course["id"],
            "course_title": course["title"],
            "id": match["id"],
            "title": match["title"],
            "url": _material_url(f"wp:{slug}", match["id"]),
            "before": match["requirement_level"],
            "after": proposed_level,
            "match_reason": reason,
            "wp_priority_raw": wp_priority,
            "wp_source_url": wp_mat.get("_source_url"),
        })

    # ---- задания ----
    for wp_task in data.get("Задания", []):
        slug = _slug_from_course_uid_field(wp_task.get("course_uid"))
        if not slug:
            unresolved.append({"kind": "task", "reason": "не удалось извлечь slug курса", "wp": wp_task})
            continue
        course = course_by_uid.get(f"wp:{slug}")
        if not course:
            unresolved.append({"kind": "task", "reason": f"курс с course_uid=wp:{slug} не найден в LMS", "wp": wp_task})
            continue
        wp_priority = wp_task.get("_priority") or "recommended"
        proposed_level = WP_PRIORITY_TO_LEVEL.get(wp_priority, "recommended")
        candidates = tasks_by_course.get(course["id"], [])

        task_kind = wp_task.get("task_kind") or ""
        match = None
        reason = ""
        if task_kind == "external_task" and wp_task.get("source_url"):
            task_n = COURSE_UID_TO_TASK_N.get(f"wp:{slug}")
            if task_n is not None:
                computed_uid = _build_wp_nav_external_uid(task_n, _normalize_task_url(wp_task["source_url"]))
                match = next((c for c in candidates if c["external_uid"] == computed_uid), None)
                reason = "wp_nav-hash-exact" if match else f"hash {computed_uid} не найден среди wp_nav:* задач курса"
            else:
                reason = "нет task_n для курса (не ЕГЭ-по-информатике задание) — внешняя задача не сопоставима"
        else:
            wp_only_candidates = [c for c in candidates if (c["external_uid"] or "").startswith("wp:task")]
            match, reason = match_task_by_stem(wp_task.get("stem", ""), wp_only_candidates)

        if not match:
            unresolved.append({
                "kind": "task", "reason": reason, "wp": wp_task,
                "course_id": course["id"], "course_title": course["title"],
            })
            continue
        if match["requirement_level"] == proposed_level:
            continue
        proposals.append({
            "kind": "task",
            "course_id": course["id"],
            "course_title": course["title"],
            "id": match["id"],
            "external_uid": match["external_uid"],
            "title": (wp_task.get("stem") or "")[:90].replace("\n", " "),
            "url": _task_url(f"wp:{slug}", match["external_uid"]),
            "before": match["requirement_level"],
            "after": proposed_level,
            "match_reason": reason,
            "wp_priority_raw": wp_priority,
            "wp_source_url": wp_task.get("source_url") or wp_task.get("_source_anchor"),
        })

    return proposals, unresolved


async def build_report(data: dict, out_path: Path) -> None:
    dsn = _dsn()
    conn = await asyncpg.connect(dsn)
    try:
        course_by_uid, materials_by_course, tasks_by_course = await load_scope(conn)
    finally:
        await conn.close()
    proposals, unresolved = compute_proposals(data, course_by_uid, materials_by_course, tasks_by_course)
    _write_markdown(out_path, proposals, unresolved)


def _write_markdown(out_path: Path, proposals: list[dict], unresolved: list[dict]) -> None:
    lines: list[str] = []
    lines.append("# tsk-418: предложение по requirement_level (WP-иконки -> LMS)\n")
    lines.append(
        f"Найдено предложений на изменение: **{len(proposals)}**. "
        f"Не сопоставлено (не трогаем без ручной проверки): **{len(unresolved)}**.\n"
    )
    lines.append(
        "\n> Ничего не применено. Это read-only отчёт для подтверждения оператором. "
        "Курс правился много раз после импорта — сопоставление ниже сделано по "
        "точному совпадению заголовка/текста; всё, что не совпало однозначно, "
        "вынесено в раздел «Не сопоставлено» и НЕ предлагается менять.\n"
    )

    by_course: dict[int, list[dict]] = {}
    for p in proposals:
        by_course.setdefault(p["course_id"], []).append(p)

    lines.append("\n## Предложения по курсам\n")
    for course_id in sorted(by_course):
        items = by_course[course_id]
        title = items[0]["course_title"]
        lines.append(f"\n### Курс {course_id} — {title} ({len(items)})\n")
        lines.append("| Тип | Объект | Было | Стало | Сопоставление | Ссылка |")
        lines.append("|---|---|---|---|---|---|")
        for p in items:
            obj = p["title"] if p["kind"] == "material" else f"{p['title']} ({p['external_uid']})"
            lines.append(
                f"| {p['kind']} | {obj} | `{p['before']}` | `{p['after']}` | "
                f"{p['match_reason']} (WP: {p['wp_priority_raw']}) | [{p['id'] if p['kind']=='material' else p['external_uid']}]({p['url']}) |"
            )

    lines.append(f"\n## Не сопоставлено ({len(unresolved)}) — требует ручной проверки, менять не будем\n")
    by_course_u: dict[Any, list[dict]] = {}
    for u in unresolved:
        key = u.get("course_id", "без курса")
        by_course_u.setdefault(key, []).append(u)
    for key, items in by_course_u.items():
        title = items[0].get("course_title", "")
        lines.append(f"\n### Курс {key} — {title} ({len(items)})\n")
        for u in items[:50]:
            wp = u["wp"]
            label = wp.get("title") or wp.get("stem") or wp.get("label") or "?"
            lines.append(f"- [{u['kind']}] «{str(label)[:80]}» — {u['reason']}")
        if len(items) > 50:
            lines.append(f"- ... и ещё {len(items) - 50}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Отчёт записан: {out_path}")
    print(f"Предложений: {len(proposals)}, не сопоставлено: {len(unresolved)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    out_path = Path(args.out) if args.out else PROJECT_ROOT / "reviews" / "2026-08-03-tsk418-requirement-level-proposal.md"
    asyncio.run(build_report(data, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
