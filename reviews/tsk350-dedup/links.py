# -*- coding: utf-8 -*-
"""tsk-350: построение ссылок на задание в LMS и на первоисточник.

Форматы первоисточников — проверенные (reference_ege_answer_sources):
  kompege   → https://kompege.ru/api/v1/task/<id>            (JSON: text + key)
  sdamgia   → https://inf-ege.sdamgia.ru/problem?id=<id>     (HTML, "Ответ: X")
  kpolyakov → https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=<id>
  yandex    → source_url из task_content (education.yandex.ru)
  tg:ege    → https://t.me/cyberguru_ege/<msg>  (msg из source_tg_global_uid)
"""
from __future__ import annotations

import re
from urllib.parse import quote

LMS_BASE = "https://learn.victor-komlev.ru"


def lms_url(row: dict) -> str:
    """Ссылка на задание в LMS (формат проверен живьём на 2205)."""
    uid = row["external_uid"] or ""
    return f"{LMS_BASE}/courses/id-{row['course_id']}/task/{quote(uid, safe='')}"


def _tg_msg(row: dict) -> str | None:
    g = (row["task_content"] or {}).get("source_tg_global_uid") or ""
    m = re.match(r"tg_parser:1701256430:(\d+)", g)
    return m.group(1) if m else None


def source_link(row: dict) -> tuple[str, str]:
    """(метка источника, URL первоисточника | '')."""
    uid = row["external_uid"] or ""
    tc = row["task_content"] or {}

    # 1) прямой source_url (yandex, wp-навигатор)
    su = tc.get("source_url")
    kind = (tc.get("source_kind") or "").lower()

    # определить источник и id из external_uid
    m = re.match(r"ext:(?:d4|calib):(kompege|sdamgia|polyakov):\d+:(.+)", uid)
    if not m:
        m2 = re.match(r"ext:(polyakov|kompege|sdamgia):\w+:\w+:(.+)", uid)
        m = m2 or m
    if m:
        src, sid = m.group(1), m.group(2)
        if src == "kompege":
            return "КомпЕГЭ #" + sid, f"https://kompege.ru/api/v1/task/{sid}"
        if src == "sdamgia":
            return "Решу ЕГЭ #" + sid, f"https://inf-ege.sdamgia.ru/problem?id={sid}"
        if src == "polyakov":
            return "Поляков #" + sid, (
                f"https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId={sid}")

    # tg:ege:N — обёртка ТГ-разбора; сам первоисточник — в source_kind/source_task_id
    if uid.startswith("tg:ege:"):
        msg = _tg_msg(row)
        tg = f"https://t.me/cyberguru_ege/{msg}" if msg else ""
        st = tc.get("source_task_id")
        if kind == "kompege" and st:
            return f"ТГ-пост (КомпЕГЭ #{st})", f"https://kompege.ru/api/v1/task/{st}"
        if kind == "sdamgia" and st:
            return f"ТГ-пост (Решу ЕГЭ #{st})", f"https://inf-ege.sdamgia.ru/problem?id={st}"
        if kind == "polyakov" and st:
            return (f"ТГ-пост (Поляков #{st})",
                    f"https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId={st}")
        return "ТГ-пост @cyberguru_ege", tg

    # wp-навигатор: авторская страница-обёртка, первоисточник в source_url
    if uid.startswith("wp_nav:"):
        if su:
            host = "Яндекс.Учебник" if "yandex" in su else "первоисточник"
            return f"WP-навигатор ({host})", su
        return "WP-навигатор (авторское)", ""

    # Крылов — книжный сборник, веб-ссылки нет; есть ТГ-разбор
    if uid.startswith("crylov:"):
        cm = re.match(r"crylov:v(\d+)t(\d+)", uid)
        label = f"Крылов вариант {cm.group(1)}, задание {cm.group(2)}" if cm else "Крылов"
        msg = _tg_msg(row)
        return label, (f"https://t.me/cyberguru_ege/{msg}" if msg else "")

    # yandex-калибровка
    if "yandex" in uid:
        return "Яндекс.Учебник", su or ""

    # собственное задание LMS
    if uid.startswith("lms:"):
        return "LMS (авторское, без внешнего источника)", ""

    return uid[:40], su or ""
