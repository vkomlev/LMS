"""
Файлы-вложения к ответам ученика: раскладка на диске и её инварианты (tsk-575).

Отдельной таблицы вложений нет — каталог плоский
(`settings.attempt_attachments_upload_dir`), и всё знание о том, чьё это
вложение, живёт В ИМЕНИ файла. Отсюда два формата:

* `{attempt_id}_{uuid32}_{имя}` — исторический (до tsk-575). Так грузят
  клиенты, которые не присылают `task_id`; такие файлы уже лежат на проде,
  и ссылки на них живут в `answer_json`, поэтому формат остаётся рабочим;
* `{attempt_id}_t{task_id}_{uuid32}_{имя}` — текущий: вложение привязано к
  паре «попытка + задание».

**Зачем понадобилась пара.** Попытка в этой LMS охватывает МНОГО заданий, а
загрузка удаляла все прежние файлы попытки — «одно вложение на ответ» на деле
означало «одно вложение на десятки ответов». Ученик сдавал задание 1 с
`task1.py`, брался за задание 2 — и файл первого исчезал вместе со ссылкой,
по которой преподаватель шёл проверять работу. На проде так утрачено 180
файлов из 205 (201 работа, 13 учеников, замер 2026-08-07); восстановить их
нельзя — копий нет ни в бэкапах, ни в других каталогах.

Разбор имени однозначен: после `{attempt_id}_` в старом формате идёт uuid из
32 hex-символов, а `t` в hex не входит — спутать метку задания с началом uuid
невозможно.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.core.config import Settings

logger = logging.getLogger("app.attempt_attachments")

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
ATTACHMENT_ID_RE = re.compile(
    r"^(?P<attempt_id>\d+)_(?:t(?P<task_id>\d+)_)?[a-f0-9]{32}_[A-Za-z0-9._-]+$"
)


def upload_dir() -> Path:
    """Каталог вложений. Settings читается на вызов — тесты подменяют его monkeypatch'ем."""
    return Settings().attempt_attachments_upload_dir


def safe_upload_filename(filename: Optional[str]) -> str:
    """Имя файла, безопасное для плоского каталога: без путей и небезопасных символов."""
    base = os.path.basename(filename or "attachment")
    safe = SAFE_FILENAME_RE.sub("_", base).strip("._")
    return safe or "attachment"


def build_attachment_id(attempt_id: int, task_id: Optional[int], original_name: str) -> str:
    """Собрать имя файла (оно же `attachment_id`) для новой загрузки."""
    scope = f"{attempt_id}_" if task_id is None else f"{attempt_id}_t{int(task_id)}_"
    return f"{scope}{uuid4().hex}_{original_name}"


def parse_attachment_id(name: str) -> Optional[Tuple[int, Optional[int]]]:
    """
    Разобрать имя вложения в пару (`attempt_id`, `task_id`).

    `task_id` = None у файлов исторического формата. Возвращает None, если имя
    не наше (чужой файл в каталоге, попытка выйти из него, мусор из `answer_json`).
    """
    match = ATTACHMENT_ID_RE.fullmatch(name)
    if match is None:
        return None
    task_raw = match.group("task_id")
    return int(match.group("attempt_id")), (int(task_raw) if task_raw is not None else None)


def attempt_attachment_files(
    attempt_id: int,
    task_id: Optional[int] = None,
    *,
    include_untagged: bool = True,
) -> List[Path]:
    """
    Файлы вложений попытки на диске.

    * `task_id=None` — все файлы попытки (историческое поведение);
    * `task_id=N` — файлы этого задания. При `include_untagged=True` к ним
      добавляются файлы попытки БЕЗ метки задания: их прислал клиент, который
      ещё не умеет передавать `task_id`, и отказать ему значит сломать приём
      ответов у бота до его обновления.
    """
    directory = upload_dir()
    if not directory.exists():
        return []
    picked: List[Path] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        scope = parse_attachment_id(path.name)
        if scope is None or scope[0] != attempt_id:
            continue
        file_task_id = scope[1]
        if task_id is None:
            picked.append(path)
        elif file_task_id == task_id or (file_task_id is None and include_untagged):
            picked.append(path)
    return sorted(picked)


def files_replaced_by_upload(attempt_id: int, task_id: Optional[int]) -> List[Path]:
    """
    Файлы, которые вытесняет новая загрузка: СТРОГО та же пара (попытка, задание).

    Инвариант — «одно актуальное вложение на пару», а не на попытку. Загрузка
    без `task_id` вытесняет только такие же файлы без метки: у неё нет данных,
    чтобы отличить своё задание от чужого, и стереть файл соседнего задания
    (то, из-за чего и заведена tsk-575) она не должна.
    """
    directory = upload_dir()
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and parse_attachment_id(path.name) == (attempt_id, task_id)
    )


def attachment_file_path(attachment_id: str) -> Optional[Path]:
    """
    Путь к файлу вложения по его id, если id корректен и не выводит из каталога.

    Возвращает путь независимо от того, существует файл или нет: «нет файла» —
    отдельное состояние (утрачен/вытеснен), и решать его должен вызывающий.
    """
    if parse_attachment_id(attachment_id) is None:
        return None
    base = upload_dir().resolve()
    try:
        path = (base / attachment_id).resolve()
    except OSError:
        return None
    if not path.is_relative_to(base):
        logger.warning("Вложение вне каталога загрузок, отказ: %s", attachment_id)
        return None
    return path


def attachment_exists(attachment_id: Any) -> bool:
    """Есть ли файл вложения на диске (id из `answer_json` — данные, доверия им нет)."""
    if not isinstance(attachment_id, str) or not attachment_id:
        return False
    path = attachment_file_path(attachment_id)
    return path is not None and path.is_file()


def mark_missing_attachments(answer_json: Any) -> Any:
    """
    Проставить `missing: true` вложениям, файлов которых на диске уже нет (tsk-575).

    Считается на чтении, а не хранится: метаданные ответа (`filename`,
    `size_bytes`) — след того, что ученик файл действительно присылал, и
    вычищать их из `answer_json` значит терять доказательство при разборе
    жалобы и в аудите гейтов tsk-227/419. Флаг же самолечащийся: вернётся файл
    — исчезнет и пометка.

    Возвращает КОПИЮ (исходный dict может быть кэшем строки БД). Ответ без
    вложений возвращается как есть.
    """
    if not isinstance(answer_json, dict):
        return answer_json
    response = answer_json.get("response")
    if not isinstance(response, dict):
        return answer_json
    meta = response.get("meta")
    if not isinstance(meta, dict):
        return answer_json
    attachments = meta.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return answer_json

    marked: List[Any] = []
    changed = False
    for item in attachments:
        if not isinstance(item, dict):
            marked.append(item)
            continue
        if attachment_exists(item.get("attachment_id")):
            marked.append(item)
            continue
        marked.append({**item, "missing": True})
        changed = True

    if not changed:
        return answer_json

    patched_meta: Dict[str, Any] = {**meta, "attachments": marked}
    patched_response: Dict[str, Any] = {**response, "meta": patched_meta}
    return {**answer_json, "response": patched_response}
