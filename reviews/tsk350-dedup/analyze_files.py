# -*- coding: utf-8 -*-
"""tsk-350: разбор пар «разные приложенные файлы».

Имя файла /api/v1/media/<sha256>.<ext> — это SHA256 содержимого (CAS-хранилище,
app/api/v1/media.py). Значит:
  - одинаковый sha => байт-в-байт один файл => дубль;
  - у одного файл есть, у другого нет => «односторонний» (одна версия без приложения);
  - разный sha => разные байты (но, возможно, тот же контент в другом формате).

Второй ключ — ID первоисточника: если у обоих заданий один источник и один ID,
это одна и та же задача независимо от файла.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


SHA_RE = re.compile(r"([0-9a-f]{64})\.(xlsx|xls|ods|csv|txt|odt|docx|doc|zip)")


def file_shas(row: dict) -> dict[str, str]:
    """Все приложенные файлы задания как {sha256: ext}.

    Файл бывает и в attached_file_paths, и ссылкой /api/v1/media в тексте stem
    (project_lms_task_attachments) — берём отовсюду.
    """
    tc = row["task_content"] or {}
    blob = " ".join(str(tc.get(k) or "") for k in
                    ("stem", "attached_file_paths", "media", "code"))
    return {m.group(1): m.group(2) for m in SHA_RE.finditer(blob)}


def file_sha(row: dict) -> str | None:
    s = file_shas(row)
    return next(iter(s)) if s else None


def file_ext(row: dict) -> str:
    s = file_shas(row)
    return next(iter(s.values())) if s else ""


SRC_WORDS = {
    "комп егэ": "kompege", "компегэ": "kompege", "кегэ": "kompege", "комп.егэ": "kompege",
    "решу егэ": "sdamgia", "решуегэ": "sdamgia", "сдамгиа": "sdamgia",
    "поляков": "polyakov", "яндекс": "yandex", "крылов": "crylov",
}


def src_from_text(row: dict) -> tuple[str, str] | None:
    """ID первоисточника из шапки ТГ-поста «Задание N_ID (Источник)»."""
    stem = (row["task_content"] or {}).get("stem") or ""
    # «Задание 18_72603 (Решу ЕГЭ)», «Решение задания 18_72603 (Решу ЕГЭ)», «Разбор…»
    m = re.search(r"[Зз]адани[еяй]\s*[\d\-]+[ _]([0-9a-fA-Fа-яё]{3,})\s*\(?\s*([^)\n.]{2,20})",
                  stem)
    if not m:
        return None
    sid = m.group(1)
    tail = m.group(2).lower().strip()
    for word, src in SRC_WORDS.items():
        if word in tail:
            return (src, sid)
    return None


def source_id(row: dict) -> tuple[str, str] | None:
    """(источник, id-в-пространстве-источника) или None, если не определить."""
    uid = row["external_uid"] or ""
    tc = row["task_content"] or {}
    kind = (tc.get("source_kind") or "").lower()
    stid = tc.get("source_task_id")
    su = tc.get("source_url") or ""

    # каталожный импорт: ext:d4:<src>:date:<id> / ext:calib:<src>:date:<id>
    m = re.match(r"ext:(?:d4|calib):(kompege|sdamgia|polyakov):\d+:(.+)", uid)
    if m:
        return (m.group(1), m.group(2))
    # пилотный полякеровский: ext:polyakov:pilot:mini50:<id>
    m = re.match(r"ext:(polyakov|kompege|sdamgia):\w+:\w+:(.+)", uid)
    if m:
        return (m.group(1), m.group(2))

    # ТГ-обёртка и wp-навигатор: источник и id — в source_kind/source_task_id/url
    if kind in ("kompege", "sdamgia", "polyakov") and stid:
        # у kompege/sdamgia/polyakov id числовой; вычленить из stid или url
        num = re.search(r"\d+", str(stid))
        if not num and su:
            q = parse_qs(urlparse(su).query)
            num = re.search(r"\d+", (q.get("id") or q.get("topicId") or [""])[0])
        return (kind, num.group(0)) if num else (kind, str(stid))
    if kind == "yandex" and stid:
        # yandex id — UUID (иногда с суффиксом :N задачи в подборке)
        return ("yandex", str(stid))
    if kind == "yandex" and su:
        m = re.search(r"([0-9a-f-]{36})(?:/task/(\d+))?", su)
        if m:
            return ("yandex", m.group(1) + (f":{m.group(2)}" if m.group(2) else ""))
    # последний резерв — шапка ТГ-поста в тексте
    return src_from_text(row)


# Пары «разные байты», для которых содержимое скачано и сверено вручную (см. media/).
CONTENT_CHECKED = {
    frozenset((2167, 2315)): "дубль (таблица идентична, xls vs ods)",
    frozenset((3562, 4225)): "дубль (те же ячейки, csv с разными разделителями , и ;)",
    frozenset((3790, 3793)): "НЕ дубль (txt-данные реально разные)",
}


def classify(a: dict, b: dict) -> dict:
    shas_a, shas_b = set(file_shas(a)), set(file_shas(b))
    if shas_a and shas_b:
        file_state = ("одинаковый файл (sha)" if shas_a & shas_b
                      else "разные байты файла")
    elif shas_a or shas_b:
        file_state = "файл только у одного"
    else:
        file_state = "файлов нет"

    src_a, src_b = source_id(a), source_id(b)
    if src_a and src_b:
        src_state = "источник+ID совпали" if src_a == src_b else "источники разные"
    else:
        src_state = "источник одного неизвестен"

    # вердикт
    checked = CONTENT_CHECKED.get(frozenset((a["id"], b["id"])))
    if checked:
        verdict = ("ДУБЛЬ (контент сверен): " + checked if checked.startswith("дубль")
                   else "НЕ дубль (контент сверен)")
    elif file_state == "одинаковый файл (sha)":
        verdict = "ДУБЛЬ (идентичный файл)"
    elif src_state == "источник+ID совпали":
        verdict = "ДУБЛЬ (один первоисточник)"
    else:
        verdict = "смотреть глазами"

    return {
        "file_state": file_state, "src_state": src_state, "verdict": verdict,
        "shas_a": sorted(shas_a), "shas_b": sorted(shas_b), "src_a": src_a, "src_b": src_b,
        "ext_a": file_ext(a), "ext_b": file_ext(b),
    }


if __name__ == "__main__":
    import sys, io
    from collections import Counter
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    from detect import find_groups

    gs, weak = find_groups()
    pairs = [w for w in weak if w["reason"].startswith("разные приложенные файлы")]
    verdicts = Counter()
    for w in pairs:
        a, b = w["members"]
        c = classify(a, b)
        verdicts[c["verdict"]] += 1
        print(f"{a['id']:>5} / {b['id']:<5}  {c['file_state']:<24} {c['src_state']:<26} "
              f"=> {c['verdict']}")
        print(f"          {c['src_a']} vs {c['src_b']}")
    print()
    for v, n in verdicts.most_common():
        print(f"  {n:>2}  {v}")
