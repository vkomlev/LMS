# -*- coding: utf-8 -*-
"""tsk-612: короткое человеческое название задания (`task_content.title`).

ЧТО ДЕЛАЕТ
Поле `task_content.title` в LMS есть с самого начала и уже доезжает до клиента
(`TaskRead.task_content` отдаётся целиком), но заполнено оно у 105 заданий из
7554 — и то шаблонно («ОГЭ. Задание 13 (презентация) — вариант 10593», импорт
sdamgia). У остальных подпись в списках собирается из условия
(`app/utils/task_title.py::humanize_task_title` → обрезка stem), из-за чего в
разборе темы три задания подряд выглядят как «Исходный код для этого задания:»
и на глаз не различаются (находка F5 аудита навигации методиста, tsk-600).

Скрипт читает активные задания без названия, просит модель придумать короткое
название по условию и складывает результат в JSONL для вычитки методистом.
С `--apply` записывает названия в `task_content.title` на проде.

ПОЧЕМУ НЕ ШАБЛОН ИЗ ИМЕЮЩИХСЯ ПОЛЕЙ
Шаблон «курс + номер варианта» (как у sdamgia) различает задания, но не говорит,
о чём задача: в списке из 90 заданий «ЕГЭ. Задание 6 — вариант 8214» ничем не
помогает методисту. Решение оператора (2026-08-14) — название по смыслу условия.

ПОЧЕМУ ПАКЕТАМИ ПО 8, А НЕ ПО ОДНОМУ
6.5 тысяч отдельных запросов дороже и дольше пакетных примерно на порядок.
Риск пакета — рассинхронизация «условие ↔ название»: модель возвращает названия
не в том порядке или пропускает элемент, и задание получает ЧУЖОЕ имя (хуже, чем
отсутствие имени: методист поверит подписи). Поэтому модель обязана вернуть id
рядом с названием, а `_match_batch` сверяет их с отправленными и молча
отбрасывает всё, чего не просили. Непришедшие id уходят в следующий проход, а не
подставляются «по позиции».

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
UPDATE трогает только `task_content->'title'` и только там, где название ещё
пустое (WHERE-guard внутри транзакции) — повторный прогон ничего не перезапишет.
Остальные ключи `task_content` не затрагиваются (`jsonb_set`, не перезапись
объекта). Ответы учеников, правила проверки, порядок заданий не затрагиваются
вовсе. Обратимо: `title` можно вернуть в JSON-null тем же WHERE.

ВОЗОБНОВЛЕНИЕ
Прогон долгий, поэтому результат пишется в JSONL построчно, а уже обработанные
id при повторном запуске пропускаются (`--out` тот же файл).

Запуск (dry-run, ничего не пишет в БД):
    PYTHONIOENCODING=utf-8 python scripts/backfill_task_titles_tsk612.py --limit 24

Запись на прод (после вычитки JSONL и go оператора):
    DBCHECK_OK=1 python scripts/backfill_task_titles_tsk612.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(project_root / ".env", encoding="utf-8-sig")

from app.services.llm import client as llm_client  # noqa: E402
from app.services.llm.contracts import Budget, LLMError, LLMMessage  # noqa: E402
from app.utils.task_title import _clean_stem  # noqa: E402

#: Сколько заданий уходит в один запрос к модели.
BATCH_SIZE = 8
#: Сколько символов условия показываем модели. Условия программных задач бывают
#: до сотен КБ; смысл задачи виден в начале, а полный текст только съел бы окно.
STEM_LIMIT = 1500
#: Границы приемлемого названия. Ниже — модель отписалась («Задача»), выше — она
#: пересказала условие вместо того, чтобы его назвать.
TITLE_MIN_LEN = 8
TITLE_MAX_LEN = 70

DEFAULT_OUT = project_root / "reviews" / "evidence" / "2026-08-14-tsk612-task-titles.jsonl"

_SYSTEM_PROMPT = """\
Ты — методист онлайн-школы информатики. Тебе показывают условия учебных заданий.
Для каждого придумай КОРОТКОЕ НАЗВАНИЕ — так его увидит методист в списке из
сотни заданий и должен понять, о чём задача, не открывая её.

Требования к названию:
- 3–7 слов, русский язык, именительный падеж, без точки в конце;
- называй СУТЬ задачи: что нужно сделать и с чем («Площадь фигуры Черепахи из
  четырёх команд», «Скорость передачи файла через два канала»);
- сперва смысл, потом различия: название должно читаться как нормальная русская
  фраза («Округление результата деления до двух знаков»), а не как пересказ
  выражения из кода («Округление 10 делить 3»);
- различающую деталь (числа, имена переменных, объекты условия) добавляй только
  тогда, когда без неё задание не отличить от соседнего;
- НЕ пиши слова «Задание», «Задача», «Вариант», номер задания ЕГЭ/ОГЭ, номер
  варианта и id — это уже показано рядом в интерфейсе;
- НЕ повторяй название курса — оно тоже показано рядом;
- названия в одном ответе должны ОТЛИЧАТЬСЯ друг от друга: если два задания
  похожи, найди в условиях то, чем они различаются (разный вопрос к одному коду,
  разные числа, разные переменные), иначе список снова станет нечитаемым;
- не выдумывай того, чего нет в условии.

Ответ — строго JSON: {"titles": [{"id": <число>, "title": "<название>"}, ...]}
Ровно по одному объекту на каждое присланное задание, id — из запроса."""


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


#: Задания без названия: ключа нет, JSON-null или пустая строка. Проверка идёт
#: через jsonb_typeof, а не `IS NULL`: в jsonb лежит JSON-null, и `->>'title'
#: IS NULL` дало бы верный ответ случайно, а `->'title' IS NULL` — нет
#: (см. память проекта про JSON-null в jsonb).
_EMPTY_TITLE_PREDICATE = """
    (
        NOT (t.task_content ? 'title')
        OR jsonb_typeof(t.task_content->'title') = 'null'
        OR (jsonb_typeof(t.task_content->'title') = 'string' AND btrim(t.task_content->>'title') = '')
    )
"""

_SELECT_SQL = f"""
SELECT t.id,
       t.course_id,
       c.title AS course_title,
       t.task_content->>'stem' AS stem,
       t.task_content->>'type' AS task_type
FROM tasks t
JOIN courses c ON c.id = t.course_id
WHERE t.is_active IS TRUE
  AND {_EMPTY_TITLE_PREDICATE}
ORDER BY t.course_id, t.order_position NULLS LAST, t.id
"""


async def fetch_candidates(
    conn: asyncpg.Connection, *, course_id: Optional[int], limit: Optional[int]
) -> list[dict[str, Any]]:
    """Активные задания без названия (по возрастанию курса и позиции в нём)."""
    sql = _SELECT_SQL
    args: list[Any] = []
    if course_id is not None:
        sql = sql.replace("WHERE t.is_active IS TRUE", "WHERE t.is_active IS TRUE AND t.course_id = $1")
        args.append(course_id)
    if limit is not None:
        sql = f"{sql} LIMIT ${len(args) + 1}"
        args.append(limit)
    rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _render_batch(items: Sequence[dict[str, Any]]) -> str:
    """Пакет заданий в виде текста запроса: id, курс, тип, очищенное условие."""
    parts: list[str] = []
    for it in items:
        stem = _shorten(_clean_stem(it.get("stem")), STEM_LIMIT) or "(условие пустое)"
        parts.append(
            f"id: {it['id']}\n"
            f"курс: {it.get('course_title') or '—'}\n"
            f"тип: {it.get('task_type') or '—'}\n"
            f"условие: {stem}"
        )
    return "\n\n---\n\n".join(parts)


def _valid_title(raw: Any) -> Optional[str]:
    """Отсеять отписки и пересказы. Возвращает нормализованное название или None."""
    if not isinstance(raw, str):
        return None
    title = " ".join(raw.split()).strip().strip('"').rstrip(".")
    if not (TITLE_MIN_LEN <= len(title) <= TITLE_MAX_LEN):
        return None
    lowered = title.lower()
    # «Задание 6», «Задача №3», «Вариант 8214» — ровно то, что уже показано рядом.
    if lowered.startswith(("задание", "задача", "вариант")):
        return None
    if "#" in title:
        return None
    return title


def _match_batch(payload_text: str, items: Sequence[dict[str, Any]]) -> dict[int, str]:
    """Сопоставить ответ модели с отправленными заданиями ПО id.

    Всё, чего не просили (чужие/выдуманные id), и всё, что не прошло
    `_valid_title`, отбрасывается: лучше оставить задание без названия и
    вернуться к нему следующим проходом, чем подписать его чужим именем.
    """
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return {}
    rows = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    asked = {int(it["id"]) for it in items}
    out: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            task_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if task_id not in asked:
            continue
        title = _valid_title(row.get("title"))
        if title:
            out[task_id] = title
    return out


async def generate_titles(items: Sequence[dict[str, Any]]) -> tuple[dict[int, str], Optional[str]]:
    """Названия для одного пакета. Возвращает (id → название, имя модели)."""
    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=_render_batch(items)),
    ]
    result = await llm_client.complete(
        messages,
        temperature=0.2,
        max_tokens=90 * len(items) + 120,
        purpose="task_title_backfill_tsk612",
        budget=Budget.BATCH,
        response_format={"type": "json_object"},
    )
    return _match_batch(result.text, items), result.model


_RETRY_HINT = """\

ВНИМАНИЕ: этим заданиям уже придумали названия, и они СОВПАЛИ между собой —
в списке курса задания снова неразличимы. Переименуй так, чтобы названия
отличались друг от друга, оставаясь осмысленными: разница между этими заданиями
есть в условиях (разный вопрос к одному коду, разные данные, разное действие).
Совпавшее название: «%s»."""


async def resolve_duplicates(
    candidates: Sequence[dict[str, Any]],
    titles: dict[int, str],
    fh: Any = None,
) -> int:
    """Переименовать задания, получившие одинаковые названия внутри курса.

    Модель видит только свой пакет из 8, поэтому совпадения возникают на границе
    пакетов (задание из первого пакета и задание из третьего). Здесь совпавшие
    задания идут в модель ОДНИМ пакетом — только так у неё есть шанс их развести.
    Возвращает число переименованных.
    """
    by_id = {c["id"]: c for c in candidates}
    renamed = 0
    for course_id, dup_title, ids in find_duplicate_titles(candidates, titles):
        items = [by_id[i] for i in ids if i in by_id][:BATCH_SIZE]
        if len(items) < 2:
            continue
        messages = [
            LLMMessage(role="system", content=_SYSTEM_PROMPT + (_RETRY_HINT % dup_title)),
            LLMMessage(role="user", content=_render_batch(items)),
        ]
        try:
            result = await llm_client.complete(
                messages,
                temperature=0.4,
                max_tokens=90 * len(items) + 120,
                purpose="task_title_backfill_tsk612_dedup",
                budget=Budget.BATCH,
                response_format={"type": "json_object"},
            )
        except LLMError as exc:
            print(f"  курс {course_id}: переименование не удалось — {exc}")
            continue
        got = _match_batch(result.text, items)
        # Первое задание вправе сохранить название: разводим ОСТАЛЬНЫЕ. Если
        # модель снова выдала одно и то же, ничего не меняем — пусть дубль
        # уедет в отчёт на вычитку, а не подменит смысл наугад.
        fresh = {tid: t for tid, t in got.items() if t.casefold() != dup_title.casefold()}
        if not fresh:
            continue
        for task_id, title in fresh.items():
            titles[task_id] = title
            renamed += 1
            if fh is not None:
                item = by_id[task_id]
                fh.write(
                    json.dumps(
                        {
                            "id": task_id,
                            "course_id": item["course_id"],
                            "course_title": item["course_title"],
                            "title": title,
                            "stem_head": _shorten(_clean_stem(item.get("stem")), 120),
                            "model": result.model,
                            "dedup": True,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if fh is not None:
            fh.flush()
    return renamed


_UPDATE_SQL = f"""
UPDATE tasks t
SET task_content = jsonb_set(t.task_content, '{{title}}', to_jsonb($2::text), true)
WHERE t.id = $1
  AND t.is_active IS TRUE
  AND {_EMPTY_TITLE_PREDICATE}
"""


async def apply_titles(conn: asyncpg.Connection, titles: dict[int, str]) -> int:
    """Записать названия в транзакции. Возвращает число реально изменённых строк."""
    updated = 0
    async with conn.transaction():
        for task_id, title in titles.items():
            status = await conn.execute(_UPDATE_SQL, task_id, title)
            # asyncpg отдаёт "UPDATE <n>" — считаем реально задетые строки, а не
            # размер словаря: WHERE-guard мог отсечь уже названное задание.
            updated += int(status.rsplit(" ", 1)[-1] or 0)
    return updated


def find_duplicate_titles(
    candidates: Sequence[dict[str, Any]], titles: dict[int, str]
) -> list[tuple[int, str, list[int]]]:
    """Одинаковые названия внутри одного курса.

    Дубль названия возвращает ровно ту проблему, ради которой всё делается:
    в списке курса два задания снова выглядят одинаково. Модель видит только
    свой пакет из 8, поэтому совпадения на границе пакетов неизбежны — их
    показываем оператору как адресный список для вычитки, а не прячем.
    """
    by_course: dict[tuple[int, str], list[int]] = {}
    course_names: dict[int, str] = {}
    for item in candidates:
        title = titles.get(item["id"])
        if not title:
            continue
        course_names[item["course_id"]] = item.get("course_title") or "—"
        by_course.setdefault((item["course_id"], title.casefold()), []).append(item["id"])
    dupes = [
        (course_id, titles[ids[0]], sorted(ids))
        for (course_id, _), ids in by_course.items()
        if len(ids) > 1
    ]
    return sorted(dupes, key=lambda x: (x[0], x[1]))


def _load_done(out_path: Path) -> dict[int, str]:
    """Уже сгенерированные названия из JSONL (для возобновления прогона)."""
    if not out_path.exists():
        return {}
    done: dict[int, str] = {}
    for line in out_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            done[int(row["id"])] = str(row["title"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return done


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-612: названия заданий по условию")
    parser.add_argument("--apply", action="store_true", help="записать названия в прод-БД")
    parser.add_argument("--course-id", type=int, default=None, help="ограничить одним курсом")
    parser.add_argument("--limit", type=int, default=None, help="сколько заданий взять")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="JSONL с названиями")
    parser.add_argument(
        "--from-file",
        action="store_true",
        help="не звать модель: взять готовые названия из --out (для --apply после вычитки)",
    )
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(args.out)

    conn = await asyncpg.connect(_dsn())
    try:
        candidates = await fetch_candidates(conn, course_id=args.course_id, limit=args.limit)
        print(f"Заданий без названия в выборке: {len(candidates)}; уже сгенерировано: {len(done)}")

        if args.from_file:
            titles = {c["id"]: done[c["id"]] for c in candidates if c["id"] in done}
        else:
            pending = [c for c in candidates if c["id"] not in done]
            titles = dict(done)
            with args.out.open("a", encoding="utf-8") as fh:
                for start in range(0, len(pending), BATCH_SIZE):
                    batch = pending[start : start + BATCH_SIZE]
                    try:
                        got, model = await generate_titles(batch)
                    except LLMError as exc:
                        print(f"  пакет {start // BATCH_SIZE + 1}: отбой модели — {exc}")
                        continue
                    for item in batch:
                        title = got.get(item["id"])
                        if not title:
                            continue
                        titles[item["id"]] = title
                        fh.write(
                            json.dumps(
                                {
                                    "id": item["id"],
                                    "course_id": item["course_id"],
                                    "course_title": item["course_title"],
                                    "title": title,
                                    "stem_head": _shorten(_clean_stem(item.get("stem")), 120),
                                    "model": model,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                    fh.flush()
                    print(
                        f"  пакет {start // BATCH_SIZE + 1}/{(len(pending) + BATCH_SIZE - 1) // BATCH_SIZE}: "
                        f"{len(got)}/{len(batch)} названий ({model})"
                    )
            titles = {c["id"]: titles[c["id"]] for c in candidates if c["id"] in titles}
            with args.out.open("a", encoding="utf-8") as fh:
                renamed = await resolve_duplicates(candidates, titles, fh)
            if renamed:
                print(f"  переименовано после совпадений: {renamed}")

        print(f"Готовых названий к записи: {len(titles)}")
        for item in candidates[:10]:
            title = titles.get(item["id"])
            if title:
                print(f"  #{item['id']} [{item['course_title'][:30]}] → {title}")

        dupes = find_duplicate_titles(candidates, titles)
        if dupes:
            print(f"\nСовпавшие названия внутри курса ({len(dupes)}) — на вычитку методисту:")
            for course_id, title, ids in dupes[:20]:
                print(f"  курс {course_id}: «{title}» → задания {', '.join(map(str, ids))}")

        if not args.apply:
            print(f"\nDry-run: в БД ничего не записано. Названия: {args.out}")
            return 0

        updated = await apply_titles(conn, titles)
        remaining = await conn.fetchval(
            f"SELECT count(*) FROM tasks t WHERE t.is_active IS TRUE AND {_EMPTY_TITLE_PREDICATE}"
        )
        print(f"Записано названий: {updated}. Активных заданий без названия осталось: {remaining}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
