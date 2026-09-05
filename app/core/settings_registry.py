# app/core/settings_registry.py
"""Реестр настроек школы, которые правит администратор в кабинете (tsk-721).

Что здесь лежит и чего здесь нет
--------------------------------
Здесь — ПРАВИЛА РАБОТЫ ШКОЛЫ: пороги, окна, интервалы, лимиты и тексты для
людей. То, что администратор решает сам и меняет без похода на сервер.

Здесь НЕТ и не должно появиться:

* **секретов** — ключи, токены ботов, пароли, строки подключения. Правило
  «секреты только в переменных окружения» не обсуждается: в кабинете их не
  должно быть даже в виде звёздочек с кнопкой «изменить». Появление такого
  ключа в этом файле — дефект, а не расширение возможностей;
* **настроек развёртывания** — адреса служб, пути загрузок, домен cookie,
  таймауты хранилища. Они меняются вместе с сервером, а не по решению школы;
* **инженерных констант** — размеры кусков при загрузке, ключи блокировок,
  пределы разбора текста. У них нет смысла для человека, а изменение ломает
  поведение.

Почему реестр в коде, а значения в базе
---------------------------------------
Описание настройки (как называется, на что влияет, какие границы) — часть
кода: оно меняется вместе с тем кодом, который настройку применяет. В базе
лежит только то, что администратор выбрал сам. Пока он ничего не выбрал,
значение берётся из переменной окружения, а если и её нет — из умолчания
здесь же. Порядок разбирается в `app/core/settings_store.py`.

Границы обязательны у каждого числа. Порог, который можно выставить в ноль
или в миллион, однажды выставят: `0` в дне блокировки за неоплату отрезает
школу от занятий в тот же день, а `1` в доле ошибок делает сигнал вечно
молчащим. Проверка границ живёт на сервере, а не только в форме кабинета.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Tuple

SettingKind = Literal["int", "float", "bool", "str"]


@dataclass(frozen=True)
class SettingDef:
    """Описание одной настройки: как называется, что значит, что допустимо."""

    key: str
    group: str
    title: str
    """Русское название целиком, без сокращений: человек читает только его."""

    description: str
    """Одна строка «на что влияет» — что изменится в работе школы."""

    kind: SettingKind
    default: Any
    """Умолчание в коде — последний рубеж, если нет ни базы, ни окружения."""

    env_var: str | None = None
    """Переменная окружения, из которой значение бралось до кабинета.

    `None` — настройка была константой в коде и переменной окружения не имела.
    """

    unit: str = ""
    """Единица измерения по-русски: «дни», «минуты», «штук», «доля»."""

    min_value: float | None = None
    max_value: float | None = None
    max_length: int | None = None
    warning: str | None = None
    """Предупреждение рядом с полем: что перестанет работать при выключении."""


# Группы в том порядке, в каком их видит администратор.
GROUP_MONEY = "Деньги и оплата"
GROUP_LESSONS = "Занятия"
GROUP_SIGNALS = "Сигналы о рисках"
GROUP_SENSORS = "Фоновые датчики"

GROUP_ORDER: Tuple[str, ...] = (
    GROUP_MONEY,
    GROUP_LESSONS,
    GROUP_SIGNALS,
    GROUP_SENSORS,
)


SETTINGS: Tuple[SettingDef, ...] = (
    # ---------------------------------------------------------------- деньги
    SettingDef(
        key="payment_block_after_days",
        group=GROUP_MONEY,
        title="Через сколько дней после конца месяца закрывать занятия за неоплату",
        description=(
            "Месяц оплачивается до своего конца. Отсчёт идёт с 1-го числа "
            "следующего месяца: при 5 занятия закрываются 6-го."
        ),
        kind="int",
        default=5,
        env_var="PAYMENT_BLOCK_AFTER_DAYS",
        unit="дни",
        min_value=1,
        max_value=60,
    ),
    SettingDef(
        key="payment_due_soon_days",
        group=GROUP_MONEY,
        title="За сколько последних дней месяца напоминать об оплате в кабинете",
        description=(
            "Плашка «период заканчивается» появляется в кабинете у того, кто "
            "ещё не оплатил: при 4 она видна с 28 августа по 31-е. После конца "
            "месяца не гаснет — держится, пока долг не закрыт."
        ),
        kind="int",
        default=4,
        env_var="PAYMENT_DUE_SOON_DAYS",
        unit="дни",
        min_value=1,
        max_value=15,
    ),
    SettingDef(
        key="first_month_charge_cutoff_day",
        group=GROUP_MONEY,
        title="До какого числа первая покупка оплачивает текущий месяц",
        description=(
            "Купил позже этого числа — первое начисление ставится за следующий "
            "месяц, остаток текущего даётся бесплатно."
        ),
        kind="int",
        default=20,
        env_var="FIRST_MONTH_CHARGE_CUTOFF_DAY",
        unit="число месяца",
        min_value=1,
        max_value=28,
    ),
    SettingDef(
        key="ai_package_price_minor",
        group=GROUP_MONEY,
        title="Цена пакета обращений к ИИ-наставнику",
        description=(
            "В копейках: 50000 — это 500 ₽. Столько платит ученик, когда "
            "включённые в тариф обращения закончились."
        ),
        kind="int",
        default=50000,
        env_var="AI_PACKAGE_PRICE_MINOR",
        unit="копейки",
        min_value=1000,
        max_value=2_000_000,
    ),
    SettingDef(
        key="ai_package_units",
        group=GROUP_MONEY,
        title="Сколько обращений к наставнику в одном пакете",
        description="Что ученик получает за цену выше.",
        kind="int",
        default=40,
        env_var="AI_PACKAGE_UNITS",
        unit="обращения",
        min_value=1,
        max_value=1000,
    ),
    SettingDef(
        key="payment_transfer_details",
        group=GROUP_MONEY,
        title="Реквизиты для перевода",
        description=(
            "Текст показывается ученику в кабинете над кнопкой оплаты. "
            "Пусто — блок с реквизитами не выводится вовсе."
        ),
        kind="str",
        default="",
        env_var="PAYMENT_TRANSFER_DETAILS",
        unit="текст",
        max_length=2000,
    ),
    # --------------------------------------------------------------- занятия
    SettingDef(
        key="lesson_idle_threshold_minutes",
        group=GROUP_LESSONS,
        title="Через сколько минут молчания на занятии звать преподавателя",
        description=(
            "Ученик на занятии ничего не делает дольше этого срока — "
            "преподаватель получает сигнал."
        ),
        kind="int",
        default=10,
        env_var="LESSON_IDLE_THRESHOLD_MINUTES",
        unit="минуты",
        min_value=3,
        max_value=60,
    ),
    SettingDef(
        key="lesson_no_show_threshold_minutes",
        group=GROUP_LESSONS,
        title="Через сколько минут после начала считать, что ученик не пришёл",
        description=(
            "Не подключился и не отмечен вручную за этот срок — занятию "
            "проставляется «не пришёл»."
        ),
        kind="int",
        default=10,
        env_var="LESSON_NO_SHOW_THRESHOLD_MINUTES",
        unit="минуты",
        min_value=5,
        max_value=180,
    ),
    SettingDef(
        key="lesson_reminder_lead_minutes",
        group=GROUP_LESSONS,
        title="За сколько минут до занятия напоминать ученику",
        description="Одно напоминание на занятие, повторов нет.",
        kind="int",
        default=30,
        env_var="LESSON_REMINDER_LEAD_MINUTES",
        unit="минуты",
        min_value=5,
        max_value=240,
    ),
    SettingDef(
        key="homework_program_ege_courses",
        group=GROUP_LESSONS,
        title="Курсы программы подготовки к ЕГЭ",
        description=(
            "Через запятую номера корневых курсов, которые ученик должен "
            "закончить к сроку ниже. По остатку ОБЯЗАТЕЛЬНЫХ элементов этих "
            "курсов считается персональная норма домашней работы. Пусто — "
            "норма берётся общая по классу, как было раньше."
        ),
        kind="str",
        default="88,112",
        env_var=None,
        unit="номера курсов",
        max_length=200,
    ),
    SettingDef(
        key="homework_program_oge_courses",
        group=GROUP_LESSONS,
        title="Курсы программы подготовки к ОГЭ",
        description=(
            "То же для девятиклассников. Ученик считается ОГЭшником, если "
            "записан на любой курс из этого списка."
        ),
        kind="str",
        # 1080 «ОГЭ по информатике» + 1454 «Python для ОГЭ» — второй собран из
        # тех же подкурсов, что и «Python для ЕГЭ», но до циклов включительно
        # (без функций, списков, словарей, множеств и рекурсии).
        default="1080,1454",
        env_var=None,
        unit="номера курсов",
        max_length=200,
    ),
    SettingDef(
        key="homework_program_planned_pace",
        group=GROUP_LESSONS,
        title="На какой недельный темп рассчитывать объём программы",
        description=(
            "Сколько элементов в неделю школа ожидает от ученика при "
            "планировании. Из этого числа и срока считается, сколько "
            "тренажёра ему выдать. У того, кто делает больше, план строится "
            "по его собственному темпу — настройка его не ограничивает."
        ),
        kind="int",
        default=25,
        env_var=None,
        unit="элементов в неделю",
        min_value=5,
        max_value=100,
    ),
    SettingDef(
        key="homework_program_ege_deadline",
        group=GROUP_LESSONS,
        title="К какому числу закончить программу ЕГЭ",
        description=(
            "День и месяц в формате ММ-ДД. Год берётся по классу ученика: "
            "одиннадцатикласснику ближайший, десятикласснику — следующий. "
            "После этой даты время уходит на отработку вариантов."
        ),
        kind="str",
        default="03-31",
        env_var=None,
        unit="ММ-ДД",
        max_length=5,
    ),
    SettingDef(
        key="homework_program_oge_deadline",
        group=GROUP_LESSONS,
        title="К какому числу закончить программу ОГЭ",
        description="День и месяц в формате ММ-ДД, год — по классу ученика.",
        kind="str",
        default="04-30",
        env_var=None,
        unit="ММ-ДД",
        max_length=5,
    ),
    SettingDef(
        key="lesson_summary_after_start_minutes",
        group=GROUP_LESSONS,
        title="Сколько минут после начала занятия держать сводку по ученикам",
        description=(
            "В начале урока преподаватель смотрит, кто что сделал дома. Раньше "
            "кнопка превращалась в «Подвести итоги» ровно в час начала — "
            "сводку было уже не открыть."
        ),
        kind="int",
        default=15,
        env_var="LESSON_SUMMARY_AFTER_START_MINUTES",
        unit="минуты",
        min_value=0,
        max_value=120,
    ),
    SettingDef(
        key="lesson_wrapup_before_end_minutes",
        group=GROUP_LESSONS,
        title="За сколько минут до конца занятия предлагать подвести итоги",
        description=(
            "Итоги подводят в конце урока, а не в начале. До этого срока "
            "кнопка остаётся сводкой по ученикам."
        ),
        kind="int",
        default=15,
        env_var="LESSON_WRAPUP_BEFORE_END_MINUTES",
        unit="минуты",
        min_value=0,
        max_value=120,
    ),
    SettingDef(
        key="lesson_auto_confirm_early_grace_minutes",
        group=GROUP_LESSONS,
        title="Насколько раньше начала засчитывать приход",
        description=(
            "Ученик сел за работу до звонка — явка всё равно отмечается, "
            "если он опередил начало не больше чем на этот срок."
        ),
        kind="int",
        default=15,
        env_var="LESSON_AUTO_CONFIRM_EARLY_GRACE_MINUTES",
        unit="минуты",
        min_value=0,
        max_value=60,
    ),
    SettingDef(
        key="lesson_occurrence_horizon_days",
        group=GROUP_LESSONS,
        title="На сколько дней вперёд создавать занятия по расписанию",
        description=(
            "Скользящее окно: занятия из постоянного расписания появляются в "
            "календаре на столько дней вперёд."
        ),
        kind="int",
        default=14,
        env_var="LESSON_OCCURRENCE_HORIZON_DAYS",
        unit="дни",
        min_value=3,
        max_value=90,
    ),
    SettingDef(
        key="lesson_reschedule_horizon_days",
        group=GROUP_LESSONS,
        title="На сколько дней вперёд можно перенести занятие",
        description="Дальше этого срока перенос не предлагается и не принимается.",
        kind="int",
        default=14,
        env_var=None,
        unit="дни",
        min_value=1,
        max_value=90,
    ),
    # --------------------------------------------------------------- сигналы
    SettingDef(
        key="dropout_risk_window_days",
        group=GROUP_SIGNALS,
        title="Сколько дней тишины считать признаком, что ученик затих",
        description=(
            "Не ходит на занятия и не сдаёт свои работы столько дней — "
            "преподаватель видит сигнал о риске ухода. Меньше срок — раньше "
            "узнаём, но чаще ошибаемся."
        ),
        kind="int",
        default=14,
        env_var="DROPOUT_RISK_WINDOW_DAYS",
        unit="дни",
        min_value=5,
        max_value=60,
    ),
    SettingDef(
        key="gap_student_min_submissions",
        group=GROUP_SIGNALS,
        title="Сколько сдач нужно, чтобы судить о трудностях ученика",
        description=(
            "На меньшем числе работ сигнал не поднимается: две неудачи подряд "
            "ещё ничего не значат."
        ),
        kind="int",
        default=8,
        env_var=None,
        unit="сдачи",
        min_value=3,
        max_value=50,
    ),
    SettingDef(
        key="gap_student_error_rate",
        group=GROUP_SIGNALS,
        title="Доля ошибок, с которой ученик попадает в сигнал",
        description="0.5 — ошибается в половине работ и чаще.",
        kind="float",
        default=0.5,
        env_var=None,
        unit="доля от 0 до 1",
        min_value=0.1,
        max_value=0.9,
    ),
    SettingDef(
        key="ai_signal_min_flagged_works",
        group=GROUP_SIGNALS,
        title="Сколько подозрительных работ нужно для сигнала об ИИ",
        description=(
            "Признак несамостоятельной работы поднимается, только когда таких "
            "работ набралось не меньше этого числа."
        ),
        kind="int",
        default=3,
        env_var=None,
        unit="работы",
        min_value=1,
        max_value=20,
    ),
    SettingDef(
        key="ai_signal_min_flagged_share",
        group=GROUP_SIGNALS,
        title="Какая доля работ ученика должна быть подозрительной",
        description="Второе условие сигнала об ИИ, вместе с числом работ выше.",
        kind="float",
        default=0.5,
        env_var=None,
        unit="доля от 0 до 1",
        min_value=0.1,
        max_value=1.0,
    ),
    SettingDef(
        key="ai_signal_window_days",
        group=GROUP_SIGNALS,
        title="За какой срок смотреть работы при проверке на ИИ",
        description="Работы старше этого срока в подсчёт не берутся.",
        kind="int",
        default=90,
        env_var=None,
        unit="дни",
        min_value=7,
        max_value=365,
    ),
    SettingDef(
        key="gap_task_min_submissions",
        group=GROUP_SIGNALS,
        title="Сколько сдач по заданию нужно, чтобы говорить о пробеле",
        description=(
            "Задание попадает в список проблемных, только когда его решало "
            "достаточно людей."
        ),
        kind="int",
        default=20,
        env_var=None,
        unit="сдачи",
        min_value=5,
        max_value=200,
    ),
    SettingDef(
        key="gap_task_error_rate",
        group=GROUP_SIGNALS,
        title="Доля ошибок, с которой задание считается проблемным",
        description="0.35 — на задании спотыкается примерно каждый третий.",
        kind="float",
        default=0.35,
        env_var=None,
        unit="доля от 0 до 1",
        min_value=0.1,
        max_value=0.9,
    ),
    SettingDef(
        key="gap_task_min_students",
        group=GROUP_SIGNALS,
        title="Сколько разных учеников должны ошибиться на задании",
        description=(
            "Защита от случая, когда все ошибки — это один человек, "
            "переотправлявший ответ."
        ),
        kind="int",
        default=3,
        env_var=None,
        unit="ученики",
        min_value=2,
        max_value=50,
    ),
    # -------------------------------------------------------------- датчики
    SettingDef(
        key="learning_gaps_cron_enabled",
        group=GROUP_SENSORS,
        title="Искать учебные пробелы",
        description="Суточный проход, который собирает трудности учеников и заданий.",
        kind="bool",
        default=True,
        env_var="LEARNING_GAPS_CRON_ENABLED",
        warning=(
            "Выключено — преподаватель и методист перестанут получать новые "
            "сигналы о трудностях. Уже собранные останутся на месте."
        ),
    ),
    SettingDef(
        key="lesson_idle_cron_enabled",
        group=GROUP_SENSORS,
        title="Следить за простоем на занятии",
        description="Проход раз в несколько минут во время идущих занятий.",
        kind="bool",
        default=True,
        env_var="LESSON_IDLE_CRON_ENABLED",
        warning=(
            "Выключено — преподаватель не узнает, что ученик молчит на занятии."
        ),
    ),
    SettingDef(
        key="homework_auto_issue_enabled",
        group=GROUP_LESSONS,
        title="Выдавать домашнюю работу автоматически после занятия",
        description=(
            "Как только преподаватель отметил, что ученик был на занятии, "
            "система сама задаёт ему объём по темпу и классу — со сроком до "
            "следующего занятия. Выключено — преподаватель задаёт вручную "
            "кнопкой в карточке ученика; расчёт объёма работает в обоих "
            "случаях."
        ),
        kind="bool",
        # Выключено по умолчанию намеренно: формула согласована с оператором,
        # но на живых учениках ещё не обкатана, а выдача видна ученику сразу.
        # Включение — один переключатель, без выката (tsk-741).
        default=False,
        env_var="HOMEWORK_AUTO_ISSUE_ENABLED",
        warning=(
            "Включено — ученики начнут получать домашнюю работу без участия "
            "преподавателя, каждый после своего занятия."
        ),
    ),
    SettingDef(
        key="code_review_cron_enabled",
        group=GROUP_SENSORS,
        title="Оценивать код учеников",
        description=(
            "Фоновая оценка чистоты кода и признака ИИ-авторства после сдачи."
        ),
        kind="bool",
        default=True,
        env_var="CODE_REVIEW_CRON_ENABLED",
        warning=(
            "Выключено — приём ответов работает как обычно, но отчёты копятся "
            "неразобранными. Пригодится, если сломался поставщик моделей."
        ),
    ),
    SettingDef(
        key="curator_weekly_report_enabled",
        group=GROUP_SENSORS,
        title="Присылать недельный отчёт по кураторству",
        description=(
            "Раз в неделю, утром понедельника, владелец школы получает сводку: "
            "у кого сколько учеников, скольких куратор за неделю не тронул ни "
            "разу, что просрочено. Отчёт про работу кураторов, а не про "
            "успеваемость учеников."
        ),
        kind="bool",
        # Выключено по умолчанию намеренно (tsk-742): отчёт видит живой человек,
        # и его содержание — повод для разговора с преподавателями. Включать
        # решает оператор, и включение не требует выката.
        default=False,
        env_var="CURATOR_WEEKLY_REPORT_ENABLED",
        warning=(
            "Включено — каждую неделю приходит сводка по работе кураторов. "
            "Выключено — отчёт не приходит, кураторство продолжает работать."
        ),
    ),
    SettingDef(
        key="curator_signal_response_days",
        group=GROUP_SIGNALS,
        title="Срок разбора сигнала куратором",
        description=(
            "Сколько дней у куратора есть на то, чтобы принять, передать "
            "методисту или отклонить сигнал по своему ученику. После этого "
            "сигнал считается просроченным и попадает в недельный отчёт."
        ),
        kind="int",
        default=7,
        unit="дни",
        min_value=1,
        max_value=30,
    ),
    SettingDef(
        key="curator_urgent_response_hours",
        group=GROUP_SIGNALS,
        title="Срок реакции на риск ухода",
        description=(
            "Отдельный, более короткий срок для сигнала «ученик затих». "
            "Остальные поводы можно обсудить на занятии, а этого ученика "
            "может не оказаться уже на следующем."
        ),
        kind="int",
        default=24,
        unit="часы",
        min_value=1,
        max_value=168,
    ),
    SettingDef(
        key="curator_inactivity_weeks",
        group=GROUP_SIGNALS,
        title="Через сколько недель молчания предупредить куратора",
        description=(
            "Если куратор столько недель подряд не сделал по своим ученикам "
            "ничего — ни просмотра, ни ответа, ни проверки, — сигнал приходит "
            "ему самому, в его кабинет. Владелец школы в этом разговоре не "
            "участвует. Работает только при включённом недельном отчёте."
        ),
        kind="int",
        default=2,
        unit="недели",
        min_value=1,
        max_value=8,
        warning=(
            "Единица означает предупреждение после первой же тихой недели — "
            "у человека может быть отпуск или болезнь."
        ),
    ),
    SettingDef(
        key="curator_review_response_days",
        group=GROUP_SIGNALS,
        title="Срок проверки работы ученика",
        description=(
            "Сколько дней работа может лежать на ручной проверке, прежде чем "
            "она попадёт в недельный отчёт как просроченная."
        ),
        kind="int",
        default=3,
        unit="дни",
        min_value=1,
        max_value=14,
    ),
    SettingDef(
        key="charge_cron_enabled",
        group=GROUP_SENSORS,
        title="Пересчитывать начисления за месяц",
        description=(
            "Суточный пересчёт текущего месяца и проверка «ходит, но не выставлен»."
        ),
        kind="bool",
        default=True,
        env_var="CHARGE_CRON_ENABLED",
        warning=(
            "Выключено — строка месяца не появится сама первого числа, "
            "и невыставленных учеников никто не заметит."
        ),
    ),
)


BY_KEY: Dict[str, SettingDef] = {s.key: s for s in SETTINGS}


def get_definition(key: str) -> SettingDef:
    """Найти описание настройки; неизвестный ключ — ошибка, а не молчание."""
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"Настройка {key!r} отсутствует в реестре") from None


def grouped() -> List[Tuple[str, List[SettingDef]]]:
    """Настройки по группам в порядке показа администратору."""
    result: List[Tuple[str, List[SettingDef]]] = []
    for group in GROUP_ORDER:
        items = [s for s in SETTINGS if s.group == group]
        if items:
            result.append((group, items))
    return result


def coerce(definition: SettingDef, raw: Any) -> Any:
    """Привести значение к типу настройки и проверить границы.

    Возвращает готовое значение или бросает `ValueError` с русским текстом —
    он же уходит в ответ кабинета, поэтому написан для человека, а не для лога.
    """
    kind = definition.kind

    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes", "да"):
            return True
        if text in ("false", "0", "no", "нет"):
            return False
        raise ValueError(f"«{definition.title}»: ожидается да или нет")

    if kind == "str":
        text = "" if raw is None else str(raw)
        limit = definition.max_length
        if limit is not None and len(text) > limit:
            raise ValueError(
                f"«{definition.title}»: не длиннее {limit} символов "
                f"(сейчас {len(text)})"
            )
        return text

    # Числа. bool в Python — подкласс int, и `True` молча стал бы единицей:
    # отсекаем его явно, иначе «да» в числовом поле превратится в порог 1.
    if isinstance(raw, bool):
        raise ValueError(f"«{definition.title}»: ожидается число")
    try:
        value: Any = int(raw) if kind == "int" else float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"«{definition.title}»: ожидается число") from None

    low, high = definition.min_value, definition.max_value
    if low is not None and value < low:
        raise ValueError(
            f"«{definition.title}»: не меньше {_human_number(low, kind)} "
            f"{definition.unit}".strip()
        )
    if high is not None and value > high:
        raise ValueError(
            f"«{definition.title}»: не больше {_human_number(high, kind)} "
            f"{definition.unit}".strip()
        )
    return value


def _human_number(value: float, kind: SettingKind) -> str:
    """Целое печатаем без хвоста «.0» — это читает человек, а не парсер."""
    return str(int(value)) if kind == "int" else str(value)


def serialize(definition: SettingDef, value: Any) -> str:
    """Значение в текст для хранения в базе (одна колонка на любой тип)."""
    if definition.kind == "bool":
        return "true" if value else "false"
    return str(value)
