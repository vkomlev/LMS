# -*- coding: utf-8 -*-
"""tsk-369, шаг 2: скачать файлы-приложения из источников и сверить, что файл — от ЭТОЙ задачи.

ИСТОЧНИКИ
  * kompege  — `GET https://kompege.ru/api/v1/task/<id>` → `files[].url` (относительный,
               качается с того же домена без авторизации), `text` (условие), `key` (ответ);
  * sdamgia  — `GET https://inf-ege.sdamgia.ru/problem?id=<id>` → ссылки `/get_file?id=N`
               в теле страницы; расширение берётся из Content-Disposition/Content-Type;
  * polyakov — `GET https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId=<id>`;
               условие спрятано в `document.write(changeImageFilePath('…'))`, снимать теги
               до извлечения аргумента нельзя.
`yandex` здесь не обрабатывается: его файлы лежат в `markup.resources` и берутся
авторизованным API из браузера (метод tsk-100), это отдельный шаг.

ГЕЙТ (без него файл не привязывается)
ID сам по себе — не доказательство: в tsk-362 задание 3108 ссылалось на sdamgia:68243, а по
тому ID лежит совсем другая задача. Поэтому по каждой паре сверяются три признака:
  1. дословный фрагмент 60 букв из середины условия LMS есть в тексте источника
     (сравниваются только буквы: KaTeX в LMS дублирует формулу, посимвольно тексты не
     совпадают никогда, а sdamgia ещё и расставляет мягкие переносы внутри слов);
  2. значимые числа источника (3+ цифр) все присутствуют в условии LMS — именно они
     различают задачи одного типа с дословно общей преамбулой;
  3. тип файла согласован с формулировкой условия («файл электронной таблицы» → xls/ods,
     «текстовый файл» → txt): расхождение означает, что по ID лежит другая задача.
`verdict = match` только когда сошлись 1 и 2; 3 идёт отдельным флагом `ext_ok`.

Ничего не пишет в БД. На выходе JSON + скачанные файлы для шага 3.

Запуск:
  python scripts/tsk369_fetch_files.py --items <items.json> --out-dir <каталог> [--only kompege]
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0 Safari/537.36")
TIMEOUT = 60
PAUSE_SEC = 0.7

# Зеркалит allowlist LMS `app/api/v1/media.py`: файл с другим расширением отдать нельзя.
ALLOWED_EXT = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "pdf", "txt", "ods", "odt",
    "xlsx", "xls", "csv", "rar", "zip", "doc", "docx", "ppt", "pptx", "odp",
}
CONTENT_TYPE_EXT = {
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/msword": "doc",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/zip": "zip",
    "application/x-rar-compressed": "rar",
    "application/vnd.rar": "rar",
    "application/pdf": "pdf",
}

# Тип файла, ожидаемый формулировкой условия. Нужен как третий признак сверки.
SPREADSHEET_EXT = {"xls", "xlsx", "ods", "csv"}
TEXT_EXT = {"txt"}
DOC_EXT = {"doc", "docx", "odt", "rtf"}
# Архив согласуется с ЛЮБОЙ формулировкой: внутри лежит ровно тот файл, который обещан
# условием. У ОГЭ-11/12 приложение — всегда архив каталога («DEMO-12.rar»), и без этой
# поблажки задания отсеивались как «тип файла не совпал с формулировкой» (tsk-392).
ARCHIVE_EXT = {"zip", "rar"}


def fetch_bytes(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read(), dict(resp.headers)


def fetch(url: str) -> str:
    raw, _ = fetch_bytes(url)
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = s.replace("­", "").replace("​", "").replace("﻿", "")
    return re.sub(r"\s+", " ", s).strip()


def numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+", s or ""))


def prose(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^а-яa-z]+", " ", s).strip()


def middle_slice(s: str, size: int = 60) -> str:
    if len(s) <= size:
        return s
    start = max(0, len(s) // 2 - size // 2)
    return s[start:start + size]


def verdict_for(lms_stem: str, src_text: str) -> tuple[str, dict]:
    """Сверка «текст + числа» (перенесена из tsk-362, там же обоснование каждого шага)."""
    lms_p, src_p = prose(lms_stem), prose(src_text)
    frag = middle_slice(lms_p)
    prose_ok = bool(frag) and frag in src_p

    head_len = max(500, int(len(strip_html(lms_stem)) * 1.4))
    src_head = src_text[:head_len]
    lms_n, src_n = numbers(lms_stem), numbers(src_head)
    key_src = {n for n in src_n if len(n) >= 3}
    missing = sorted(key_src - lms_n)
    nums_ok = (len(missing) == 0) if key_src else (bool(lms_n & src_n) or not src_n)

    detail = {"fragment": frag[:80], "prose_ok": prose_ok,
              "key_src_numbers": sorted(key_src)[:15], "missing_in_lms": missing[:10],
              "nums_ok": nums_ok}
    if prose_ok and nums_ok:
        return "match", detail
    if prose_ok or nums_ok:
        return "weak", detail
    return "mismatch", detail


def expected_ext(stem_plain: str) -> set[str] | None:
    """Какого типа файл обещает условие. None — если по тексту не понять."""
    low = stem_plain.lower()
    if re.search(r"электронн\w* таблиц|таблич\w* файл", low):
        return SPREADSHEET_EXT | ARCHIVE_EXT
    if re.search(r"текстов\w* файл", low):
        return TEXT_EXT | DOC_EXT | ARCHIVE_EXT
    if re.search(r"текстов\w* редактор", low):
        return DOC_EXT | TEXT_EXT | ARCHIVE_EXT
    if re.search(r"файл, содержащ\w* текст|фрагмент базы данных", low):
        return SPREADSHEET_EXT | DOC_EXT | TEXT_EXT | ARCHIVE_EXT
    return None


def name_from_headers(headers: dict) -> str:
    """Имя файла из Content-Disposition («DEMO-12.rar»); пусто, если сервер его не дал.

    HTTP-заголовки Python декодирует как latin-1, а sdamgia кладёт в них UTF-8-байты:
    «Лермонтов.rar» без обратной перекодировки превращается в «Ð\x9bÐµÑ\x80…» и в таком
    виде уехал бы ученику в подпись ссылки (tsk-392).
    """
    disp = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disp)
    if not m:
        return ""
    name = urllib.parse.unquote(m.group(1)).strip()
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def ext_from_headers(headers: dict, url: str, fallback_name: str = "") -> str | None:
    disp = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disp)
    for candidate in (m.group(1) if m else "", fallback_name, urllib.parse.urlparse(url).path):
        ext = Path(urllib.parse.unquote(candidate or "")).suffix.lower().lstrip(".")
        if ext in ALLOWED_EXT:
            return ext
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").split(";")[0].strip()
    return CONTENT_TYPE_EXT.get(ctype)


def src_kompege(task_id: str) -> tuple[str, str | None, list[dict]]:
    data = json.loads(fetch(f"https://kompege.ru/api/v1/task/{task_id}"))
    text = strip_html(data.get("text") or "")
    for sub in data.get("subTask") or []:
        text += " " + strip_html(sub.get("text") or "")
    files = []
    for f in data.get("files") or []:
        url = f.get("url") or ""
        if url.startswith("/"):
            url = "https://kompege.ru" + url
        files.append({"url": url, "name": f.get("name") or ""})
    return text, (data.get("key") or "").strip() or None, files


def sdamgia_trim_header(text: str) -> str:
    """Срезать служебную шапку страницы «Решу ЕГЭ» перед условием.

    Внутри `prob_maindiv` сначала идёт «Тип 18 № 27415 Источник: Демонстрационная версия
    ЕГЭ−2021 … Раздел кодификатора ФИПИ … Задания для подготовки i», и её числа (номер
    задачи, год) попадали в «значимые числа источника». В условии LMS их, естественно,
    нет — из-за этого 30 из 34 заданий получали вердикт `weak` на ровном месте.
    """
    m = re.search(r"Задания для подготовки\s*i?\s*", text[:2000])
    if m:
        return text[m.end():].strip()
    m = re.search(r"Раздел кодификатора[^.]*?\.\s*", text[:2000])
    if m:
        return text[m.end():].strip()
    return re.sub(r"^\s*Тип \d+ № \d+\s*", "", text).strip()


def sdamgia_block(html: str, task_id: str, lms_stem: str = "") -> str:
    """Кусок страницы, относящийся ИМЕННО к нужной задаче.

    `problem?id=N` у «Решу ЕГЭ» нередко отдаёт не одну задачу, а связку: задания 19–21
    про камни печатаются тройкой (`prob_maindiv` + два `submaindiv`), и у каждой свой
    блок «Ответ». Хуже того, **ID в LMS указывает на первую задачу связки, а само задание
    в LMS бывает вторым или третьим**: у 2385 по ID 47016 (тип 19) лежит наша задача типа
    20 — её ответ `1011`, а первый ответ на странице `12` относится к соседней.

    Поэтому блок выбирается ПО ТЕКСТУ условия LMS (дословный фрагмент из середины), и
    только если текста нет — по шапке «Тип N № <task_id>», и лишь затем первым блоком.
    """
    parts = re.split(r'(?=<div[^>]*class="prob_maindiv")', html)
    blocks = [p for p in parts if "prob_maindiv" in p[:200]] or parts

    if lms_stem:
        # Искать надо по ХВОСТУ условия — по самому вопросу. Середина у связки 19-21 общая
        # (описание игры повторяется в каждой задаче), и поиск по ней находит все три блока.
        text = prose(lms_stem)
        for frag in (text[-140:-20] if len(text) > 200 else "", middle_slice(text)):
            if not frag:
                continue
            hits = [p for p in blocks if frag in prose(strip_html(p[:60000]))]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                # Вложенные блоки: связка целиком тоже содержит фрагмент — берём самый узкий.
                return min(hits, key=len)

    for part in blocks:
        if re.search(rf"№\s*{re.escape(task_id)}\b", strip_html(part[:4000])):
            return part
    return blocks[0] if blocks else html


# Служебные вложения шаблона страницы «Решу ОГЭ/ЕГЭ»: лежат внутри блока задачи, но к ней
# не относятся (одинаковы у всех задач). Без фильтра ученику к каждому заданию ОГЭ-14
# приложилась бы инструкция по обновлению сертификата Windows (tsk-392).
_SDAMGIA_BOILERPLATE = re.compile(r"сер[­\s]*ти[­\s]*фи[­\s]*ка[­\s]*та", re.I)


def src_sdamgia(task_id: str, lms_stem: str = "", oge: bool = False) -> tuple[str, str | None, list[dict]]:
    # ОГЭ живёт на отдельном домене того же движка: разметка и разбор совпадают.
    host = "https://inf-oge.sdamgia.ru" if oge else "https://inf-ege.sdamgia.ru"
    h = fetch(f"{host}/problem?id={task_id}")
    block = sdamgia_block(h, task_id, lms_stem)
    answer = None
    m = re.search(r'<div class="answer"[^>]*>(.{0,300}?)</div>', block, re.S)
    if m:
        m2 = re.search(r"Ответ:?\s*(.+)", strip_html(m.group(1)))
        if m2:
            answer = m2.group(1).strip().rstrip(".")
    m = re.search(r'class="prob_maindiv"[^>]*>(.{0,60000}?)<div class="answer"', block, re.S)
    block = m.group(1) if m else block
    text = strip_html(block)
    text = sdamgia_trim_header(text)
    cut = re.search(r"\bРешение\b", text)
    if cut and cut.start() > 100:
        text = text[:cut.start()]
    files = []
    for href in re.findall(r'href="([^"]+)"', block):
        if "get_file?id=" in href or Path(href).suffix.lower().lstrip(".") in ALLOWED_EXT - {"png", "jpg", "jpeg", "gif", "svg", "webp"}:
            if _SDAMGIA_BOILERPLATE.search(href):
                continue
            url = href if href.startswith("http") else host + href
            files.append({"url": url, "name": ""})
    return text, answer, files


def src_polyakov(task_id: str) -> tuple[str, str | None, list[dict]]:
    h = fetch(f"https://kpolyakov.spb.ru/school/ege/gen.php?action=viewTopic&topicId={task_id}")
    answer = None
    m = re.search(
        r'<div class="hidedata" id="%s">\s*<script>\s*document\.write\(\s*changeImageFilePath\(\s*\'(.*?)\'\s*\)'
        % re.escape(task_id), h, re.S)
    if m:
        answer = strip_html(m.group(1)).strip()
    m = re.search(r'(?s)class="topicview"[^>]*>(.*?)<td class="answer"', h)
    block = m.group(1) if m else h
    chunks = re.findall(r"changeImageFilePath\(\s*'(.*?)'\s*\)", block, re.S)
    text = strip_html(" ".join(chunks)) if chunks else strip_html(block)
    # Файл-ссылки внутри changeImageFilePath даны относительно НЕ /school/ege/, а базы из
    # скрытого поля filePath (JS переписывает `a href="X"` → filePath.value + X). Без этого
    # префикса ссылка ведёт в 404 (`/school/ege/ege-txt/…` вместо `/cms/files/ege-txt/…`) —
    # именно поэтому polyakov не отдал ни одного файла ни в tsk-369, ни в первом прогоне
    # tsk-390. Значение обычно «../../cms/files/» (tsk-390).
    fp = re.search(r'id=[\'"]filePath[\'"][^>]*value=[\'"]([^\'" >]+)', h)
    file_base = fp.group(1) if fp else ""
    page_base = "https://kpolyakov.spb.ru/school/ege/"
    files = []
    seen_urls: set[str] = set()
    for src_block in ([*chunks, block] if chunks else [block]):
        for href in re.findall(r'href=\\?"([^"\\]+)"', src_block):
            if Path(href).suffix.lower().lstrip(".") in ALLOWED_EXT - {"png", "jpg", "jpeg", "gif", "svg", "webp"}:
                if href.startswith("http"):
                    url = href
                else:
                    # JS префиксует только относительные пути, не начинающиеся с ':' (маркер
                    # «путь уже абсолютный»); повторяем эту логику перед разрешением base.
                    rel = href if href.startswith(":") else file_base + href
                    url = urllib.parse.urljoin(page_base, rel.lstrip(":"))
                # Один и тот же href встречается и в chunks, и в block — без дедупликации
                # ученик увидит ссылку на файл дважды (tsk-390).
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                files.append({"url": url, "name": Path(href).name})
    return text, answer, files


YANDEX_SUBJECT_ID = "ac7328ca-dd3d-4bea-8566-9c3177273a57"
_yandex_sk: list[str] = []


def _yandex_call(payload: list[dict]) -> dict | list:
    """POST /api/v5/gpttr с csrf-токеном. Авторизация НЕ нужна: и токен, и разбор задачи
    отдаются анонимно (проверено 2026-07-22) — метод tsk-100 требовал входа оператора для
    закрытых подборок, для публичных задач ЕГЭ хватает анонимного запроса."""
    if not _yandex_sk:
        raw = fetch("https://education.yandex.ru/api/v5/get-csrf-token")
        _yandex_sk.append(json.loads(raw)["sk"])
    req = urllib.request.Request(
        "https://education.yandex.ru/api/v5/gpttr",
        data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "x-csrf-token": _yandex_sk[0]},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def _yandex_task_payload(task: dict) -> tuple[str, str | None, list[dict]]:
    markup = task.get("markup") or {}
    parts = [blk.get("content", {}).get("text") or ""
             for blk in markup.get("layout") or [] if blk.get("kind") == "text"]
    text = strip_html(" ".join(parts))
    answers = []
    for blk in markup.get("answer_control_layout") or []:
        for a in (blk.get("content") or {}).get("correct_answers") or []:
            answers.append(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False))
    files = [{"url": r.get("link") or "", "name": r.get("title") or ""}
             for r in markup.get("resources") or [] if r.get("link")]
    return text, ("; ".join(answers) or None), files


def _yandex_variant(variant_id: str) -> list[dict]:
    data = _yandex_call([{"type": "public_get_variant_request_item",
                          "variant_id": variant_id,
                          "params": {"subject_id": YANDEX_SUBJECT_ID}}])
    return (data or {}).get("tasks") or []


def src_yandex(task_id: str, lms_stem: str = "") -> tuple[str, str | None, list[dict]]:
    """`<uuid>` — задача целиком; `<uuid>:<N>` — задание №N внутри подборки-варианта.

    Ссылка из поста Telegram нередко ведёт на ПОДБОРКУ, а не на задачу: по такому UUID
    запрос задачи отвечает 404. Тогда забирается вариант целиком (27 заданий) и нужное
    выбирается сверкой с условием LMS — то есть тем же гейтом, что и всё остальное,
    а не догадкой по порядку.
    """
    if ":" in task_id:
        variant_id, num = task_id.split(":", 1)
        tasks = _yandex_variant(variant_id)
        picked = next((t for t in tasks if str(t.get("number")) == str(num).strip()), None)
        if picked is None:
            raise ValueError(f"в подборке {variant_id} нет задания №{num}")
        return _yandex_task_payload(picked)

    try:
        data = _yandex_call([{"type": "get_task_by_id", "task_id": task_id,
                              "params": {"subject_id": YANDEX_SUBJECT_ID}}])
    except urllib.error.HTTPError as exc:
        if exc.code != 404 or not lms_stem:
            raise
        frag = middle_slice(prose(lms_stem))
        hits = []
        for t in _yandex_variant(task_id):
            payload = _yandex_task_payload(t)
            if frag and frag in prose(payload[0]):
                hits.append(payload)
        if len(hits) != 1:
            raise ValueError(
                f"подборка {task_id}: условие LMS совпало с {len(hits)} задачами — "
                "однозначно выбрать нельзя") from exc
        return hits[0]
    if isinstance(data, list):
        data = data[0] if data else {}
    return _yandex_task_payload(data)


GETTERS = {"kompege": src_kompege, "sdamgia": src_sdamgia,
           "polyakov": src_polyakov, "yandex": src_yandex}


def main(items_path: Path, out_dir: Path, only: str | None, limit: int | None,
         ids: list[int] | None = None, mapping: dict[int, tuple[str, str]] | None = None) -> None:
    items = json.loads(items_path.read_text(encoding="utf-8"))
    if mapping:
        # Источник, указанный оператором вручную: в шапке условия бывает опечатка в
        # номере задания («27360» вместо «23760»), и автоматический разбор уходит не туда.
        # Сверка после подмены остаётся прежней — ручной ключ её не отменяет.
        items = [i for i in items if i["id"] in mapping]
        for i in items:
            i["source"], i["source_id"] = mapping[i["id"]]
            i["via"] = "operator"
    if ids:
        items = [i for i in items if i["id"] in set(ids)]
    files_dir = out_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    results = []
    stats: dict[str, int] = {}
    processed = 0
    url_cache: dict[str, tuple[bytes, dict]] = {}
    for it in items:
        src, sid = it.get("source"), it.get("source_id")
        if src not in GETTERS or not sid:
            continue
        if only and src != only:
            continue
        if limit and processed >= limit:
            break
        processed += 1

        rec = {"id": it["id"], "course_id": it["course_id"], "source": src, "source_id": sid,
               "via": it["via"], "phrase": it.get("phrase")}
        try:
            if src == "yandex":
                text, answer, files = src_yandex(str(sid), it["stem"])
            elif src == "sdamgia":
                # Условие нужно, чтобы выбрать нужную задачу из связки 19-21 на странице.
                # `oge` ставит шаг 1 для партии ОГЭ: тот же движок, но домен inf-oge.
                text, answer, files = src_sdamgia(str(sid), it["stem"], oge=bool(it.get("oge")))
            else:
                text, answer, files = GETTERS[src](str(sid))
            time.sleep(PAUSE_SEC)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
            rec.update({"verdict": "error", "error": f"{type(exc).__name__}: {exc}"})
            stats["error"] = stats.get("error", 0) + 1
            results.append(rec)
            print(f"  [error   ] id={it['id']} {src}:{sid} — {exc}")
            continue

        # Сверяем ОЧИЩЕННЫЙ текст условия: в сыром HTML имена тегов и стили («td tr strong
        # border width px») попадают в «буквенную» часть и рвут дословный фрагмент.
        verdict, detail = verdict_for(it["stem"], text)
        rec.update({"verdict": verdict, "detail": detail, "answer_src": answer,
                    "src_text": text[:6000], "files": []})

        want = expected_ext(it["stem"])
        for n, f in enumerate(files):
            try:
                # Задачи ОГЭ-11/12 одной демо-версии ссылаются на ОДИН архив (DEMO-12.rar,
                # 33 МБ): без кэша прогон из 110 заданий скачал бы его 110 раз — 3.6 ГБ
                # трафика и час ожидания ради одного и того же файла (tsk-392).
                if f["url"] in url_cache:
                    data, headers = url_cache[f["url"]]
                else:
                    data, headers = fetch_bytes(f["url"])
                    url_cache[f["url"]] = (data, headers)
                    time.sleep(0.3)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                rec["files"].append({"url": f["url"], "error": str(exc)})
                continue
            ext = ext_from_headers(headers, f["url"], f.get("name", ""))
            if not ext:
                rec["files"].append({"url": f["url"], "error": "не определил расширение"})
                continue
            dest = files_dir / f"{it['id']}_{n}.{ext}"
            dest.write_bytes(data)
            rec["files"].append({
                # Имя из Content-Disposition — то, что увидит ученик в подписи ссылки.
                # У sdamgia имя в разметке страницы отсутствует, и без этого запасного
                # источника подпись выходила «Файл к заданию: Файл к заданию» вместо
                # «DEMO-12.rar» (tsk-392).
                "url": f["url"], "name": f.get("name") or name_from_headers(headers), "ext": ext,
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "path": str(dest),
                "ext_ok": (ext in want) if want else None,
            })

        got = [f for f in rec["files"] if f.get("ext")]
        rec["ext_ok"] = None if want is None else all(f.get("ext_ok") for f in got) if got else None
        rec["n_files"] = len(got)
        if not got:
            rec["verdict"] = "no_files" if verdict == "match" else f"{verdict}_no_files"
        stats[rec["verdict"]] = stats.get(rec["verdict"], 0) + 1
        results.append(rec)
        marks = "".join(f" {f['ext']}/{f['size']}b" for f in got)
        print(f"  [{rec['verdict']:14}] id={it['id']} {src}:{sid} ext_ok={rec['ext_ok']}{marks}")

    suffix = ("operator" if mapping else f"{only or 'all'}") + ("_add" if ids and not mapping else "")
    out_path = out_dir / f"fetched_{suffix}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nИтого: {stats}")
    print(f"Сохранено: {out_path}")


def reverify(items_path: Path, files: list[str]) -> None:
    """Пересчитать вердикты по уже сохранённым текстам источника, без обращения к сайтам.

    Нужно, когда меняется правило сверки: перекачивать сотни мегабайт файлов ради этого
    незачем — полный текст источника лежит в результатах шага 2.
    """
    items = {i["id"]: i for i in json.loads(items_path.read_text(encoding="utf-8"))}
    for f in files:
        path = Path(f)
        recs = json.loads(path.read_text(encoding="utf-8"))
        stats: dict[str, int] = {}
        for rec in recs:
            if not rec.get("src_text") or rec["id"] not in items:
                stats[rec.get("verdict", "?")] = stats.get(rec.get("verdict", "?"), 0) + 1
                continue
            text = rec["src_text"]
            if rec.get("source") == "sdamgia":
                text = sdamgia_trim_header(text)
            verdict, detail = verdict_for(items[rec["id"]]["stem"], text)
            if not [x for x in rec.get("files", []) if x.get("ext")]:
                verdict = "no_files" if verdict == "match" else f"{verdict}_no_files"
            rec["verdict"], rec["detail"] = verdict, detail
            stats[verdict] = stats.get(verdict, 0) + 1
        path.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{path.name}: {stats}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--only", help="ограничить одним источником (kompege/sdamgia/polyakov/yandex)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="только эти id заданий, через запятую (добор)")
    ap.add_argument("--map", dest="mapping", nargs="+",
                    help="ручной ключ источника от оператора: id:источник:id_в_источнике")
    ap.add_argument("--reverify", nargs="+", help="пересчитать вердикты в готовых файлах")
    a = ap.parse_args()
    if a.reverify:
        reverify(Path(a.items), a.reverify)
    else:
        ids = [int(x) for x in a.ids.split(",")] if a.ids else None
        mapping = None
        if a.mapping:
            mapping = {}
            for pair in a.mapping:
                tid, src, sid = pair.split(":", 2)
                mapping[int(tid)] = (src, sid)
        main(Path(a.items), Path(a.out_dir), a.only, a.limit, ids, mapping)
