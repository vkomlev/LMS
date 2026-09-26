"""tsk-1130: синонимы в эталон задания 8053 и зачёт ложного незачёта «Платеж» (прод).

Зачем. Задание 8053 (курс 1288 «Семь принципов тестирования», qa-manual) просит
одним словом назвать модуль, «где студент вводит данные карты и подтверждает
покупку курса». Эталон знал только лемму «оплата», которой нет ни в стеме, ни в
материалах курса, — ученик 4652 ответил «Платеж» (task_results 34584) и получил
незачёт. Добавляются законные по смыслу стема названия модуля: «платёж»,
«покупка», «касса», «платёжный» — все формы лемм (pymorphy3, приём tsk-796),
с «ё» и без (нормализация задания «ё» не снимает). Ничего не сужается:
существующие 10 форм «оплата» остаются первыми.

Попытки 34580/34581 того же ученика («В тот, где выдает больше ошибок», «Где
небольшое количество дефектов») не в формате «одним словом», вторая ещё и
противоположна по смыслу — их незачёт верен, скрипт проверяет, что движок
и после правки эталона их не засчитывает, и что они не изменились.

Вердикт 34584 не выдумывается: его выдаёт настоящий `CheckingService.check_task`
по уже обновлённому эталону (образец tsk-602). Пишутся поля штатной ручной
дооценки (`score`, `is_correct`, `checked_at`, `checked_by`) плюс пояснение
ученику в `metrics.comment`. В отличие от tsk-602 эта сдача ПОСЛЕДНЯЯ по
заданию, поэтому состояние задания меняется — кеш `student_course_state`
пересчитывается тем же `_refresh_course_state`, что у ручного зачёта, в той же
транзакции.

Протокол /db-check (режим записи): dry-run по умолчанию; «до» сверяется
дословно; одна транзакция; верификация до COMMIT.

Запуск (из корня LMS):
  .venv/Scripts/python.exe scripts/tsk1130_fix_8053_payment_synonyms.py
  DBCHECK_OK=1 .venv/Scripts/python.exe scripts/tsk1130_fix_8053_payment_synonyms.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Сервисы движка при импорте читают Settings; локальный .env нужен только для
# этого (смотрит в dev-БД) — подключение к проду скрипт строит сам из .mcp.json.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402
from app.services.manual_progress_service import _refresh_course_state  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("tsk1130")

TASK_ID = 8053
STUDENT_ID = 4652
TARGET_RESULT = 34584
TARGET_ANSWER = "Платеж"
UNTOUCHED_RESULTS = (34580, 34581)
CHECKED_BY = 2

BEFORE = [
    "оплаты", "оплата", "оплате", "оплату", "оплатой",
    "оплатою", "оплат", "оплатам", "оплатами", "оплатах",
]

ADD_FORMS = [
    # платёж
    "платёж", "платеж", "платежа", "платежу", "платежом", "платеже", "платежи",
    "платежей", "платежам", "платежами", "платежах",
    # покупка
    "покупка", "покупки", "покупке", "покупку", "покупкой", "покупкою", "покупок",
    "покупкам", "покупками", "покупках",
    # касса
    "касса", "кассы", "кассе", "кассу", "кассой", "кассою", "касс", "кассам",
    "кассами", "кассах",
    # платёжный (модуль)
    "платёжный", "платежный", "платёжного", "платежного", "платёжному",
    "платежному", "платёжным", "платежным", "платёжном", "платежном",
    "платёжная", "платежная", "платёжной", "платежной", "платёжную", "платежную",
    "платёжною", "платежною", "платёжное", "платежное", "платёжные", "платежные",
    "платёжных", "платежных", "платёжными", "платежными",
]

VERDICT_COMMENT = (
    "tsk-1130: ответ верен по смыслу — эталон задания знал только слово «оплата». "
    "Эталон дополнен, вердикт этой работы пересчитан."
)

RESULT_SQL = text(
    """
    SELECT tr.id, tr.task_id, tr.user_id, tr.attempt_id, tr.score, tr.max_score,
           tr.is_correct, tr.checked_at, tr.checked_by, tr.answer_json, tr.metrics,
           tr.submitted_at, t.task_content, t.solution_rules, t.max_score AS task_max_score,
           t.course_id
    FROM task_results tr JOIN tasks t ON t.id = tr.task_id
    WHERE tr.id = ANY(:ids) ORDER BY tr.id
    """
)


def prod_url() -> str:
    """URL боевой базы для asyncpg из `.mcp.json` (пароль не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    p = urlparse(mcp["mcpServers"]["learn_prod_db"]["args"][-1])
    user = quote(unquote(p.username or ""), safe="")
    pwd = quote(unquote(p.password or ""), safe="")
    return f"postgresql+asyncpg://{user}:{pwd}@{p.hostname}:{p.port or 5432}/{p.path.lstrip('/')}"


def engine_verdict(service: CheckingService, row: Any, rules_json: Any) -> tuple[bool | None, int, int]:
    """Вердикт движка проверки по сдаче при заданном эталоне."""
    content = TaskContent.model_validate(row["task_content"])
    rules = service.build_solution_rules(rules_json, fallback_max_score=row["task_max_score"] or 1)
    result = service.check_task(content, rules, StudentAnswer.model_validate(row["answer_json"]))
    return result.is_correct, result.score, result.max_score


async def run(apply: bool) -> int:
    """Сверка, план и (при --apply) запись одной транзакцией."""
    engine = create_async_engine(prod_url())
    service = CheckingService()
    try:
        async with AsyncSession(engine) as db:
            rows = {
                r["id"]: r
                for r in (
                    await db.execute(RESULT_SQL, {"ids": [TARGET_RESULT, *UNTOUCHED_RESULTS]})
                ).mappings().all()
            }
            target = rows.get(TARGET_RESULT)
            if target is None or len(rows) != 3:
                logger.error("Сдачи не найдены целиком — останов")
                return 1
            accepted = target["solution_rules"]["short_answer"]["accepted_answers"]
            if [a.get("value") for a in accepted] != BEFORE or any(a.get("score") != 1 for a in accepted):
                logger.error("Эталон изменился со снимка: %s", json.dumps(accepted, ensure_ascii=False))
                return 1
            answer = ((target["answer_json"] or {}).get("response") or {}).get("value")
            if (
                target["task_id"], target["user_id"], answer, target["is_correct"],
                target["score"], target["checked_by"], target["checked_at"],
            ) != (TASK_ID, STUDENT_ID, TARGET_ANSWER, False, 0, None, None):
                logger.error("Сдача %s не в ожидаемом состоянии «до» — останов", TARGET_RESULT)
                return 1

            new_rules = json.loads(json.dumps(target["solution_rules"]))
            new_rules["short_answer"]["accepted_answers"] = list(accepted) + [
                {"score": 1, "value": f} for f in ADD_FORMS
            ]

            before_verdict = engine_verdict(service, target, target["solution_rules"])
            verdict, score, max_score = engine_verdict(service, target, new_rules)
            logger.info("Сдача %s «%s»: движок сейчас %s, с новым эталоном %s (%s/%s)",
                        TARGET_RESULT, answer, before_verdict[0], verdict, score, max_score)
            if verdict is not True or max_score != target["max_score"]:
                logger.error("Движок не засчитывает сдачу и с новым эталоном — останов")
                return 1
            for rid in UNTOUCHED_RESULTS:
                v = engine_verdict(service, rows[rid], new_rules)
                logger.info("Сдача %s (не трогаем): движок с новым эталоном %s", rid, v[0])
                if v[0] is True:
                    logger.error("Сдача %s стала бы зачтённой — состав правки неверен", rid)
                    return 1

            logger.info("Форм в эталоне: %d → %d", len(accepted), len(new_rules["short_answer"]["accepted_answers"]))
            if not apply:
                logger.info("DRY-RUN: запись не выполнена.")
                await db.rollback()
                return 0

            await db.execute(
                text(
                    "UPDATE tasks SET solution_rules = jsonb_set(solution_rules, "
                    "'{short_answer,accepted_answers}', CAST(:acc AS jsonb), true), updated_at = now() "
                    "WHERE id = :id"
                ),
                {"acc": json.dumps(new_rules["short_answer"]["accepted_answers"], ensure_ascii=False), "id": TASK_ID},
            )
            res = await db.execute(
                text(
                    """
                    UPDATE task_results
                    SET is_correct = :ok, score = :score, checked_at = :now, checked_by = :by,
                        metrics = CASE WHEN jsonb_typeof(metrics) = 'object' THEN metrics
                                       ELSE '{}'::jsonb END
                                  || jsonb_build_object('comment', CAST(:comment AS text))
                    WHERE id = :id AND is_correct = false AND checked_by IS NULL
                    """
                ),
                {"ok": verdict, "score": score, "now": datetime.now(timezone.utc),
                 "by": CHECKED_BY, "comment": VERDICT_COMMENT, "id": TARGET_RESULT},
            )
            if res.rowcount != 1:
                raise RuntimeError(f"обновлено {res.rowcount} строк сдачи вместо 1")
            await _refresh_course_state(db, STUDENT_ID, int(target["course_id"]))

            after = {
                r["id"]: r
                for r in (
                    await db.execute(RESULT_SQL, {"ids": [TARGET_RESULT, *UNTOUCHED_RESULTS]})
                ).mappings().all()
            }
            t = after[TARGET_RESULT]
            vals = [a["value"] for a in t["solution_rules"]["short_answer"]["accepted_answers"]]
            if vals[:10] != BEFORE or "платеж" not in vals or len(vals) != 10 + len(ADD_FORMS):
                raise RuntimeError("эталон после записи не совпал с планом")
            if (t["is_correct"], t["score"], t["checked_by"]) != (True, score, CHECKED_BY):
                raise RuntimeError("вердикт сдачи после записи не совпал")
            for rid in UNTOUCHED_RESULTS:
                a = after[rid]
                if (a["is_correct"], a["score"], a["checked_by"]) != (False, 0, None):
                    raise RuntimeError(f"сдача {rid} изменилась")
            await db.commit()
            logger.info("COMMIT: эталон +%d форм, сдача %s зачтена, кеш прогресса пересчитан",
                        len(ADD_FORMS), TARGET_RESULT)
            return 0
    except Exception:  # noqa: BLE001 — любая осечка откатывает всю правку
        logger.exception("ОШИБКА — транзакция откачена")
        return 1
    finally:
        await engine.dispose()


def main() -> int:
    """Точка входа."""
    parser = argparse.ArgumentParser(description="tsk-1130: синонимы 8053 и зачёт сдачи 34584")
    parser.add_argument("--apply", action="store_true", help="Записать (по умолчанию dry-run)")
    return asyncio.run(run(parser.parse_args().apply))


if __name__ == "__main__":
    sys.exit(main())
