# -*- coding: utf-8 -*-
"""tsk-808: построить план «видеоразбор из ContentBackbone -> задание LMS».

READ-ONLY. Запускается ЛОКАЛЬНО: читает прод-БД ContentBackbone и прод-БД LMS
(обе из `.mcp.json`), пишет только файл `reviews/tsk808-video-hint-plan.json`.
Запись подсказок делает отдельный скрипт `tsk808_apply_video_hints.py`.

ПОЧЕМУ НЕ ХВАТИЛО tsk-324
Тот проход брал заголовок ролика («… задание №N (N_ID) (Источник)») и вешал
подсказку на `external_uid LIKE 'ext:d4:{kompege|sdamgia|polyakov}:%'`. Обе
половины ключа узкие: заголовок без метки источника не разбирался вовсе, а
префикс `ext:d4:` не покрывает ни `ext:polyakov:pilot:mini50:*`, ни
`wp_nav:*` с провенансом в `task_content`, ни ОГЭ-шаблоны. Отсюда и вопрос
оператора: разбор задания 4406 лежал в ВК с 13.02.2026, а у задания 2058 в
LMS `hints_video` был пуст.

ЦЕПОЧКА (её и просил оператор — не угадывание по числовому хвосту uid)
    content_hub.source_item (source_system='vk_importer')  — ролик
      -> content_hub.publication (destination='vk')        — ссылка и статус
      -> ТГ-пост: content_hub.asset.content_hash == raw->>'file_hash' ролика
      -> первоисточник: ссылка в теле поста (kpolyakov / kompege / sdamgia /
         education.yandex)
      -> задание LMS по (источник, id).

КЛЮЧИ СОПОСТАВЛЕНИЯ, в порядке применения
  K1  «источник + id». У ролика — из ссылки в теле связанного ТГ-поста, иначе
      из кода `(N_M)` в заголовке/описании при названном источнике. У задания —
      из `task_content.source_kind` + `source_task_id` либо из `external_uid`
      известных шаблонов (см. UID_RX).
  K1b «код без источника». Заголовок несёт код, но источник не назван нигде.
      Разрешаем, ТОЛЬКО если этот числовой id встречается ровно у одного
      источника среди заданий LMS И номер задания ЕГЭ из заголовка ролика
      совпал с номером в названии курса. Без второй проверки внутренняя
      нумерация роликов («Вспомогательное задание 25_6») ложится на чужой id
      источника — замер: 4 таких пары из 20.
  K2  «номер ТГ-поста»: `task_content.source_tg_global_uid` -> пост -> его ролик.

ЧТО СОЗНАТЕЛЬНО НЕ ИСПОЛЬЗУЕТСЯ: сопоставление по ТЕКСТУ условия. Замерено
(Жаккар по редким словам, порог 0.55): даёт 181 «находку», но 29 роликов
претендуют на 153 задания сразу — у заданий одного типа условие отличается
числами, а не словами. Один ролик «задание №6 (6_52845)» получал 19 разных
заданий с похожестью до 0.94. Разбор чужого варианта хуже отсутствия
подсказки, поэтому текстовый ключ отклонён целиком, а не поднят порогом.

ПРОВЕРКА ССЫЛОК. Код ответа страницы ВК не показатель: удалённое видео тоже
отдаёт 200. Проверяется содержимое встраиваемого плеера `video_ext.php` —
того самого, что показывает ученику SPW (components/media/VideoEmbed.tsx), и
АНОНИМНО, без сессии: закрытое видео без метки доступа `hash` у ученика не
откроется (урок tsk-806). Не открывшиеся ссылки в план не попадают.

Запуск: python scripts/tsk808_build_video_hint_plan.py [--skip-link-check]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
PLAN_PATH = project_root / "reviews" / "tsk808-video-hint-plan.json"

CODE_RE = re.compile(r"\((\d+)_(\d+)\)")
LABEL_RE = re.compile(r"\)\s*\(([^)]+)\)\s*$")
COURSE_NUM = re.compile(r"[Зз]адание\s*№?\s*(\d+)")
LABEL_MAP = {
    "поляков": "polyakov", "кегэ": "kompege", "компегэ": "kompege", "комп егэ": "kompege",
    "решу егэ": "sdamgia", "решуегэ": "sdamgia", "решу оге": "sdamgia_oge",
    "решуоге": "sdamgia_oge", "яндекс учебник": "yandex", "крылов": "krylov",
}
HREF_RX = [
    ("polyakov", re.compile(r"kpolyakov\.spb\.ru[^\s'\"]*topicId=(\d+)")),
    ("kompege", re.compile(r"kompege\.ru/task\?id=(\d+)")),
    ("sdamgia", re.compile(r"inf-ege\.sdamgia\.ru/problem\?id=(\d+)")),
    ("sdamgia_oge", re.compile(r"inf-oge\.sdamgia\.ru/problem\?id=(\d+)")),
]
# Яндекс Учебник — отдельно: в подборке `/collections/<uuid>/task/<N>` один UUID
# несёт десятки разных заданий. Ключ по одному UUID вешал на задание 3476 пять
# чужих роликов (№25, №3, №9, №26, №11) — поэтому номер в подборке в ключе.
YA_COLLECTION = re.compile(r"education\.yandex\.ru/[^\s'\"]*?collections/([0-9a-f-]{36})/task/(\d+)")
YA_SINGLE = re.compile(
    r"education\.yandex\.ru/[^\s'\"]*?task/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

# Шаблоны external_uid, из которых достаётся id первоисточника.
# Индексы в OGE_ONLY — те, где источник заведомо РешуОГЭ, а группа 1 = id.
UID_RX = [
    re.compile(r"^ext:d\d+:(kompege|sdamgia|polyakov):\d+:(\d+)$"),
    re.compile(r"^ext:(polyakov):pilot:mini\d+:(\d+)$"),
    re.compile(r"^ext:calib:(polyakov):img:\d+:(\d+)$"),
    re.compile(r"^(sdamgia):(\d+)$"),
    re.compile(r"^oge:reshu:t\d+:(\d+)$"),
    re.compile(r"^sdamgia:oge:\d+:(\d+)$"),
]
OGE_ONLY = {4, 5}

VK_RE = re.compile(r"(?:vk\.com|vk\.ru|vkvideo\.ru)/video(-?\d+)_(\d+)", re.I)
TITLE_RE = re.compile(r'"title"\s*:\s*"([^"]{1,200})"')
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def _dsn(server: str) -> str:
    """Достать прод-DSN нужного MCP-сервера из .mcp.json (секрет не печатаем)."""
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    for arg in cfg[server]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError(f"Не нашёл прод-DSN для {server} в .mcp.json")


def _norm_label(raw: str | None) -> str | None:
    return LABEL_MAP.get(raw.strip().lower().replace("ё", "е")) if raw else None


def _hrefs_of(body: str | None) -> set[tuple[str, str]]:
    """Пары (источник, id) из ссылок в теле ТГ-поста."""
    out: set[tuple[str, str]] = set()
    body = body or ""
    for src, rx in HREF_RX:
        for m in rx.finditer(body):
            out.add((src, m.group(1)))
    for m in YA_COLLECTION.finditer(body):
        out.add(("yandex", f"{m.group(1)}:{m.group(2)}"))
    in_collection = {k[1].split(":")[0] for k in out if k[0] == "yandex"}
    for m in YA_SINGLE.finditer(body):
        if m.group(1) not in in_collection:
            out.add(("yandex", m.group(1)))
    return out


def _task_key(uid: str | None, kind: str | None, tid: str | None) -> tuple[str | None, str | None, str | None]:
    """(источник, id, способ) для задания LMS."""
    if kind and tid:
        # у Яндекса id составной («<uuid>:<номер в подборке>»), дробить нельзя
        return kind, str(tid) if kind == "yandex" else str(tid).split(":")[0], "provenance"
    for idx, rx in enumerate(UID_RX):
        m = rx.match(uid or "")
        if not m:
            continue
        if idx in OGE_ONLY:
            return "sdamgia_oge", m.group(1), "uid"
        return m.group(1), m.group(2), "uid"
    return None, None, None


def probe_link(url: str, attempts: int = 3) -> tuple[str, str]:
    """Открывается ли ролик у ученика. Возвращает (статус, живое название)."""
    m = VK_RE.search(url)
    if not m:
        return "НЕ_ВК", ""
    ext = f"https://vk.com/video_ext.php?oid={m.group(1)}&id={m.group(2)}&hd=2"
    last = "БЕЗ_НАЗВАНИЯ"
    for n in range(attempts):
        try:
            req = urllib.request.Request(ext, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = f"ОШИБКА:{exc}"[:80]
        else:
            if "Видеофайл не найден" in html or "видеозапись удалена" in html.lower():
                return "НЕ_НАЙДЕН", ""
            found = TITLE_RE.search(html)
            if found:
                return "ОК", found.group(1)
            last = "БЕЗ_НАЗВАНИЯ"
        if n + 1 < attempts:
            time.sleep(1.5)
    return last, ""


async def collect() -> tuple[list, Counter]:
    cb = await asyncpg.connect(_dsn("content_backbone_prod_db"))
    lms = await asyncpg.connect(_dsn("learn_prod_db"))
    try:
        await cb.execute("SET default_transaction_read_only = on")
        await lms.execute("SET default_transaction_read_only = on")
        videos = await cb.fetch(
            "SELECT si.id, si.global_uid, si.raw->>'title' AS title, si.raw->>'description' AS descr, "
            "si.raw->>'video_url' AS url, si.raw->>'file_hash' AS fhash, si.raw->>'date' AS rec_date, "
            "p.id AS pub_id, p.status AS pub_status, p.remote_url AS pub_url, p.published_at "
            "FROM content_hub.source_item si "
            "LEFT JOIN content_hub.publication p ON p.global_uid = si.global_uid AND p.destination = 'vk' "
            "WHERE si.source_system = 'vk_importer' AND si.raw->>'video_url' IS NOT NULL"
        )
        tg_assets = await cb.fetch(
            "SELECT a.content_hash, si.external_id AS post, si.global_uid, si.body "
            "FROM content_hub.asset a JOIN content_hub.source_item si ON si.id = a.source_item_id "
            "WHERE si.source_system = 'telegram_desktop' AND a.content_hash IS NOT NULL"
        )
        tasks = await lms.fetch(
            "SELECT id, course_id, external_uid, task_content->>'title' AS title, "
            "task_content->>'source_kind' AS kind, task_content->>'source_task_id' AS tid, "
            "task_content->>'source_tg_global_uid' AS tg_uid, "
            "jsonb_array_length(COALESCE(task_content->'hints_video', '[]'::jsonb)) AS nhv "
            "FROM tasks WHERE is_active"
        )
        courses = {r["id"]: r["title"] for r in await lms.fetch("SELECT id, title FROM courses")}
    finally:
        await cb.close()
        await lms.close()

    posts_by_hash: dict[str, list] = defaultdict(list)
    for a in tg_assets:
        posts_by_hash[a["content_hash"]].append(a)

    stat: Counter = Counter()
    by_srcid: dict[tuple[str, str], list] = defaultdict(list)
    by_tguid: dict[str, list] = defaultdict(list)
    unkeyed_coded: list[dict] = []
    for v in videos:
        rec = dict(v)
        rec["published"] = v["pub_status"] == "published" and bool(v["pub_url"])
        stat["ролики_всего"] += 1
        stat["публикация_" + str(v["pub_status"])] += 1
        posts = posts_by_hash.get(v["fhash"] or "", [])
        rec["tg_posts"] = [p["post"] for p in posts]
        href_keys: set[tuple[str, str]] = set()
        for p in posts:
            href_keys |= _hrefs_of(p["body"])
            by_tguid[p["global_uid"]].append(rec)
        title = v["title"] or ""
        code_m = CODE_RE.search(title)
        label_m = LABEL_RE.search(title)
        label = _norm_label(label_m.group(1) if label_m else None)
        if label is None:
            d_m = LABEL_RE.search(v["descr"] or "")
            label = _norm_label(d_m.group(1) if d_m else None)
        keys: set[tuple[str, str]] = set()
        how = None
        if len(href_keys) == 1:
            keys, how = href_keys, "href"
        elif code_m and label:
            keys, how = {(label, code_m.group(2))}, "title"
        elif code_m and href_keys:
            same = {k for k in href_keys if k[1] == code_m.group(2)}
            if len(same) == 1:
                keys, how = same, "title+href"
        rec["resolve"] = how
        rec["ege_num"] = code_m.group(1) if code_m else None
        rec["code"] = code_m.group(2) if code_m else None
        for k in keys:
            by_srcid[k].append(rec)
        if not keys and code_m:
            unkeyed_coded.append(rec)
        stat["ключ_" + str(how)] += 1

    # K1b — код есть, источник нигде не назван
    by_code: dict[str, list] = defaultdict(list)
    for t in tasks:
        s, i, _h = _task_key(t["external_uid"], t["kind"], t["tid"])
        if s and i and i.isdigit():
            by_code[i].append(t)
    for rec in unkeyed_coded:
        cands = by_code.get(rec["code"] or "", [])
        srcs = {_task_key(t["external_uid"], t["kind"], t["tid"])[0] for t in cands}
        if len(srcs) != 1:
            stat["k1b_источник_не_один" if cands else "k1b_нет_задания_с_id"] += 1
            continue
        nums = set()
        for t in cands:
            cm = COURSE_NUM.search(courses.get(t["course_id"]) or "")
            nums.add(cm.group(1) if cm else None)
        if nums != {rec["ege_num"]}:
            stat["k1b_номер_задания_не_сошёлся"] += 1
            continue
        stat["k1b_принято"] += 1
        rec["resolve"] = "code+unique_id"
        by_srcid[(next(iter(srcs)), rec["code"])].append(rec)

    plan = []
    for t in tasks:
        src, tid, how = _task_key(t["external_uid"], t["kind"], t["tid"])
        cand, key_used = [], None
        if src and tid:
            stat["задание_с_ключом"] += 1
            cand = by_srcid.get((src, tid), [])
            if cand:
                key_used = "K1:" + how
        if not cand and t["tg_uid"]:
            cand = by_tguid.get(t["tg_uid"], [])
            if cand:
                key_used = "K2:tg_uid"
        if not cand:
            continue
        stat["совпало_" + key_used] += 1
        if t["nhv"] > 0:
            stat["уже_с_подсказкой"] += 1
            continue
        good = [c for c in cand if c["published"]]
        if not good:
            stat["ролики_не_опубликованы"] += 1
            continue
        # Правило при нескольких роликах на один ключ: берём ВСЕ опубликованные,
        # дедуп по ссылке, порядок — от свежей записи к старой. Так уже устроены
        # существующие подсказки (155 заданий с двумя ссылками, 52 — с шестью):
        # это разные разборы одной задачи, а не дубли, и выбирать «лучший»
        # за автора у нас нет основания.
        seen, videos_out = set(), []
        for c in sorted(good, key=lambda x: (x["rec_date"] or ""), reverse=True):
            if c["pub_url"] in seen:
                continue
            seen.add(c["pub_url"])
            videos_out.append(c)
        cm = COURSE_NUM.search(courses.get(t["course_id"]) or "")
        warn = []
        vnums = {c["ege_num"] for c in videos_out if c["ege_num"]}
        if cm and vnums and cm.group(1) not in vnums:
            warn.append(f"номер задания в заголовке ролика ({'/'.join(sorted(vnums))}) "
                        f"не совпал с номером курса ({cm.group(1)})")
        if len(videos_out) > 1:
            warn.append("несколько разборов одной задачи")
        stat["В_ПЛАН_" + key_used] += 1
        plan.append({
            "task_id": t["id"], "course_id": t["course_id"], "course": courses.get(t["course_id"]),
            "external_uid": t["external_uid"], "task_title": t["title"],
            "key": key_used, "src": src, "code": tid, "warn": warn,
            "videos": [{
                "source_item_id": c["id"], "title": c["title"], "url": c["pub_url"],
                "publication_id": c["pub_id"], "published_at": str(c["published_at"]),
                "recorded_at": c["rec_date"], "resolve": c["resolve"], "tg_posts": c["tg_posts"],
            } for c in videos_out],
        })
    return plan, stat


def main(skip_link_check: bool) -> None:
    plan, stat = asyncio.run(collect())
    print("Сводка:")
    for k, v in sorted(stat.items()):
        print(f"  {k:<34} {v}")
    print(f"\nЗаданий в плане: {len(plan)}")

    if not skip_link_check:
        urls = sorted({v["url"] for p in plan for v in p["videos"]})
        print(f"Проверяю, что ролики открываются (анонимно, как у ученика): {len(urls)} ссылок")
        checked = {}
        for i, url in enumerate(urls, 1):
            status, live_title = probe_link(url)
            checked[url] = status
            if status != "ОК":
                print(f"  !! {i}/{len(urls)} {url} -> {status}")
            time.sleep(0.3)
        bad = {u for u, s in checked.items() if s != "ОК"}
        print(f"  не открылись: {len(bad)}")
        for p in plan:
            for v in p["videos"]:
                v["link_check"] = checked.get(v["url"], "НЕ_ПРОВЕРЕНО")
            p["videos"] = [v for v in p["videos"] if v["link_check"] == "ОК"]
        dropped = [p for p in plan if not p["videos"]]
        plan = [p for p in plan if p["videos"]]
        if dropped:
            print(f"  заданий выпало целиком (все ролики не открылись): {len(dropped)}: "
                  f"{[p['task_id'] for p in dropped]}")

    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nИтого в плане: {len(plan)} заданий, "
          f"{sum(len(p['videos']) for p in plan)} ссылок")
    print(f"Записано: {PLAN_PATH}")
    flagged = [p for p in plan if p["warn"]]
    if flagged:
        print(f"\nС пометками ({len(flagged)}) — посмотреть глазами перед записью:")
        for p in flagged:
            print(f"  задание {p['task_id']} ({p['course']}): {'; '.join(p['warn'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-link-check", action="store_true",
                    help="не ходить в ВК за проверкой ссылок (быстрее, но план непроверен)")
    main(ap.parse_args().skip_link_check)
