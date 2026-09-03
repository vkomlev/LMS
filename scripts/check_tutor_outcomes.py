# -*- coding: utf-8 -*-
"""Регулярный чек: чем кончаются разговоры с ИИ-наставником (tsk-661).

Зачем. У контура наставника были счётчики сессий и ходов. Они говорят, что
механизм вызывался, и молчат о том, чем дело кончилось. Именно поэтому разрыв
«до наставника доходит 2 % поводов» всплыл случайно и через полтора месяца после
выката, при сборке зонтика tsk-657, а не в первую неделю.

Мера успеха — ПАРА цифр, обе обязательны (решение оператора 25.08):

1. **Охват** — какая доля поводов дошла до наставника. Повод = пара
   «ученик + задание», по которой в окне была хотя бы одна неверная сдача
   (повторные попытки по тому же заданию повод не удваивают: разговор тоже
   заводится один на пару).

   **Знаменатель — поводы, где ученик ЗАСТРЯЛ** (2+ неверных попытки), а не все
   подряд (tsk-779, решение оператора 03.09). Замер на боевых данных: из 254
   поводов 182, то есть 72%, — ученик ошибся один раз и тут же сдал верно сам.
   Наставник там не нужен, звать его никто и не станет, а в знаменателе он топил
   долю втрое: чек показывал 6% при пороге 20% и звал разбирать провал там, где
   среди застрявших охват 21%, то есть цель уже достигнута. Доля «промахнулся
   один раз» держится около 70% из недели в неделю — свойство, а не случайность
   окна. Общая доля по всем поводам осталась справочной строкой: смена
   знаменателя не должна выглядеть подгонкой под порог.
2. **Исход** — какая доля состоявшихся разговоров кончилась тем, что ученик сам
   сдал задание верно.

Одна цифра без другой врёт в обе стороны. Охват без исхода — ровно та слепота,
на которой контур проехал полтора месяца («вызывался» ≠ «помог»). Исход без
охвата — красивая доля от числа, о размере которого никто не спрашивал.

Что здесь НЕ считается разговором. Сессия создаётся сама, при простом открытии
задания: клиент читает состояние, а `get_or_create` заводит запись. Плюс ход
(`turns`) растёт ДО обращения к модели, то есть остаётся и тогда, когда модель
отказала. Поэтому ни «число сессий», ни «число ходов» мерой быть не могут.
Разговор считается состоявшимся, только если наставник сказал хотя бы одно слово
(есть строка `ai_tutor_message` с ролью `tutor`).

Три вида «ничего не вышло» разведены намеренно, потому что чинятся они в разных
местах:

* **не дошёл** — повод был, разговора не случилось вовсе (продуктовая причина:
  кнопка невзрачная, tsk-659);
* **молчание модели** — ход был, ответа нет (техническая причина: провайдер,
  цепочки, tsk-671/tsk-678). Это НЕ «ученику не помогло». Считается по ходам
  ученика, а не по строкам `llm_usage_event`: там строка пишется на каждую
  ПОПЫТКУ модели, и цепочка, перебравшая три модели и ответившая четвёртой,
  выглядела бы тремя отказами при полностью удачном для ученика обращении;
* **пустой заход** — сессия с нулём ходов (след дефекта и простых открытий
  задания, а не поведения ученика).

Ловушка, ради которой всё это писалось. «Решил после разговора» посчитать легко,
но наставник намеренно не получает эталон (решение №1 в tsk-572) — значит зачёт
сразу после диалога может означать и то, что ученик списал ответ из чата. Мера,
которая этого не различает, хуже отсутствия меры. Поэтому подозрительные зачёты
считаются успехом (признак грубый, тихо выбрасывать живые случаи опаснее), но
печатаются ОТДЕЛЬНОЙ строкой — решение оператора 25.08. Признак срабатывает,
если верный ответ дословно есть в репликах наставника ЛИБО сдача пришла меньше
чем через полминуты после его реплики, а сам ученик не написал ни слова.

Почему окно упирается в 24 августа. До tsk-666 наставник не видел ни задания,
ни ответа: условие уезжало модели только в первой реплике, снимок ответа не
доезжал ни разу — ноль из 27 сессий. Мерить «помог ли» по разговорам со слепым
собеседником бессмысленно, поэтому окно жёстко обрезано снизу. Дата взята НЕ по
времени коммита, а по факту выката: первая сессия с непустым снимком ответа —
24.08.2026 18:16 МСК (правки на бою уезжают раньше, чем ложатся в git).

Read-only: ни одного UPDATE. Чинит не этот скрипт — он только сообщает.

Куда смотрит. В базу из `DATABASE_URL`; по умолчанию это dev (прод от скриптов
закрыт, tsk-246). Прод — явным override:
    DATABASE_URL=<прод-dsn> python scripts/check_tutor_outcomes.py
Скрипт всегда печатает хост и базу, которую проверил.

Запуск из корня проекта:
    python scripts/check_tutor_outcomes.py             # полный отчёт
    python scripts/check_tutor_outcomes.py --quiet     # только находки
    python scripts/check_tutor_outcomes.py --days 30   # окно шире недели

Под планировщиком чек идёт через общий вход `scripts/weekly_checks.py
tutor-outcomes` — он подставляет боевой DSN и пишет журнал
`logs/tutor_outcomes_check.log`.

Коды выхода: 0 — тревоги нет (в том числе когда данных мало для вывода);
1 — есть находка; 2 — ошибка выполнения.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Mapping, Sequence, Set as AbstractSet
from typing import Any, Optional

# tsk-641: LMS_CHECK_NO_CONSOLE ставит `scripts/weekly_checks.py`, когда чек идёт под
# планировщиком через pythonw.exe — консоли там нет, а `os.system` поднял бы cmd.exe
# со своим окном.
if sys.platform == "win32" and not os.environ.get("LMS_CHECK_NO_CONSOLE"):
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

#: Момент, с которого наставник видит задание и ответ ученика (tsk-666). Раньше
#: этой отметки мерить нечего: собеседник был слепым. См. модульную докстроку —
#: дата по факту выката, а не по коммиту.
FIX_MOMENT = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)  # 18:00 МСК

#: Учётки, которые не являются учениками, хотя формально имеют роль `student`.
#: Роли admin/teacher/methodist отсекаются запросом; сюда попадает то, что ролями
#: не ловится: личные учётки оператора и заведённые вручную тестовые.
#: Тариф `test` таким признаком НЕ является — на нём сидят и живые ученики,
#: которым выдали безлимит.
SERVICE_STUDENT_IDS: tuple[int, ...] = (142, 4541)

#: Меньше этого числа поводов — тревогу не поднимаем. Не потому, что всё хорошо,
#: а потому, что на трёх случаях доля не значит ничего: одна сдача двигает её на
#: треть. Чек в этом случае честно пишет «данных мало».
MIN_SAMPLE = 5

#: Ниже этой доли охват считается находкой (при достаточной выборке).
COVERAGE_ALARM = 0.20

#: Доля ходов, оставшихся без ответа наставника, с которой это уже находка.
MODEL_FAILURE_ALARM = 0.30

#: Минимум ходов, ниже которого долю молчания не оцениваем.
MIN_TURNS_SAMPLE = 5

#: Быстрее этого зачёт после реплики наставника выглядит переписанным из чата.
COPY_SUSPECT_SECONDS = 30


# Ученики — все, кроме персонала и служебных учёток. Персонал ловим по ролям:
# у оператора и преподавателя роль `student` тоже есть, и без этого фильтра их
# прогоны по проду попали бы в метрику как ученические.
SQL_REAL_STUDENTS = """
SELECT u.id
FROM users u
WHERE NOT EXISTS (
        SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = u.id AND r.name IN ('admin', 'teacher', 'methodist')
      )
  AND u.id <> ALL(:service_ids)
"""

# Поводы: пары «ученик + задание» с неверной сдачей в окне. `has_tutor` — есть ли
# у ученика вообще право на наставника: повод, которого тариф закрыть не может,
# это не провал наставника, и в общем знаменателе он врёт.
SQL_REASONS = f"""
WITH real_students AS ({SQL_REAL_STUDENTS})
SELECT tr.user_id, tr.task_id,
       min(tr.submitted_at) AS first_wrong_at,
       count(*) AS wrong_tries,
       coalesce((
           SELECT p.ai_tutor_limit IS NULL OR p.ai_tutor_limit > 0
           FROM student_subscription s JOIN subscription_plan p ON p.id = s.plan_id
           WHERE s.student_id = tr.user_id AND s.ends_on IS NULL
           LIMIT 1
       ), false) AS has_tutor
FROM task_results tr
JOIN real_students rs ON rs.id = tr.user_id
WHERE tr.is_correct IS FALSE
  AND tr.submitted_at >= :since
GROUP BY tr.user_id, tr.task_id
"""

# Разговоры в окне. Окном считается активность наставника или ученика, а не дата
# создания сессии: сессия могла завестись раньше от простого открытия задания.
SQL_SESSIONS = f"""
WITH real_students AS ({SQL_REAL_STUDENTS})
SELECT s.id, s.student_id, s.task_id, s.mode, s.turns, s.status,
       s.created_at,
       (SELECT count(*) FROM ai_tutor_message m
         WHERE m.session_id = s.id AND m.role = 'tutor') AS tutor_msgs,
       (SELECT count(*) FROM ai_tutor_message m
         WHERE m.session_id = s.id AND m.role = 'student') AS student_msgs,
       (SELECT max(m.created_at) FROM ai_tutor_message m
         WHERE m.session_id = s.id AND m.role = 'tutor') AS last_tutor_at,
       (SELECT string_agg(m.content, E'\\n') FROM ai_tutor_message m
         WHERE m.session_id = s.id AND m.role = 'tutor') AS tutor_text
FROM ai_tutor_session s
JOIN real_students rs ON rs.id = s.student_id
WHERE greatest(s.created_at, s.last_activity_at) >= :since
ORDER BY s.id
"""

# Первая верная сдача по паре ПОСЛЕ последней реплики наставника — и её текст,
# чтобы отличить понимание от переписанного из чата ответа.
SQL_WIN_AFTER = """
SELECT tr.id, tr.submitted_at,
       coalesce(tr.answer_json->'response'->>'value',
                tr.answer_json->'response'->>'text',
                tr.answer_json->'response'->>'code', '') AS answer
FROM task_results tr
WHERE tr.user_id = :uid AND tr.task_id = :tid
  AND tr.is_correct IS TRUE
  AND tr.submitted_at > :after
ORDER BY tr.submitted_at
LIMIT 1
"""

# Попытки моделей за окном — СПРАВКА, а не мера молчания.
#
# Строка в `llm_usage_event` пишется на каждую ПОПЫТКУ модели, а не на обращение
# ученика. Одно обращение 25.08 дало четыре строки: grok отказал, gpt-5.5 не
# успел, claude вернул ошибку внутри успешного ответа, gpt-5.4-mini ответил —
# ученик при этом получил ответ и ничего не заметил. Считать по этим строкам
# «сколько раз наставник промолчал» — значит записать в отказ ровно ту работу,
# ради которой цепочки и переделывали (tsk-671, tsk-678).
#
# Поэтому мера молчания считается по ходам ученика (см. `silent_turns` ниже), а
# отсюда берётся только фон: какие модели и как часто подводят. События без
# `student_id` отброшены — это самопроверка цепочек, не ученик.
SQL_MODEL_CALLS = f"""
WITH real_students AS ({SQL_REAL_STUDENTS})
SELECT e.outcome, count(*) AS n
FROM llm_usage_event e
JOIN real_students rs ON rs.id = e.student_id
WHERE e.purpose = 'tutor' AND e.created_at >= :since
GROUP BY e.outcome
ORDER BY n DESC
"""


def _normalize(text_value: str) -> str:
    """Схлопнуть пробелы и регистр — сравнивать ответы иначе бессмысленно."""
    return re.sub(r"\s+", " ", (text_value or "").strip().lower())


def looks_copied(
    answer: str, tutor_text: str, seconds_after: Optional[float], student_msgs: int
) -> bool:
    """Похож ли зачёт на переписанный из чата.

    Два признака, любой достаточен:

    1. Верный ответ дословно встречается в репликах наставника. Сравнение идёт по
       границам слова: без этого ответ «5» находился бы в любом тексте, где есть
       пятёрка, и признак срабатывал бы всегда.
    2. Сдача пришла быстрее чем за полминуты после реплики наставника, а сам
       ученик не написал ни слова. Своей работы в таком заходе не было.

    Признак заведомо грубый и потому только помечает, а не отменяет зачёт.
    """
    ans = _normalize(answer)
    if ans and tutor_text:
        pattern = r"(?<![0-9A-Za-zА-Яа-яЁё])" + re.escape(ans) + r"(?![0-9A-Za-zА-Яа-яЁё])"
        if re.search(pattern, _normalize(tutor_text)):
            return True
    if (
        seconds_after is not None
        and seconds_after < COPY_SUSPECT_SECONDS
        and student_msgs == 0
    ):
        return True
    return False


def split_by_struggle(
    gated: Sequence[Mapping[str, Any]],
    covered: AbstractSet[tuple[int, int]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], set[tuple[int, int]]]:
    """Разделить поводы на «ученик застрял» и «промахнулся один раз» (tsk-779).

    Вынесено отдельной функцией, потому что именно этот раздел задаёт знаменатель
    охвата, а значит и вердикт чека. Ошибка здесь тихо сдвинет продуктовую меру,
    по которой принимают решения о наставнике.

    :param gated: поводы (пары «ученик + задание») у учеников с правом на
        наставника; у каждого ожидается ``user_id``, ``task_id``, ``wrong_tries``.
    :param covered: пары, по которым наставник заговорил после неверной сдачи.
    :returns: тройка «застрявшие поводы, поводы с одной ошибкой, застрявшие из
        числа закрытых наставником».
    """
    struggled = [r for r in gated if int(r["wrong_tries"]) > 1]
    one_off = [r for r in gated if int(r["wrong_tries"]) <= 1]
    struggled_keys = {(int(r["user_id"]), int(r["task_id"])) for r in struggled}
    return struggled, one_off, {k for k in covered if k in struggled_keys}


def _pct(part: int, whole: int) -> str:
    """Доля в процентах либо прочерк, если делить не на что."""
    return f"{part / whole * 100:.0f}%" if whole else "—"


async def main(quiet: bool = False, days: int = 7) -> int:
    """Посчитать охват и исход разговоров с наставником за окно."""
    from sqlalchemy import bindparam, text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("ОШИБКА: не задан DATABASE_URL (ни в окружении, ни в .env)", file=sys.stderr)
        return 2
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    now = datetime.now(timezone.utc)
    since = max(now - timedelta(days=days), FIX_MOMENT)
    params: dict[str, Any] = {
        "since": since,
        "service_ids": list(SERVICE_STUDENT_IDS),
    }
    # `service_ids` уезжает массивом (`<> ALL`), поэтому тип биндинга задаём явно:
    # без этого asyncpg получил бы список там, где ждёт скаляр.
    reasons_stmt = text(SQL_REASONS).bindparams(bindparam("service_ids", expanding=False))
    sessions_stmt = text(SQL_SESSIONS).bindparams(bindparam("service_ids", expanding=False))
    calls_stmt = text(SQL_MODEL_CALLS).bindparams(bindparam("service_ids", expanding=False))

    engine = create_async_engine(dsn, echo=False)
    try:
        async with engine.connect() as conn:
            where = (await conn.execute(text(
                "SELECT current_database() AS db, inet_server_addr()::text AS host"
            ))).mappings().first()
            print(f"Проверяю базу: {where['db']} на {where['host'] or 'localhost'}")

            reasons = (await conn.execute(reasons_stmt, params)).mappings().all()
            sessions = (await conn.execute(sessions_stmt, params)).mappings().all()
            calls = (await conn.execute(calls_stmt, params)).mappings().all()

            # Исход разговора: сдал ли ученик верно ПОСЛЕ последней реплики
            # наставника. Отсчёт именно от неё, а не от начала сессии: зачёт,
            # пришедший до первого слова наставника, к разговору отношения не имеет.
            outcomes: dict[int, dict[str, Any]] = {}
            for s in sessions:
                if not s["tutor_msgs"]:
                    continue
                row = (await conn.execute(text(SQL_WIN_AFTER), {
                    "uid": s["student_id"], "tid": s["task_id"], "after": s["last_tutor_at"],
                })).mappings().first()
                if row is None:
                    outcomes[s["id"]] = {"solved": False}
                    continue
                seconds = (row["submitted_at"] - s["last_tutor_at"]).total_seconds()
                outcomes[s["id"]] = {
                    "solved": True,
                    "seconds": seconds,
                    "suspect": looks_copied(
                        row["answer"], s["tutor_text"] or "", seconds, int(s["student_msgs"])
                    ),
                }
    finally:
        await engine.dispose()

    since_msk = (since + timedelta(hours=3)).strftime("%d.%m %H:%M")
    print(f"\nОкно: с {since_msk} МСК (не раньше починки tsk-666) по сейчас")

    # --- знаменатель -------------------------------------------------------
    gated = [r for r in reasons if r["has_tutor"]]
    ungated = [r for r in reasons if not r["has_tutor"]]

    # Разговор состоялся, если наставник сказал хоть слово. Сессии без его реплик
    # разведены на «молчание модели» (ход был) и «пустой заход» (хода не было).
    talked = {
        (s["student_id"], s["task_id"]): s["last_tutor_at"]
        for s in sessions if s["tutor_msgs"]
    }
    silent = [s for s in sessions if not s["tutor_msgs"] and s["turns"]]
    empty = [s for s in sessions if not s["turns"]]

    # Повод считается закрытым, только если наставник говорил ПОСЛЕ неверной сдачи.
    # Разговор, целиком уложившийся до неё, поводом не вызван и его не закрыл:
    # ученик спросил заранее, а споткнулся всё равно. Без этой проверки такой
    # случай засчитался бы в охват и завысил бы его ровно там, где наставник не
    # сработал.
    covered = {
        (r["user_id"], r["task_id"])
        for r in gated
        if (r["user_id"], r["task_id"]) in talked
        and talked[(r["user_id"], r["task_id"])] > r["first_wrong_at"]
    }
    reason_keys = {(r["user_id"], r["task_id"]) for r in gated}
    talked_without_reason = set(talked) - reason_keys

    # tsk-779: главный охват считается по поводам, где ученик РЕАЛЬНО застрял.
    #
    # Замер 03.09 на боевых данных: из 254 поводов 182 (72%) — ученик ошибся один
    # раз и тут же сам сдал верно. Наставник там не нужен и звать его никто не
    # будет, а в знаменателе он топил долю втрое. Из-за этого чек каждую неделю
    # показывал провал (6% при пороге 20%) там, где цель уже достигнута: среди
    # застрявших охват 15 из 72, то есть 21%. Доля «промахнулся один раз» держится
    # около 70% из недели в неделю — это устойчивое свойство, а не случайность
    # окна, поэтому знаменатель и разведён.
    #
    # Общая доля не убрана: она остаётся справочной строкой, чтобы смена
    # знаменателя не выглядела подгонкой под порог и чтобы был виден обе картины.
    struggled, one_off, covered_struggled = split_by_struggle(gated, covered)

    print(f"\nПОВОДЫ (пары «ученик + задание» с неверной сдачей): {len(gated)}")
    if ungated:
        print(f"  ещё {len(ungated)} — у учеников без права на наставника (тариф), в долю не идут")
    print(
        f"  из них ученик застрял (2+ неверных попытки): {len(struggled)}; "
        f"промахнулся один раз: {len(one_off)}"
    )
    print(
        f"ЗАСТРЯЛ И ПОЗВАЛ НАСТАВНИКА: {len(covered_struggled)} из {len(struggled)}"
        f"  →  охват {_pct(len(covered_struggled), len(struggled))}"
    )
    print(
        f"  справочно, по всем поводам: {len(covered)} из {len(gated)} "
        f"→ {_pct(len(covered), len(gated))} (сюда входят и те, кто справился сам)"
    )
    if talked_without_reason:
        print(f"  плюс {len(talked_without_reason)} разговоров без неверной сдачи (спросил заранее)")

    # --- числитель ---------------------------------------------------------
    solved = [sid for sid, o in outcomes.items() if o["solved"]]
    suspect = [sid for sid, o in outcomes.items() if o.get("suspect")]
    print(f"\nСОСТОЯЛОСЬ РАЗГОВОРОВ (наставник сказал хоть слово): {len(outcomes)}")
    print(f"  после них ученик сдал верно: {len(solved)}  →  исход {_pct(len(solved), len(outcomes))}")
    if 0 < len(outcomes) < MIN_SAMPLE:
        # Доля от двух-трёх разговоров скачет на десятки процентов от одной сдачи.
        # Без этой оговорки «исход 100%» прочитается как заслуга наставника.
        print(
            f"  ВНИМАНИЕ: разговоров всего {len(outcomes)} — это описание отдельных "
            "случаев, а не вывод о наставнике"
        )
    if suspect:
        print(
            f"  ИЗ НИХ ПОД ВОПРОСОМ: {len(suspect)} — верный ответ есть в репликах "
            f"наставника либо сдача пришла быстрее {COPY_SUSPECT_SECONDS} с без "
            f"единого слова ученика (сессии: "
            + ", ".join(str(s) for s in suspect) + ")"
        )
        print("  Признак грубый: зачёт засчитан, но такие разговоры стоит прочитать глазами.")
    elif not quiet:
        print("  под вопросом: 0")

    # --- почему не вышло ---------------------------------------------------
    # Ход ученика был, реплики наставника за ним не появилось — вот честная
    # единица молчания. Считается по сессии: `turns` растёт до вызова модели,
    # строка `tutor` появляется только при непустом ответе, поэтому их разность
    # и есть число оставшихся без ответа ходов.
    silent_turns = sum(max(0, int(s["turns"]) - int(s["tutor_msgs"])) for s in sessions)
    total_turns = sum(int(s["turns"]) for s in sessions)
    bad_calls = sum(int(c["n"]) for c in calls if c["outcome"] != "ok")
    total_calls = sum(int(c["n"]) for c in calls)
    if silent_turns or silent:
        print(
            f"\nМОЛЧАНИЕ МОДЕЛИ (не «не помогло», а не ответил): "
            f"без ответа осталось ходов: {silent_turns} из {total_turns} "
            f"({_pct(silent_turns, total_turns)}); "
            f"разговоров без единой реплики наставника: {len(silent)}"
        )
    elif not quiet:
        print("\nМОЛЧАНИЕ МОДЕЛИ: 0")

    if calls and not quiet:
        # Фон по моделям: попытки, а не обращения (см. комментарий к SQL).
        print(
            f"  фон попыток моделей: {total_calls}, неудачных {bad_calls} "
            f"({_pct(bad_calls, total_calls)}) — одно обращение ученика может "
            "перебрать несколько моделей и всё равно кончиться ответом"
        )
        for c in calls:
            if c["outcome"] != "ok":
                print(f"    {c['outcome']}: {c['n']}")

    if empty:
        print(
            f"\nПУСТЫЕ ЗАХОДЫ (сессия есть, ходов ноль): {len(empty)} — "
            "открытие задания заводит разговор само, это не поведение ученика"
        )

    # --- вердикт -----------------------------------------------------------
    findings: list[str] = []
    # tsk-779: тревога — по застрявшим (см. пояснение у расчёта охвата выше).
    if len(struggled) < MIN_SAMPLE:
        print(
            f"\nДАННЫХ МАЛО ДЛЯ ВЫВОДА: поводов с застреванием {len(struggled)} "
            f"при пороге {MIN_SAMPLE}. Доли выше приведены как есть, но выводом о "
            "работе наставника они не являются."
        )
    elif len(covered_struggled) / len(struggled) < COVERAGE_ALARM:
        findings.append(
            f"охват среди застрявших {_pct(len(covered_struggled), len(struggled))} — "
            f"ниже порога {COVERAGE_ALARM * 100:.0f}%: поводов с застреванием "
            f"{len(struggled)}, разговоров {len(covered_struggled)}"
        )

    if total_turns >= MIN_TURNS_SAMPLE and silent_turns / total_turns >= MODEL_FAILURE_ALARM:
        findings.append(
            f"наставник промолчал на {_pct(silent_turns, total_turns)} ходов "
            f"({silent_turns} из {total_turns}) — смотреть цепочки моделей, tsk-678"
        )

    if findings:
        print("\nНАХОДКИ:")
        for f in findings:
            print(f"  - {f}")
        return 1

    print("\nOK: тревоги нет.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Исходы разговоров с ИИ-наставником (tsk-661)")
    ap.add_argument("--quiet", action="store_true", help="печатать только находки")
    ap.add_argument("--days", type=int, default=7, help="ширина окна в днях (по умолчанию 7)")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(quiet=args.quiet, days=args.days)))
    except Exception as exc:  # noqa: BLE001 — чек под планировщиком, причина обязана попасть в лог
        print(f"ОШИБКА выполнения чека: {exc}", file=sys.stderr)
        sys.exit(2)
