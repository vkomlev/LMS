# app/services/llm_chain_check_cron_service.py
"""Периодическая проверка боевых цепочек моделей (tsk-678).

**Зачем.** Каталог провайдера живёт своей жизнью: модели исчезают, начинают
отвечать `503 no_available_provider` и замедляются без предупреждения. 25.08 это
стоило двух разных аварий за сутки. У наставника цепочка целиком состояла из
моделей, которых у провайдера уже не было ([[tsk-671]]). У судьи из трёх
запасных работал ровно один: `claude-haiku-4.5` отдавал JSON без балла чистоты
3 раза из 3, `deepseek-v4-flash-0731` был мёртв (tsk-678). Оба раза мы узнали об
этом от человека, который полез разбираться по другому поводу, — то есть не
узнали бы вовсе.

Стенд `scripts/llm_model_bakeoff.py` называется периодическим с самого начала,
но запускать его было некому. Этот проход и есть недостающий запускающий.

**Что делает проход:**

* по каждой модели судейской цепочки — НАСТОЯЩИЙ разбор боевым промптом на ДВУХ
  разных образцах работ: держит ли модель формат (ответ обязан разобраться нашим
  же разборщиком) и укладывается ли в бюджет попытки. «Жива» и «годна судить» —
  разное, ровно на этом и попался haiku: отвечал быстро и бодро, а балла в ответе
  не было. Образцов два, потому что sonnet-4.6 прошёл гейт 3 раза из 3 на одном
  и вернул другую схему целиком на другом (см. `PROBE_WORKS`);
* по каждой модели наставницкой цепочки — стриминговый вызов по ЕЁ мерке:
  пришёл ли первый кусок за 12 c. Качество
  наставника проверяется гейтом на слив эталона, а это три полных прогона на
  модель — дорого для еженедельника и требует решения оператора. Здесь только
  доступность: она ловит ровно ту аварию, что случилась 25.08;
* отказавшая модель уходит в КОНЕЦ очереди до следующего прохода — тем же
  механизмом остывания, которым это делает рантайм после живого отказа.

**Чего проход НЕ делает: не меняет СОСТАВ цепочки.** Состав утверждён стендом по
гейту на слив эталона и по качеству разбора; подставлять туда модель мимо стенда
нельзя — «живая» и «быстрая» ещё не значит «годная» (25.08: `claude-opus-4.8` и
`claude-haiku-4.5` отвечали быстро и слили эталон 3 раза из 3). Проход меняет
только ПОРЯДОК и оставляет след, по которому человек решает, пора ли гонять
полный стенд и менять состав.

**Куда смотреть.** Отдельного экрана нет намеренно: вызовы прохода пишутся в тот
же учёт расхода `llm_usage_event` с назначением `chain_check` — а это ровно тот
пульт, на котором замедление судьи и заметили. Мёртвая модель будет видна там
еженедельной строкой с ошибкой, а не тишиной. Плюс строка в логе с меткой
`LLM_CHAIN_ALERT`, когда сломана ПЕРВАЯ модель или годных судей осталось меньше
двух: это уже не «само рассосётся», это повод запустить стенд.

**Замок между воркерами не берём** — сознательно, в отличие от соседних кронов.
Остывание живёт В ПАМЯТИ ПРОЦЕССА (см. `app/services/llm/cooldown.py`), поэтому
проход, выполненный одним воркером за всех, оставил бы остальных с неверным
порядком очереди. Пусть каждый процесс проверяет свои цепочки сам: это ровно та
же «независимость у каждого», что уже принята для остывания, а цена вопроса —
восемь вызовов в неделю на процесс, порядка полуцента.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.services.llm import Budget, LLMError, LLMMessage, complete, cooldown, providers, stream
from app.services.llm.contracts import LLMCooldown, LLMTimeout

# Разбор ответа берём ТОТ ЖЕ, что работает на бою. Своя копия проверки формата
# означала бы, что проход одобряет модель, которую сервис потом не примет —
# именно так haiku и простоял вторым в цепочке, считаясь запасом.
from app.services.code_review_service import (  # noqa: WPS450 — намеренно боевые внутренности
    _SYSTEM_PROMPT,
    _build_user_message,
    _parse_verdict,
)

logger = logging.getLogger("app.llm_chain_check")

_scheduler: Optional[AsyncIOScheduler] = None

CHECK_PURPOSE = "chain_check"

# ОБРАЗЦЫ работ для судейской проверки — их ДВА, и это не запас, а урок.
#
# 25.08 `claude-sonnet-4.6` прошёл гейт формата 3 раза из 3 на одном образце и
# был поставлен вторым в боевую цепочку. На другом образце он вернул не просто
# другой формат, а ДРУГУЮ СХЕМУ ЦЕЛИКОМ — русскими ключами
# («правильность», «оценка», «замечания») вместо нашей. То же и у
# `openai/gpt-5.4-mini`: 3/3 на первом образце, 2/3 на втором.
#
# Значит, три прогона на ОДНОМ примере не доказывают дисциплину формата: они
# доказывают её для этого примера. Годной считается модель, разобравшаяся на
# ОБОИХ — они намеренно разной формы: рисование в цикле с побочным эффектом и
# счётный цикл с вводом.
#
# Образцы синтетические: настоящая сдача — данные ученика, и брать их для
# еженедельного пинга незачем. Размер близок к типичной короткой сдаче; на
# длительность размер входа влияет слабо (замер 25.08: 999 токенов дали 4,2 c,
# 5767 токенов — 5,4 c).
PROBE_WORKS: tuple[tuple[str, str], ...] = (
    (
        "Черепашка должна нарисовать домик: квадрат со стороной 100 и "
        "треугольную крышу над ним. Используй цикл для стен и отдельные команды "
        "для крыши. Цвет стен — синий, цвет крыши — красный.",
        """import turtle

t = turtle.Turtle()
t.speed(3)

t.color("blue")
for i in range(4):
    t.forward(100)
    t.left(90)

t.color("red")
t.forward(100)
t.left(60)
t.forward(100)
t.left(120)
t.forward(100)

# risuem okno
t.penup()
t.goto(30, 30)
t.pendown()
t.color("blue")
for i in range(4):
    t.forward(40)
    t.left(90)

turtle.done()
""",
    ),
    (
        "Составь программу: пользователь вводит числа, пока не введёт 0. "
        "Выведи сумму введённых чисел и их количество.",
        """summa = 0
kol = 0

while True:
    n = int(input("vvedi chislo: "))
    if n == 0:
        break
    summa = summa + n
    kol = kol + 1

print("summa =", summa)
print("kolichestvo =", kol)
""",
    ),
)

# Совместимость для стенда и прежних вызовов: первый образец под старыми именами.
PROBE_STEM, PROBE_CODE = PROBE_WORKS[0]


async def probe_judge_model(model: str, work: tuple[str, str]) -> tuple[bool, str, str]:
    """Один настоящий разбор конкретной моделью: годна ли она судить.

    Возвращает «годна», причину и КЛАСС отказа. Класс нужен потому, что отказы
    здесь разного веса:

    * `error` и `format` — твёрдые. Модель либо не ответила, либо ответила не по
      нашей схеме; повторять незачем.
    * `slow` — шаткий. Время у провайдера пляшет втрое за пять минут (замер
      25.08: `gemini-3.7-flash` — 24,6 c в проходе и 5,4 c спустя пять минут).
      Задвигать голову цепочки по одному такому замеру значит гонять разборы к
      худшей модели из-за шума, а это деньги.

    Годна — это три условия сразу, и каждое уже подводило нас по отдельности:
    ответ пришёл, ответ разобрался нашим разборщиком до балла чистоты, и модель
    уложилась в бюджет ОДНОЙ попытки (на бою у неё сверху ещё очередь работ).
    """
    stem, code = work
    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=_build_user_message(code, task_stem=stem)),
    ]
    started = time.monotonic()
    try:
        result = await complete(
            messages,
            model=model,
            purpose=CHECK_PURPOSE,
            budget=Budget.BATCH,
            response_format={"type": "json_object"},
        )
    except LLMCooldown:
        # Остывание — это про ПРОВАЙДЕРА целиком после 429, а не про модель.
        # Записать её в негодные значило бы задвинуть всю цепочку и поднять
        # тревогу на ровном месте. Пусть проход отложится до следующего раза.
        raise
    except LLMTimeout as exc:
        # Таймаут — та же медленность, только крайняя её степень, и мерить её
        # одним замером так же неверно. `gpt-5.4` дал 10,1 c медианы на стенде и
        # упёрся в потолок двадцатью минутами позже: приговор по такому замеру —
        # это приговор вечеру у провайдера, а не модели.
        return False, f"{type(exc).__name__}: {exc}"[:200], "slow"
    except LLMError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200], "error"

    took = time.monotonic() - started
    try:
        verdict = _parse_verdict(result.text)
    except Exception as exc:  # noqa: BLE001 — любой кривой ответ здесь равнозначен
        return False, f"ответ не разобрался: {type(exc).__name__}: {exc}"[:200], "format"
    if verdict["code_quality"]["score"] is None:
        return False, "разобралось, но балла чистоты в ответе нет", "format"
    if took > Budget.BATCH.attempt_timeout:
        return False, f"{took:.1f} c — не уложилась в бюджет попытки", "slow"
    return True, f"{took:.1f} c, балл получен", "ok"


async def probe_tutor_model(model: str) -> tuple[bool, str, str]:
    """Жива ли модель наставника — по МЕРКЕ НАСТАВНИКА, а не батча.

    Мерка здесь не формальность, а суть. Наставник стримит: ученик видит текст по
    мере генерации, и «отвечает ли модель» для него значит «пришёл ли ПЕРВЫЙ
    кусок за 12 c» (`Budget.INTERACTIVE`). Полное время ответа ему безразлично.

    Первый же боевой проход это доказал: `claude-sonnet-4.6` отдал слово «Готов»
    целиком за 22 c и был объявлен недоступным — батч-вызовом, с потолком батча.
    Для ученика он при этом мог быть совершенно жив. Сторож, который так врёт,
    хуже отсутствующего: на его тревоги перестают смотреть.
    """
    started = time.monotonic()
    first_at: float | None = None
    try:
        async for chunk in stream(
            [LLMMessage(role="user", content="Ответь одним словом: готов")],
            model=model,
            purpose=CHECK_PURPOSE,
            budget=Budget.INTERACTIVE,
            max_tokens=32,
        ):
            if chunk.delta and first_at is None:
                first_at = time.monotonic() - started
            # Поток дочитываем ДО КОНЦА, хотя ответ нам уже не нужен. Выйти на
            # первом куске значит закрыть генератор до того, как он запишет
            # расход: проба стала бы невидимой на пульте — ровно там, где она и
            # задумана как видимая. Ответ в 32 токена этого не стоит.
    except LLMCooldown:
        raise  # см. `probe_judge_model`: это про провайдера, а не про модель
    except LLMTimeout as exc:
        # Класс отказа, а не текст ошибки: разбирать сообщение подстрокой —
        # значит завязаться на формулировку, которая переживёт максимум одну правку.
        return False, f"{type(exc).__name__}: {exc}"[:200], "slow"
    except LLMError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200], "error"
    if first_at is None:
        return False, "поток закончился, не отдав ни куска", "error"
    return True, f"первый кусок за {first_at:.1f} c", "ok"


def _demote(model: str, seconds: float, reason: str) -> None:
    """Задвинуть модель в конец очереди до следующего прохода.

    Не выбрасываем и не правим состав: задвигаем. Если задвинуты ВСЕ, порядок
    просто сохранится и мы попробуем их снова — остаться без разбора хуже, чем
    сходить к сомнительной модели (то же решение, что в рантайме).
    """
    cooldown.start("model:" + model, seconds)
    logger.warning("LLM_CHAIN_ALERT: %s задвинута в конец очереди — %s", model, reason)


async def _tutor_verdict(model: str) -> tuple[bool, str, str]:
    """Жив ли наставник, с подтверждением одиночного таймаута вторым замером.

    Правило то же, что у судьи: не отдать первый кусок за 12 c модель может и
    из-за плохой минуты у провайдера. Тревога «первая модель наставника
    недоступна» по одному такому замеру — это ложная тревога, а на ложные
    перестают смотреть.
    """
    good, why, kind = await probe_tutor_model(model)
    if not good and kind == "slow":
        logger.info("chain_check: наставник %s молчал (%s), перемеряю", model, why)
        good, why, kind = await probe_tutor_model(model)
    return good, why, kind


async def _judge_all_works(model: str) -> tuple[bool, str, str]:
    """Годна ли модель судить — по ВСЕМ образцам, а не по одному.

    Достаточно провалиться на одном: дисциплина формата, доказанная на единственном
    примере, — это дисциплина для этого примера (см. `PROBE_WORKS`).

    Медленность подтверждаем вторым замером. Один медленный ответ у этого
    провайдера ничего не доказывает — 25.08 время плясало втрое за пять минут, —
    а задвинутая по шуму голова уводит все разборы к худшей модели, и это деньги.
    """
    for index, work in enumerate(PROBE_WORKS, 1):
        good, why, kind = await probe_judge_model(model, work)
        if not good and kind == "slow":
            logger.info("chain_check: судья %s медлил (%s), перемеряю", model, why)
            good, why, kind = await probe_judge_model(model, work)
        if not good:
            return False, f"образец {index} из {len(PROBE_WORKS)}: {why}", kind
    return True, f"все образцы разобраны ({len(PROBE_WORKS)})", "ok"


async def llm_chain_check_tick() -> dict:
    """Один проход по обеим цепочкам. Возвращает сводку для логов и тестов.

    Исключение наружу не выпускаем: упавший проход не должен ронять планировщик
    и вместе с ним остальные фоновые задачи. Но и молчать нельзя — иначе отказ
    самой проверки неотличим от «всё в порядке», а это ровно та тишина, ради
    которой проход и заведён.
    """
    settings = Settings()
    demote_for = float(int(getattr(settings, "llm_chain_check_interval_hours", 168)) * 3600)
    summary: dict = {"judge_ok": 0, "judge_bad": 0, "tutor_ok": 0, "tutor_bad": 0, "alerts": []}

    try:
        judge_chain = providers.judge_models()
        for position, model in enumerate(judge_chain, 1):
            good, why, kind = await _judge_all_works(model)
            if good:
                summary["judge_ok"] += 1
                logger.info("chain_check: судья %s (%d) годен — %s", model, position, why)
                continue
            summary["judge_bad"] += 1
            _demote(model, demote_for, f"судья не годен ({kind}): {why}")
            if position == 1:
                summary["alerts"].append(f"первая модель судьи не годна: {model} — {why}")

        for position, model in enumerate(providers.tutor_models(), 1):
            good, why, _ = await _tutor_verdict(model)
            if good:
                summary["tutor_ok"] += 1
                continue
            summary["tutor_bad"] += 1
            _demote(model, demote_for, f"наставник недоступен: {why}")
            if position == 1:
                summary["alerts"].append(f"первая модель наставника недоступна: {model} — {why}")

        # Один годный судья — это не запас, это последний рубеж. Мёртвая
        # четвёрка 25.08 начиналась ровно с такого состояния, просто никто не
        # смотрел.
        if summary["judge_ok"] < 2:
            summary["alerts"].append(
                f"годных судей осталось {summary['judge_ok']} из {len(judge_chain)} — "
                "запаса нет, пора гонять полный стенд и менять состав"
            )

        for alert in summary["alerts"]:
            logger.error("LLM_CHAIN_ALERT: %s", alert)
        logger.info("chain_check: проход завершён — %s", summary)
    except LLMCooldown as exc:
        # Провайдер остывает после 429 — сейчас не годна ни одна модель, и это
        # ничего не говорит о самих моделях. Долбить его проверкой нельзя: повтор
        # кормит именно тот брейкер, который уже срабатывал (2026-07-05).
        summary["skipped"] = True
        logger.info("chain_check: проход отложен — %s", exc)
    except Exception:
        logger.exception("chain_check: проход упал — состояние цепочек за этот прогон неизвестно")

    return summary


def start_scheduler() -> Optional[AsyncIOScheduler]:
    """Поднять периодический проход, если включён настройкой."""
    global _scheduler
    settings = Settings()
    if not getattr(settings, "llm_chain_check_enabled", True):
        logger.info("chain_check: проверка цепочек выключена (LLM_CHAIN_CHECK_ENABLED)")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    hours = int(getattr(settings, "llm_chain_check_interval_hours", 168))
    delay_min = int(getattr(settings, "llm_chain_check_startup_delay_min", 5))
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        llm_chain_check_tick,
        trigger=IntervalTrigger(hours=hours),
        id="llm_chain_check_tick",
        # Первый проход — вскоре после запуска, а не через неделю. Причины две:
        # порядок очереди живёт в памяти процесса и после перезапуска забывается,
        # и свежий выкат не должен неделю ждать, чтобы узнать про мёртвую модель.
        # Не в сам момент старта: приложению есть чем заняться в первые секунды.
        next_run_time=_startup_run_at(delay_min),
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "chain_check: проверка цепочек запущена, интервал %s ч, первый проход через %s мин",
        hours, delay_min,
    )
    return scheduler


def _startup_run_at(delay_min: int):
    """Момент первого прохода. Вынесено функцией, чтобы тест не ждал минутами."""
    from datetime import datetime, timedelta, timezone as _tz

    return datetime.now(_tz.utc) + timedelta(minutes=max(0, delay_min))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
