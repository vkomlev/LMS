# -*- coding: utf-8 -*-
"""tsk-948: единые критерии оценивания ФИПИ для 25 заданий ОГЭ «Задание 13».

В очереди критериев (`GET /tasks/grading-criteria/queue`) 25 заданий с
`requires_attachment=true` — все в курсе 1178 «ОГЭ. Задание 13» (sdamgia,
`sdamgia:oge:13:NNNNN`). Условия однотипны: 13.1 — презентация из трёх слайдов
по каталогу, 13.2 — текстовый документ по образцу; ученик выбирает один вариант,
поэтому критерии для 13.1 и 13.2 лежат в одном блоке.

Источник критериев — ФИПИ: методические рекомендации ОГЭ-2026 по информатике
(раздел 2.1, задание 13.1 — редакция с макетами слайдов, как в наших условиях)
и демоверсия/зеркало sdamgia (задание 13.2, строки на 1 и 0 баллов).
Решения оператора 2026-09-15: зачёт = 2 балла по ФИПИ (1 балл — незачёт с
комментарием); .pptx/.docx принимаются наравне с .odp/.odt.

Запись — `PATCH /tasks/{id}` сервисным ключом: читается текущее `solution_rules`,
добавляется блок `grading_criteria` (`status=approved`, `reviewed_by=2`,
`origin=manual`), остальное возвращается как есть. После PATCH провенанс
станет `manual_web` — импорт эти задания больше не перепишет, это ожидаемо.

Без флагов — план: критерии с длинами пунктов и (при заданном ключе) список
целей с текущим состоянием; ничего не пишет.

Запуск (после протокола /db-check — reviews/2026-09-15-tsk948-*.md):
    python scripts/tsk948_oge13_criteria.py
    LMS_API_KEY=... python scripts/tsk948_oge13_criteria.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("tsk948")

API_BASE = os.environ.get("LMS_API_BASE", "https://api.learn.victor-komlev.ru/api/v1")
COURSE_ID = 1178
UID_PREFIX = "sdamgia:oge:13:"
EXPECTED_COUNT = 25

#: Оператор, подтвердивший критерии (victor.komlev@mail.ru).
REVIEWED_BY = 2

#: Границы схемы `GradingCriteria` (app/schemas/solution_rules.py).
ITEM_MIN, ITEM_MAX, ITEMS_MAX, NOTES_MAX = 10, 500, 20, 2000

MUST = [
    "Сдан файл в редактируемом формате: для 13.1 — презентация, для 13.2 — текстовый "
    "документ. Проверяется один выбранный вариант (13.1 ИЛИ 13.2), по нему и ставится оценка",
    "13.1 Структура: ровно три слайда без анимации, слайд 16:9 альбомный; титульный — название "
    "(тема из задания) и подзаголовок с данными автора; слайд 2 — заголовок, два блока текста, "
    "два изображения; слайд 3 — заголовок, три изображения, три блока текста; состав и взаимное "
    "расположение объектов — как на макетах в условии",
    "13.1 Содержание: текст и картинки из каталога к заданию или свои, но по теме и по заголовку "
    "каждого слайда; в сумме раскрыты внешний вид, ареал, образ жизни и рацион животного; "
    "слайды 2 и 3 озаглавлены по теме",
    "13.1 Шрифт: единый тип шрифта во всей презентации; размеры: название на титульном — 40 пт, "
    "подзаголовок титульного и заголовки слайдов — 24 пт, подзаголовки и основной текст на "
    "слайдах 2–3 — 20 пт; текст не перекрывает изображения и не сливается с фоном",
    "13.1 Изображения: размещены по заданию и соответствуют содержанию слайда, пропорции сохранены "
    "(не растянуты), не накладываются друг на друга, не перекрывают текст и заголовки",
    "13.2 Основной текст: воспроизведён по образцу; 14 пт, прямое нормальное начертание; "
    "полужирным, курсивом и подчёркиванием выделены ровно те слова, что в образце; выравнивание "
    "по ширине; отступ первой строки 1 см средствами абзаца; междустрочный интервал от одинарного "
    "до полуторного; строки переносит редактор, без ручных разрывов внутри абзаца",
    "13.2 Таблица: число строк и столбцов как в образце; таблица уже основного текста и стоит по "
    "центру страницы; шапка полужирная; первый столбец по левому краю, остальные столбцы и шапка "
    "по центру; надстрочные знаки из образца (м³, °C) — верхним индексом или спецсимволом; "
    "интервал между текстом и таблицей 12–24 пт без пустого абзаца",
    "13.2 Грамотность: в основном тексте не более пяти ошибок (орфография, пунктуация, пробелы, "
    "пропущенные слова), в таблице — не более трёх",
]

ACCEPT = [
    "Выполнен только один вариант — так и задумано. Сданы оба — оценивается названный в "
    "комментарии, иначе лучший из двух",
    "Файл .pptx/.docx вместо .odp/.odt — принимается наравне: экзаменационное требование формата "
    "в LMS не проверяется, засчитывается любой редактируемый файл презентации или документа",
    "Подзаголовок титульного слайда: вместо номера участника экзамена — фамилия, имя, класс или "
    "любая подпись автора",
    "13.1: текст слайдов скопирован из файла каталога дословно или написан своими словами — "
    "равноценно; выравнивание объектов и ориентация картинок произвольные, если состав и "
    "расположение по макету",
    "13.1: слайдов больше трёх — оцениваются первые три, лишние ошибкой не считаются "
    "(правило ФИПИ)",
    "13.1: тема оформления, фон, вид шрифта (рубленый / с засечками) — любые, если тип единый "
    "и текст читается",
    "13.2: ширина строк и разбиение на строки отличаются от образца из-за полей и размера "
    "страницы; параметры, не названные в условии (гарнитура, поля, цвет), любые",
    "13.2: отступ первой строки задан табуляцией, если её размер равен 1 см",
]

REJECT = [
    "Сдан скриншот, PDF или фото экрана вместо файла презентации/документа — размеры шрифта, "
    "интервалы и структуру проверить нельзя; попросить приложить исходный файл",
    "13.1: слайдов два и меньше, есть анимация или переходы, слайд не 16:9 — по ФИПИ это максимум "
    "1 балл, в LMS незачёт",
    "13.1: на слайде 2 или 3 не хватает объекта (заголовок, блок текста, изображение) или их число "
    "не по макету; текст не по теме заголовка; картинки растянуты или перекрывают текст",
    "13.1: разные типы шрифта или размеры не 40/24/20 — обычно ученик оставил размеры шаблона "
    "(44/32/18); это ошибка шрифта",
    "13.2: отступ первой строки набран пробелами, строки разорваны вручную, интервал перед таблицей "
    "сделан пустым абзацем — видно только в режиме непечатаемых символов",
    "13.2: таблицы нет, она не по центру или шире текста, шапка не полужирная, выравнивание в "
    "столбцах не по образцу, м³/°C набраны как «м3»/«оС»",
    "13.2: выделены не те слова или выделение пропущено; текст по левому краю; шрифт не 14 пт; "
    "больше пяти ошибок в тексте или больше трёх в таблице",
    "Файл пустой, повреждён или по другой теме (другое животное, другой текст) — незачёт",
]

NOTES = (
    "Источник — критерии ФИПИ (методические рекомендации ОГЭ-2026 по информатике, задание 13.1 в "
    "редакции с макетами слайдов; демоверсия — задание 13.2). Шкала ФИПИ 0/1/2: 13.1 — 1 балл при "
    "не более чем одной ошибке суммарно (однотипные считаются за одну) либо при двух безошибочных "
    "слайдах; 13.2 — 1 балл при не более чем трёх нарушениях в каждой части (текст, таблица) либо "
    "когда одна часть выполнена целиком верно, а вторая — нет; 0 — остальное. В LMS задание на "
    "зачёт/незачёт (max_score=1): зачёт = 2 балла по ФИПИ; 1 балл по ФИПИ — незачёт с "
    "комментарием, что именно снижено, чтобы ученик переделал. Как проверять: 13.1 — открыть файл, "
    "посчитать слайды, посмотреть размер шрифта на каждом объекте и состав слайдов 2–3 по макету; "
    "13.2 — включить непечатаемые символы (¶): пробелы вместо отступа, ручные разрывы строк и "
    "пустой абзац перед таблицей видны только так. Ученик обычно сдаёт .pptx/.docx — формат не "
    "снижает. Критерии одинаковы для всех 25 вариантов курса 1178 (меняется только животное в 13.1 "
    "и текст-образец в 13.2). Согласованы с оператором в чате 2026-09-15 (tsk-948)."
)


def build_criteria() -> dict[str, Any]:
    """Блок `grading_criteria` в подтверждённом виде (образец — задания 4820/5832)."""
    return {
        "must": MUST,
        "accept": ACCEPT,
        "reject": REJECT,
        "notes": NOTES,
        "status": "approved",
        "origin": "manual",
        "reviewed_by": REVIEWED_BY,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "generated_by_model": None,
        "generated_at": None,
        "draft_warning": None,
    }


def check_lengths() -> None:
    """Проверить границы схемы `GradingCriteria` до обращения к API.

    :raises ValueError: пункт вне 10–500 символов, больше 20 пунктов, дубль,
        `notes` длиннее 2000.
    """
    for name, items in (("must", MUST), ("accept", ACCEPT), ("reject", REJECT)):
        if len(items) > ITEMS_MAX:
            raise ValueError(f"{name}: больше {ITEMS_MAX} пунктов ({len(items)})")
        for item in items:
            if not ITEM_MIN <= len(item) <= ITEM_MAX:
                raise ValueError(f"{name}: пункт длиной {len(item)} вне {ITEM_MIN}–{ITEM_MAX}: {item[:60]}…")
        if len(set(items)) != len(items):
            raise ValueError(f"{name}: есть дубли")
    if len(NOTES) > NOTES_MAX:
        raise ValueError(f"notes длиннее {NOTES_MAX} ({len(NOTES)})")


def _api(method: str, path: str, body: Any | None = None) -> Any:
    """Вызов боевого API сервисным ключом из `LMS_API_KEY` (в логи не пишется).

    :raises RuntimeError: ключ не задан или ответ не 2xx (с телом ошибки).
    """
    key = os.environ.get("LMS_API_KEY")
    if not key:
        raise RuntimeError("LMS_API_KEY не задан")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method=method,
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} → {e.code}: {detail[:2000]}") from e


def list_targets() -> list[dict[str, Any]]:
    """Задания курса 1178 из sdamgia — по `external_uid`, а не по списку id руками."""
    query = urllib.parse.urlencode({"limit": 200})
    items = _api("GET", f"/tasks/by-course/{COURSE_ID}?{query}")
    targets = [t for t in items if str(t.get("external_uid") or "").startswith(UID_PREFIX)]
    targets.sort(key=lambda t: t["id"])
    return targets


def build_payload(current: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    """PATCH-тело: текущее `solution_rules` целиком + блок критериев."""
    rules = dict(current["solution_rules"])
    rules["grading_criteria"] = criteria
    return {"solution_rules": rules}


def plan(with_api: bool) -> list[dict[str, Any]]:
    """Показать критерии и (при ключе) цели с текущим состоянием; ничего не писать."""
    check_lengths()
    print(f"must={len(MUST)} accept={len(ACCEPT)} reject={len(REJECT)} notes={len(NOTES)} симв.")
    for name, items in (("MUST", MUST), ("ACCEPT", ACCEPT), ("REJECT", REJECT)):
        print(f"\n{name}:")
        for item in items:
            print(f"  - ({len(item)}) {item}")
    if not with_api:
        return []
    targets = list_targets()
    print(f"\nЦели: {len(targets)} заданий курса {COURSE_ID}")
    for t in targets:
        gc = (t.get("solution_rules") or {}).get("grading_criteria")
        prov = (t.get("content_provenance") or {}).get("source")
        print(f"  id={t['id']} {t['external_uid']} criteria={'есть' if gc else 'нет'} prov={prov}")
    return targets


def apply(targets: list[dict[str, Any]], force: bool) -> None:
    """Записать критерии каждой цели, сверяя перед PATCH uid и курс, после — ответ.

    :raises RuntimeError: число целей не 25 (без `--force`), чужое задание в
        списке, ответ PATCH не содержит approved-критериев или остальные поля
        `solution_rules` изменились.
    """
    if len(targets) != EXPECTED_COUNT and not force:
        raise RuntimeError(f"Ожидалось {EXPECTED_COUNT} заданий, найдено {len(targets)}; --force для записи")
    criteria = build_criteria()
    for t in targets:
        task_id = t["id"]
        current = _api("GET", f"/tasks/{task_id}")
        if not str(current.get("external_uid") or "").startswith(UID_PREFIX):
            raise RuntimeError(f"id={task_id}: external_uid {current.get('external_uid')!r} не sdamgia:oge:13")
        if current["course_id"] != COURSE_ID:
            raise RuntimeError(f"id={task_id}: курс {current['course_id']} ≠ {COURSE_ID}")
        existing = (current.get("solution_rules") or {}).get("grading_criteria")
        if existing and existing.get("status") == "approved" and not force:
            logger.info("id=%s: критерии уже approved — пропуск", task_id)
            continue
        updated = _api("PATCH", f"/tasks/{task_id}", build_payload(current, criteria))
        gc = (updated.get("solution_rules") or {}).get("grading_criteria") or {}
        prov = updated.get("content_provenance") or {}
        rules_after = updated["solution_rules"]
        kept = all(
            rules_after.get(k) == v for k, v in current["solution_rules"].items() if k != "grading_criteria"
        )
        logger.info(
            "id=%s: status=%s must=%s accept=%s reject=%s reviewed_by=%s provenance=%s остальное_как_было=%s",
            task_id, gc.get("status"), len(gc.get("must") or []), len(gc.get("accept") or []),
            len(gc.get("reject") or []), gc.get("reviewed_by"), prov.get("source"), kept,
        )
        if gc.get("status") != "approved" or not kept:
            raise RuntimeError(f"id={task_id}: ответ PATCH не совпал с ожидаемым")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать критерии через API")
    parser.add_argument("--force", action="store_true", help="перезаписать уже подтверждённые / другое число целей")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with_api = bool(os.environ.get("LMS_API_KEY"))
    if args.apply and not with_api:
        raise RuntimeError("--apply требует LMS_API_KEY (сервисный ключ боевого API)")
    targets = plan(with_api=with_api)
    if args.apply:
        apply(targets, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
