"""
Файлы-вложения к ответам ученика: раскладка и её инварианты (tsk-575, tsk-593).

Отдельной таблицы вложений нет — пространство плоское, и всё знание о том, чьё
это вложение, живёт В ИМЕНИ файла. Отсюда два формата:

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

**tsk-593: файлы переехали с диска приложения в объектное хранилище**
(`attachment_storage`, пространство `attempts`). Имя не изменилось — оно и есть
идентификатор, записанный в `answer_json`. Что изменилось для вызывающих:
проверка наличия файла стала СЕТЕВОЙ, поэтому функции здесь асинхронные, а там,
где вложений на экране много, наличие проверяется пачкой
(`existing_attachment_ids`), а не по одному.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

from app.services import attachment_storage

logger = logging.getLogger("app.attempt_attachments")

SPACE = attachment_storage.ATTEMPTS

ATTACHMENT_ID_RE = re.compile(
    r"^(?P<attempt_id>\d+)_(?:t(?P<task_id>\d+)_)?[a-f0-9]{32}_[A-Za-z0-9._-]+$"
)

#: Правило чистки имени общее для всех пространств хранилища (tsk-593), здесь
#: оставлено под прежним названием: на него ссылаются вызывающие и тесты.
safe_upload_filename = attachment_storage.safe_name


def build_attachment_id(attempt_id: int, task_id: Optional[int], original_name: str) -> str:
    """Собрать имя файла (оно же `attachment_id`) для новой загрузки."""
    scope = f"{attempt_id}_" if task_id is None else f"{attempt_id}_t{int(task_id)}_"
    return f"{scope}{uuid4().hex}_{original_name}"


def parse_attachment_id(name: str) -> Optional[Tuple[int, Optional[int]]]:
    """
    Разобрать имя вложения в пару (`attempt_id`, `task_id`).

    `task_id` = None у файлов исторического формата. Возвращает None, если имя
    не наше (чужой файл, попытка выйти из пространства, мусор из `answer_json`).
    """
    match = ATTACHMENT_ID_RE.fullmatch(name)
    if match is None:
        return None
    task_raw = match.group("task_id")
    return int(match.group("attempt_id")), (int(task_raw) if task_raw is not None else None)


def is_valid_attachment_id(attachment_id: Any) -> bool:
    """Похоже ли значение на наше имя вложения (данные из `answer_json` — не доверие)."""
    return isinstance(attachment_id, str) and parse_attachment_id(attachment_id) is not None


async def attempt_attachment_names(
    attempt_id: int,
    task_id: Optional[int] = None,
    *,
    include_untagged: bool = True,
) -> List[str]:
    """
    Имена вложений попытки, реально лежащих в хранилище.

    * `task_id=None` — все файлы попытки (историческое поведение);
    * `task_id=N` — файлы этого задания. При `include_untagged=True` к ним
      добавляются файлы попытки БЕЗ метки задания: их прислал клиент, который
      ещё не умеет передавать `task_id`, и отказать ему значит сломать приём
      ответов у бота до его обновления.
    """
    picked: List[str] = []
    for name in await attachment_storage.list_names(SPACE, f"{attempt_id}_"):
        scope = parse_attachment_id(name)
        if scope is None or scope[0] != attempt_id:
            continue
        file_task_id = scope[1]
        if task_id is None:
            picked.append(name)
        elif file_task_id == task_id or (file_task_id is None and include_untagged):
            picked.append(name)
    return sorted(picked)


async def names_replaced_by_upload(attempt_id: int, task_id: Optional[int]) -> List[str]:
    """
    Файлы, которые вытесняет новая загрузка: СТРОГО та же пара (попытка, задание).

    Инвариант — «одно актуальное вложение на пару», а не на попытку. Загрузка
    без `task_id` вытесняет только такие же файлы без метки: у неё нет данных,
    чтобы отличить своё задание от чужого, и стереть файл соседнего задания
    (то, из-за чего и заведена tsk-575) она не должна.
    """
    return sorted(
        name
        for name in await attachment_storage.list_names(SPACE, f"{attempt_id}_")
        if parse_attachment_id(name) == (attempt_id, task_id)
    )


async def attachment_exists(attachment_id: Any) -> bool:
    """Есть ли файл вложения в хранилище (id из `answer_json` — данные, доверия им нет)."""
    if not is_valid_attachment_id(attachment_id):
        return False
    return await attachment_storage.exists(SPACE, attachment_id)


def collect_attachment_ids(answer_json: Any) -> List[str]:
    """Имена вложений, на которые ссылается ответ. Пусто, если ссылок нет."""
    if not isinstance(answer_json, dict):
        return []
    response = answer_json.get("response")
    if not isinstance(response, dict):
        return []
    meta = response.get("meta")
    if not isinstance(meta, dict):
        return []
    attachments = meta.get("attachments")
    if not isinstance(attachments, list):
        return []
    return [
        item["attachment_id"]
        for item in attachments
        if isinstance(item, dict) and is_valid_attachment_id(item.get("attachment_id"))
    ]


async def existing_attachment_ids(answer_jsons: Sequence[Any]) -> Set[str]:
    """Какие вложения этих ответов реально лежат в хранилище (одной пачкой).

    Проверка наличия — сетевой запрос, поэтому страница с двумя десятками работ
    обязана спрашивать хранилище один раз параллельно, а не двадцать раз подряд.
    """
    wanted: List[str] = []
    for answer_json in answer_jsons:
        wanted.extend(collect_attachment_ids(answer_json))
    if not wanted:
        return set()
    return await attachment_storage.existing_names(SPACE, wanted)


def mark_missing_attachments(answer_json: Any, existing: Iterable[str]) -> Any:
    """
    Проставить `missing: true` вложениям, файлов которых в хранилище уже нет (tsk-575).

    `existing` — имена, наличие которых уже проверено (`existing_attachment_ids`).
    Сама функция в хранилище не ходит: её зовут в цикле по строкам, и запрос
    на каждую строку означал бы страницу из десятков сетевых обращений.

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

    present = set(existing)
    marked: List[Any] = []
    changed = False
    for item in attachments:
        if not isinstance(item, dict):
            marked.append(item)
            continue
        attachment_id = item.get("attachment_id")
        if isinstance(attachment_id, str) and attachment_id in present:
            marked.append(item)
            continue
        marked.append({**item, "missing": True})
        changed = True

    if not changed:
        return answer_json

    patched_meta: Dict[str, Any] = {**meta, "attachments": marked}
    patched_response: Dict[str, Any] = {**response, "meta": patched_meta}
    return {**answer_json, "response": patched_response}


async def mark_missing_one(answer_json: Any) -> Any:
    """Пометить утраченные вложения одного ответа (сама сходит в хранилище)."""
    if not collect_attachment_ids(answer_json):
        return answer_json
    existing = await existing_attachment_ids([answer_json])
    return mark_missing_attachments(answer_json, existing)


async def mark_missing_many(answer_jsons: Sequence[Any]) -> List[Any]:
    """Пометить утраченные вложения пачки ответов: один поход в хранилище на все."""
    existing = await existing_attachment_ids(answer_jsons)
    return [mark_missing_attachments(aj, existing) for aj in answer_jsons]
