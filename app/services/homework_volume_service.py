"""Сколько задавать на дом: норма из темпа ученика и срока до экзамена (tsk-741).

Формула согласована с оператором 01.09.2026 и построена на замере боевой базы:
у 60 учеников курсов ЕГЭ медиана — 35 верных сдач за 4 недели (≈9 в неделю),
p90 — 122 (≈30), 18 человек не решили за месяц ничего. При этом одиннадцати-
класснику, чтобы пройти 797 заданий до июня 2027, нужно ≈20 в неделю. Разрыв
между «надо» и «делает» — это и есть то, что система обязана показывать.

    цель   = недельная норма класса (11 → 20, 10 и 9 → 12, младше → 8)
    факт   = медиана завершённых элементов за 3 полные недели
    объём  = clamp( min(цель, факт × 1.2), 3, 25 ), но не больше остатка программы

**Единица нормы — МИНУТЫ РАБОТЫ, а не штуки** (решение оператора 09.09,
[[tsk-867]]). Та же формула считается дважды: в минутах — по измеренному весу
элемента (`task_effort_service`, [[tsk-851]]) — и в штуках, как раньше. Ведёт
минутная, штучная осталась ограждением (см. ниже).

Почему пришлось менять единицу. Элементы разновесные, и на боевых данных
норма «20 элементов в неделю» означает 4 минуты (лёгкие с выбором ответа),
5 минут (сложные с выбором) или 79 минут (лёгкие задачи с решением) — разброс
в двадцать раз при одном и том же числе на экране. Замер выданных ДЗ за 30
дней (164 выдачи, 09.09): медиана 9.6 минуты, p10 — 1.6, p90 — 64.3, максимум
125. То есть «столько же, сколько в прошлый раз» до этой правки не значило
ничего.

**Штуки остались ограждением, и это не пережиток.** Бюджет времени в чистом
виде даёт обратный перекос: серия заданий с выбором ответа по 12-15 секунд
покрывает недельные 75 минут только на трёхстах штуках. Триста нажатий за
вечер — не учебная работа, а марафон, поэтому набор ограничен И бюджетом
времени, И прежним штучным потолком: что раньше кончится.

**Мерить нечем — считаем по-старому и говорим об этом.** Пустая телеметрия
(новая установка, окно без сдач) даёт `None` от измерителя. Подставлять вместо
него число нельзя: посчиталась бы норма, которой никто не мерил. В этом случае
`effort_measured=False`, минутные поля пустые, ведёт штучный расчёт.

**Вес теории — прокси, а не измерение.** События «материал открыт» в системе
нет (`MATERIAL_EFFORT_SECONDS_PROXY = 49 с` — медиана промежутка между
соседними отметками). Настоящее измерение заводится отдельно ([[tsk-868]]);
пока бюджет теории приблизителен, и на экране число подписано как оценка.

**Почему цель задаётся классом напрямую, а не выводится из остатка программы.**
Первая редакция считала `надо = остаток / (недель до экзамена × 0.85)` — и это
не выдержало проверки на живых данных 01.09. Курс «ЕГЭ по информатике» — это
**банк из 1758 заданий**, а не конечная программа: остаток у учеников 1700-4800
элементов, `надо` выходило 52-58 в неделю у ВСЕХ и всегда упиралось в потолок.
Следствие было хуже арифметики: `min(надо, факт × 1.2)` всегда выбирал вторую
часть, и класс переставал влиять на объём вовсе — то есть весь смысл вопроса
про класс (фаза 1 этой же задачи) пропадал.

Поэтому срок до экзамена остался тем, что он есть — **пояснением**
(`weeks_to_exam`, видно преподавателю), а нагрузку задаёт целевая норма класса.
Цифра 20 для 11 класса взята из того же замера: столько нужно, чтобы пройти
базовый курс «Python для ЕГЭ» (797 заданий) за 39 недель до июня 2027. У 10
класса год в запасе, у 9 программа легче — им 12.

Что важно помнить читающему:

- **Единица нормы — элемент программы, а не задание.** Материалы (теория) —
  такая же домашняя работа: прямое требование оператора «теорию учат дома,
  чтобы занятие сместилось к заданиям». Потолок 25 откалиброван по p90 сдач
  заданий, то есть заведомо не занижен.
- **Медиана, а не среднее.** Один запойный вечер на 200 задач не должен
  задирать норму на месяц вперёд; на проде такие всплески есть (максимум за
  неделю — 1400 строк).
- **Считается только то, что ученик сделал САМ.** Ручные зачёты преподавателя
  ставятся пачками (у одного ученика 660 ручных против 4 настоящих сдач,
  tsk-656) — правило берётся из `learning_gaps_service`, а не пишется заново.
- **Не грузим больше, чем человек тянет** (`факт × 1.2`): норма растёт
  ступеньками. Если человек не дотягивает до нормы своего класса — это не
  повод завалить его заданиями, а сигнал преподавателю: он виден в `pace_gap`.
- **Качество важнее скорости.** Доля верных ниже 60% — объём уменьшается на
  четверть: человек тонет, добавлять ему задания вредно (решение оператора
  01.09: «скорость с поправкой на качество»). Поправка не применяется, пока
  сдач слишком мало для вывода.
- **Класс неизвестен → считаем как 11** (решение оператора 01.09): ошибиться
  в сторону более короткого срока безопаснее. Сам класс собирается вопросом в
  кабинете, фаза 1 той же задачи.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    SERVICE_COURSES_CTE,
    non_service_course_filter,
    real_student_material_filter,
    real_student_results_filter,
)
# tsk-867: вес элемента в секундах — измеритель, ничего не решающий сам.
from app.services.task_effort_service import (
    MATERIAL_EFFORT_SECONDS_PROXY,
    EffortTable,
    load_effort_table,
)
# tsk-741: «что вообще входит в программу» — одно правило на весь проект.
from app.services.content_grace_service import graced_for_roots
from app.services.manual_progress_service import REQUIREMENT_LEVELS
# tsk-741: «занятие пропущено» — тоже одно правило; перенос пропуском не считается.
from app.services import attendance_service

logger = logging.getLogger(__name__)

#: Меньше этого на дом не задаём — иначе выдача теряет смысл.
MIN_PER_WEEK = 3
#: Базовый потолок выдачи: выше p90 нынешнего темпа ДЗ просто перестают делать,
#: и невыполнимая норма обесценивает саму механику.
#:
#: **Это потолок ДЛЯ ТЕХ, КТО СТОЛЬКО НЕ ДЕЛАЕТ.** Тому, кто уже показывает
#: больше, он не мешает — см. `ceiling_for()`. Решение оператора 05.09: жёсткие
#: 25 сами стали ограничением — ученику, пришедшему в ноябре, при них не
#: помещается даже несокращаемое ядро программы (21.4 недели × 25 = 535 против
#: 605), хотя на проде есть люди с темпом 48-66 в неделю.
MAX_PER_WEEK = 25
#: На сколько норма может превышать сегодняшний темп ученика за один шаг.
GROWTH_FACTOR = 1.2

#: Шаг роста для того, кто НЕ УСПЕВАЕТ к сроку программы (tsk-896, решение
#: оператора 10.09: «логично задавать ей больше, чтобы дома работала активнее
#: и нагнала»). Обычный шаг растит бережно — и человека, которому не хватает
#: тридцати процентов, он подтягивает годами.
#:
#: Полтора, а не «сразу до цели»: цель отстающего выше его темпа в разы
#: (Крук делает 57 минут при нужных 90), и выдача, которую невозможно сделать,
#: обесценивает саму механику — тот же довод, что стоит за потолком.
BEHIND_GROWTH_FACTOR = 1.5

#: Пол недельной выдачи — доля от нормы (tsk-909, решение оператора 11.09).
#:
#: Шаг роста считается от СОБСТВЕННОГО темпа ученика, и у того, кто дома почти
#: не работает, он упирается в ноль: Хантанову при норме 90 минут задавалось
#: 10, Костенкову 15, Тоинову 19. Механика молчала ровно там, где нужнее
#: всего, — и «домашняя работа сделана» у такого ученика значило полтора
#: процента недели.
#:
#: Половина, а не вся норма: выдача, которую заведомо не сделать, обесценивает
#: механику — тот же довод, что стоит за потолком и за шагом роста. Половина
#: заметно поднимает планку (те же трое получают 45 минут вместо 10-19), но
#: остаётся выполнимой.
TARGET_FLOOR_SHARE = 0.5
#: Целевая недельная норма по классу, элементов программы.
#: 11 класс — выпускной, полный ход: столько нужно, чтобы пройти базовый курс
#: (797 заданий) за 39 недель до июня. 10 класс — год в запасе, 9 класс — ОГЭ
#: этим летом, но программа легче. Младше 9 — щадящий режим: экзамен далеко.
TARGET_PER_WEEK_BY_GRADE: dict[int, int] = {11: 20, 10: 12, 9: 12}
#: Норма для тех, кто младше девятого класса.
TARGET_PER_WEEK_JUNIOR = 8

#: С этого месяца выпускной класс уходит на отработку вариантов (1-2 варианта в
#: неделю целиком), и времени на обычное ДЗ почти не остаётся. Норма падает —
#: иначе система весь финиш будет показывать «не дотягивает», хотя человек как
#: раз занят главным. Решение оператора 01.09.2026.
EXAM_SPRINT_FROM_MONTH = 3
#: Недельная норма выпускного класса на финише: остаток времени держим за
#: вариантами, домашняя работа становится добавкой, а не основой.
TARGET_PER_WEEK_EXAM_SPRINT = 6

#: Потолок нагона. Пропустивший занятия — чаще всего и есть отстающий, и
#: удвоенная выдача для него не «нагон», а повод бросить совсем. Полтора
#: объёма человек ещё видит выполнимым.
MAX_CATCH_UP_FACTOR = 1.5

# --- Пропуски (tsk-914) ---------------------------------------------------
#
# Правило оператора 12.09: «пропуск добавляет к ДЗ норматив урока — но только
# если пропуск не погашен». До этого нагон был множителем (+25% за пропуск),
# а «пропуск» — любым `no_show` в окне. Оба конца были неверны:
#
# * множитель не отвечает на вопрос «сколько работы не случилось» — а не
#   случился ровно один час занятия, и добавить надо его;
# * `no_show` бывает призраком. Курунов переехал с четверга на субботу, слот
#   четверга ему выключили, но уже созданные занятия остались с ним как
#   участником — два `no_show` при двух отработанных субботних часах в
#   неделю. Или преподаватель не отметил явку, а человек работал весь час.
#
# Поэтому пропуск считается ПО НЕДЕЛЕ: сколько часов положено по расписанию
# (активные слоты) против сколько отработано — пришёл по отметке, либо
# работал в окне любого часа, своего или чужого. Непогашено только то, чего
# не хватает до плана недели.

#: Вес одного часа занятия в минутах работы, когда у ученика нет своих
#: посещённых часов в окне (новичок или не ходит вовсе). Медиана по школе,
#: замер 12.09 по посещённым часам последних четырёх недель.
LESSON_NORM_FALLBACK_MINUTES = 24

#: Остатка программы меньше, чем на столько недель — пора добавлять курс.
#: Сигнал поднимается заранее: «программа кончилась» узнавать в тот день, когда
#: ученику нечего задать, поздно (вопрос оператора 01.09: с опережением графика
#: без ДЗ не оставляем).
PROGRAM_LOW_WEEKS = 4
#: Сколько полных недель берём для оценки фактического темпа.
FACT_WEEKS = 3
#: Доля верных, ниже которой человек считается тонущим.
QUALITY_THRESHOLD = 0.6
#: Во сколько раз уменьшаем объём тонущему.
QUALITY_PENALTY = 0.75
#: Меньше этого числа сдач — о качестве судить не по чему.
MIN_QUALITY_SAMPLE = 5
#: Класс, по которому считаем тех, чей класс неизвестен (решение оператора).
ASSUMED_GRADE = 11
#: Месяц и день основного периода экзаменов — начало июня.
EXAM_MONTH = 6
EXAM_DAY = 1

# --- Норма в минутах работы (tsk-867) -------------------------------------
#
# Все четыре числа ниже — не новое продуктовое решение, а ПЕРЕВОД прежних
# штучных норм в измеренную единицу. Множитель перевода взят с прода 09.09:
# средний вес непройденного элемента программы ЕГЭ — 222 секунды (≈3.7 минуты)
# при узком разбросе между учениками (215-281 с), потому что остаток у всех —
# почти весь каталог. Отсюда 20 элементов ≈ 75 минут, 12 ≈ 45, 8 ≈ 30, 6 ≈ 20.
#
# Перевод сверен со вторым, независимым замером — фактическим темпом 88
# учеников программы: медиана 5.5 элемента в неделю = 11.1 минуты, p90 — 37
# элементов = 68.7 минуты. То есть переведённый потолок (90 минут) лежит выше
# p90 живого темпа, а переведённый пол (10 минут) — около медианы, ровно как
# было в штуках.

#: Меньше этого на дом не задаём. Перевод прежних трёх элементов.
MIN_MINUTES_PER_WEEK = 10
#: Базовый потолок недельной выдачи в минутах — перевод прежних 25 элементов.
#: Как и штучный, это потолок ДЛЯ ТЕХ, КТО СТОЛЬКО НЕ ДЕЛАЕТ: у работающего
#: больше он поднимается до его собственного темпа (`minutes_ceiling_for`).
MAX_MINUTES_PER_WEEK = 90
#: Целевая недельная норма по классу в минутах — перевод TARGET_PER_WEEK_BY_GRADE.
TARGET_MINUTES_BY_GRADE: dict[int, int] = {11: 75, 10: 45, 9: 45}
#: Норма для тех, кто младше девятого класса (перевод восьми элементов).
TARGET_MINUTES_JUNIOR = 30
#: Норма выпускного класса на финише (перевод шести элементов).
TARGET_MINUTES_EXAM_SPRINT = 20


@dataclass(frozen=True)
class VolumePlan:
    """Норма домашней работы и всё, из чего она сложилась.

    Состав полей — не отладочный: ровно это уходит в `volume_details` выдачи и
    показывается преподавателю. Число без объяснения («задать 12») никто не
    сможет ни оспорить, ни проверить.
    """

    #: Класс ученика; None — не указан (тогда считали по ASSUMED_GRADE).
    grade: Optional[int]
    #: True — класс не известен, срок взят пессимистично.
    grade_assumed: bool
    #: Дата ближайшего для этого класса экзамена.
    exam_date: date
    #: Недель до экзамена (может быть дробным).
    weeks_to_exam: float
    #: Незавершённых элементов программы. Не знаменатель нормы (курс — банк
    #: заданий, а не конечная программа), а потолок: больше, чем осталось, не
    #: задашь.
    remaining_items: int
    #: Сколько нужно в неделю ЭТОМУ ученику, чтобы успеть. Считается из его
    #: личного остатка программы и срока — не из класса (решение оператора
    #: 04.09). Может быть недостижимо большой: это правда о разрыве, и прятать
    #: её нельзя. Ученику это число не показывается.
    target_per_week: int
    #: Какая программа: `ege`, `oge` или None — ученик не записан ни на одну,
    #: тогда норма берётся по классу, как раньше.
    program_kind: Optional[str]
    #: К какому дню программу нужно закончить; None — программы нет.
    program_deadline: Optional[date]
    #: Обязательных ЗАДАНИЙ программы осталось (материалы считаются отдельно и
    #: входят в `remaining_items`).
    program_tasks_remaining: Optional[int]
    #: Сколько человек делает сейчас (медиана за FACT_WEEKS недель).
    fact_per_week: float
    #: Доля верных сдач за то же окно; None — сдач слишком мало.
    correct_ratio: Optional[float]
    #: True — объём уменьшен из-за низкой доли верных.
    quality_penalty_applied: bool
    #: Итоговая норма на неделю.
    volume_per_week: int
    #: На сколько недель хватит остатка программы при этой норме; None — норма
    #: нулевая (задавать нечего). Это ответ на «что делать с теми, кто идёт с
    #: опережением»: их видно ЗАРАНЕЕ, а не в день, когда задавать стало нечего.
    weeks_of_program_left: Optional[int]
    #: Программы осталось меньше чем на PROGRAM_LOW_WEEKS недель (или её нет
    #: вовсе) — пора добавлять ученику курс.
    needs_more_program: bool
    #: True — норма снижена, потому что выпускной класс с марта отрабатывает
    #: варианты, а не проходит новое.
    exam_sprint: bool
    #: Нужная скорость выше потолка выдачи: программа в оставшийся срок не
    #: помещается физически. Это утверждение про ПРОГРАММУ И СРОК, а не про
    #: ученика, и читать его надо так же — иначе «нужно 48» выглядит упрёком
    #: человеку, который ни при чём. На замере 04.09 таких 58 из 76.
    target_unreachable: bool
    #: Занятий пропущено за окно расчёта. Перенесённые сюда не входят.
    missed_lessons: int
    #: Во сколько раз объём увеличен, чтобы нагнать пропущенное; 1.0 — не
    #: увеличен. tsk-914: производная от `catch_up_minutes`, оставлена ради
    #: прежних читателей поля.
    catch_up_factor: float
    #: На сколько элементов в неделю человек не дотягивает до нормы своего
    #: класса; 0 — дотягивает. Это и есть сигнал преподавателю: не «завалить
    #: заданиями», а «видно, что отстаёт».
    pace_gap: int
    #: Сколько из сделанного за окно пришлось на занятия, а не на дом; None —
    #: работы не было вовсе. Доля 0..1.
    lesson_share: Optional[float] = None
    #: Сколько недель реально взято для оценки темпа. Меньше `FACT_WEEKS` —
    #: ученик с нами меньше трёх недель, и по трём его мерить нельзя.
    fact_weeks_used: int = FACT_WEEKS
    #: Темп, нужный чтобы закончить программу к концу ЭТОГО учебного года;
    #: None — ученик и так выпускник, у него другого срока нет.
    early_target_per_week: Optional[int] = None
    #: tsk-922: те же ранние нормы в минутах работы — единица, в которой
    #: показывается весь блок программы; None — вес не измерен.
    early_target_minutes_per_week: Optional[int] = None
    summer_target_minutes_per_week: Optional[int] = None
    #: То же, но с занятиями летом.
    summer_target_per_week: Optional[int] = None
    #: Дата раннего финиша (конец учебного года).
    early_deadline: Optional[date] = None
    #: Дата финиша с летними занятиями.
    summer_deadline: Optional[date] = None

    # --- Норма в минутах работы (tsk-867) ---------------------------------
    #: Вес элементов измерен, и минутные поля ниже заполнены. False — сдач в
    #: окне телеметрии не было (новая установка, пустая база): норму ведёт
    #: штучный расчёт, а минуты не показываются вовсе. Выдумывать вес нельзя —
    #: по нему посчиталась бы норма, которой никто не мерил.
    effort_measured: bool = False
    #: Итоговая норма НА НЕДЕЛЮ В МИНУТАХ — то самое число, которым ведётся
    #: выдача. None — мерить нечем.
    minutes_per_week: Optional[int] = None
    #: Сколько минут в неделю нужно ЭТОМУ ученику, чтобы успеть к сроку.
    target_minutes_per_week: Optional[int] = None
    #: Сколько минут в неделю человек работает сейчас (медиана по неделям).
    fact_minutes_per_week: Optional[float] = None
    #: Во сколько минут оценивается весь непройденный остаток программы.
    #: Оценка, а не измерение: вес теории здесь — прокси ([[tsk-868]]).
    remaining_minutes: Optional[int] = None
    #: tsk-914: пропусков, которых не хватает до плана недели по расписанию —
    #: только за них и нагоняем. `missed_lessons` минус погашенные: пришёл
    #: на другой час, работал в окне своего без отметки, переехал в другой слот.
    missed_unpaid: int = 0
    #: tsk-914: вес одного часа занятия для этого ученика в минутах работы —
    #: столько добавляется за каждый непогашенный пропуск. Свой замер по
    #: посещённым часам окна, иначе `LESSON_NORM_FALLBACK_MINUTES`.
    lesson_norm_minutes: Optional[int] = None
    #: tsk-914: сколько минут добавлено к недельной выдаче за пропуски.
    catch_up_minutes: int = 0

    def as_details(self) -> dict[str, Any]:
        """Снимок для `homework_assignment.volume_details` (JSON-совместимый)."""
        data = asdict(self)
        data["exam_date"] = self.exam_date.isoformat()
        data["program_deadline"] = (
            self.program_deadline.isoformat() if self.program_deadline else None
        )
        data["early_deadline"] = (
            self.early_deadline.isoformat() if self.early_deadline else None
        )
        data["summer_deadline"] = (
            self.summer_deadline.isoformat() if self.summer_deadline else None
        )
        return data


def ceiling_for(fact_per_week: float, *, on_track: bool = True) -> int:
    """Потолок недельной выдачи для ученика с таким фактическим темпом.

    Потолок нужен, чтобы не завалить человека сверх того, что он тянет. Но
    «тянет» — это про него, а не про среднее по школе: тот, кто уже делает 40
    в неделю, от сорока не надорвётся, и срезать его до 25 значит мешать ему
    успеть. Поэтому базовый потолок поднимается ровно до его собственного
    темпа с обычным шагом роста (`GROWTH_FACTOR`), не выше.

    Медленного это не касается вовсе: у него `факт × 1.2` заведомо ниже
    базового потолка, и он остаётся прежним (решение оператора 05.09).

    `on_track=False` — человек не успевает к сроку: шаг роста больше
    (tsk-896), и потолок поднимается вместе с ним.
    """
    growth = GROWTH_FACTOR if on_track else BEHIND_GROWTH_FACTOR
    return max(MAX_PER_WEEK, int(round(fact_per_week * growth)))


def minutes_ceiling_for(
    fact_minutes_per_week: float, *, on_track: bool = True
) -> int:
    """Потолок недельной выдачи В МИНУТАХ для ученика с таким темпом.

    Тот же принцип, что у штучного потолка (`ceiling_for`): базовый потолок
    поднимается до собственного темпа человека с обычным шагом роста. Кто уже
    работает по два часа в неделю, от двух часов не надорвётся, а срезать его
    до полутора значит мешать ему успеть.

    `on_track=False` — человек не успевает к сроку: шаг роста больше (tsk-896).
    """
    growth = GROWTH_FACTOR if on_track else BEHIND_GROWTH_FACTOR
    return max(
        MAX_MINUTES_PER_WEEK, int(round(fact_minutes_per_week * growth))
    )


def target_minutes_for(grade: Optional[int], today: Optional[date] = None) -> int:
    """Целевая недельная норма класса В МИНУТАХ на эту дату.

    Минутный близнец `target_per_week_for`: те же правила про неизвестный класс
    и про мартовский спринт выпускников, только в измеренной единице. Обе
    функции обязаны решать одинаково, поэтому спринт определяется здесь не
    заново, а сравнением со штучной нормой — разъехавшись, они дали бы
    ученику разную норму в зависимости от того, чем её меряют.
    """
    effective = grade if grade is not None else ASSUMED_GRADE
    if target_per_week_for(grade, today) == TARGET_PER_WEEK_EXAM_SPRINT and (
        grade is None or effective >= 11
    ):
        return TARGET_MINUTES_EXAM_SPRINT
    if effective in TARGET_MINUTES_BY_GRADE:
        return TARGET_MINUTES_BY_GRADE[effective]
    return TARGET_MINUTES_JUNIOR


def exam_date_for(grade: Optional[int], today: date) -> date:
    """Дата ближайшего экзамена для класса.

    Экзаменные классы — 9 (ОГЭ) и 11 (ЕГЭ); всем, кто младше, считаем срок до
    ближайшего из них. Учебный год отсчитывается от июня: в сентябре 2026
    одиннадцатиклассник сдаёт в июне 2027, десятиклассник — в июне 2028.

    Args:
        grade: класс 1-11 или None (тогда ASSUMED_GRADE).
        today: сегодняшняя дата.

    Returns:
        Дата начала основного периода экзаменов.
    """
    effective = grade if grade is not None else ASSUMED_GRADE
    target = 9 if effective <= 9 else 11
    years_left = max(target - effective, 0)

    this_year_exam = date(today.year, EXAM_MONTH, EXAM_DAY)
    base_year = today.year if today <= this_year_exam else today.year + 1
    return date(base_year + years_left, EXAM_MONTH, EXAM_DAY)


def _course_ids(raw: str) -> list[int]:
    """Номера курсов из настройки «88,112» → [88, 112]. Мусор молча пропускаем:
    настройку правит человек, и одна опечатка не должна ронять расчёт всем."""
    result: list[int] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            result.append(int(chunk))
    return result


async def _scoped_remaining(
    db: AsyncSession,
    *,
    student_id: int,
    program: dict[str, Any],
    fact_per_week: float,
    today: date,
    effort_table: Optional[EffortTable] = None,
    fact_minutes_per_week: Optional[float] = None,
) -> tuple[int, Optional[float]]:
    """Сколько программы ученику РЕАЛЬНО спланировано: штуки и минуты (tsk-869).

    Объём режется под срок и темп (`program_scope_service`, tsk-798): ядро —
    теория и разбор номеров — проходится целиком, отработка берётся частью.
    Норматив обязан считаться от этого числа, иначе система требует того, что
    сама же отменила.

    Возвращается пара: элементов и минут работы. Вторая нужна норме в минутах
    (tsk-867) — у неё был ровно тот же дефект, и чинить его надо тем же числом,
    иначе две единицы одной нормы разошлись бы между собой.

    Минуты — `None`, когда вес не измерен (пустая телеметрия): тогда минутный
    норматив остаётся на полном остатке, как было до 09.09.

    **Ошибки расчёта НЕ глушатся.** Первая редакция ловила здесь любое
    исключение и возвращала «считай по полному остатку» — так делать нельзя по
    двум причинам. Ошибка запроса помечает транзакцию PostgreSQL сбойной, и
    дальше в ЭТОЙ ЖЕ сессии любой запрос вернёт ошибку — то есть проглоченный
    сбой всплыл бы в чужом коде и в чужих числах, где его никто не свяжет с
    домашней работой. И вторая: молчаливая подмена нормы на завышенную — это
    ровно тот дефект, который задача и чинит.

    Импорт локальный: `program_scope_service` зовёт этот модуль за темпом и
    сроком, а на уровне модуля вышло бы кольцо.
    """
    from app.services import program_scope_service

    scope = await program_scope_service.compute_scope(
        db,
        student_id=student_id,
        kind=program["kind"],
        root_ids=program["root_ids"],
        deadline=program["deadline"],
        fact_per_week=fact_per_week,
        today=today,
        effort_table=effort_table,
        fact_minutes_per_week=fact_minutes_per_week,
    )

    minutes: Optional[float] = None
    if scope.core_minutes is not None and scope.drill_allowed_minutes is not None:
        minutes = float(scope.core_minutes + scope.drill_allowed_minutes)
    return scope.core_total + scope.drill_allowed, minutes


def _weekly_for(remaining: int, deadline: date, today: date) -> int:
    """Сколько элементов в неделю нужно, чтобы пройти `remaining` к сроку."""
    days = (deadline - today).days
    if days <= 0:
        return remaining
    return max(int(-(-remaining // max(days / 7.0, 1e-9))), 0)


def _deadline_for(raw: str, exam_day: date, fallback_md: tuple[int, int]) -> date:
    """Срок «пройти программу» в году экзамена: из настройки вида «03-31».

    Год берётся у экзамена, а не у сегодняшнего дня: одиннадцатикласснику это
    ближайший март, десятикласснику — следующий (решение оператора 04.09).
    """
    month, day = fallback_md
    parts = (raw or "").split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        month, day = int(parts[0]), int(parts[1])
    try:
        return date(exam_day.year, month, day)
    except ValueError:
        return date(exam_day.year, *fallback_md)


#: Остаток ОБЯЗАТЕЛЬНЫХ элементов программы подготовки — курсов, которые ученик
#: должен закончить к сроку. Не «весь банк заданий курса», как считалось до
#: 04.09: банк не проходят целиком, и норма от него получалась одинаковой у всех.
_PROGRAM_REMAINING_SQL = f"""
WITH RECURSIVE tree AS (
    SELECT unnest(CAST(:root_ids AS int[])) AS member_course_id
    UNION
    SELECT cp.course_id
      FROM tree t
      JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
),
course_tasks AS (
    SELECT DISTINCT t.id
      FROM tasks t JOIN tree ON tree.member_course_id = t.course_id
     WHERE COALESCE(t.is_active, true) AND t.requirement_level = ANY(:levels)
       AND t.id <> ALL(CAST(:graced_tasks AS int[]))
),
course_materials AS (
    SELECT DISTINCT m.id
      FROM materials m JOIN tree ON tree.member_course_id = m.course_id
     WHERE COALESCE(m.is_active, true) AND m.requirement_level = ANY(:levels)
       AND m.id <> ALL(CAST(:graced_materials AS int[]))
),
tasks_done AS (
    SELECT DISTINCT tr.task_id AS id
      FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
     WHERE tr.user_id = :student_id AND tr.is_correct = true
       AND tr.task_id IN (SELECT id FROM course_tasks)
    UNION
    SELECT stp.task_id
      FROM student_task_progress stp
     WHERE stp.student_id = :student_id AND stp.status = 'skipped'
       AND stp.task_id IN (SELECT id FROM course_tasks)
),
materials_done AS (
    SELECT DISTINCT smp.material_id AS id
      FROM student_material_progress smp
     WHERE smp.student_id = :student_id AND smp.status IN ('completed', 'skipped')
       AND smp.material_id IN (SELECT id FROM course_materials)
)
SELECT (SELECT count(*) FROM course_tasks) AS tasks_total,
       (SELECT count(*) FROM course_materials) AS materials_total,
       (SELECT count(*) FROM tasks_done) AS tasks_done,
       (SELECT count(*) FROM materials_done) AS materials_done
"""


async def program_for_student(
    db: AsyncSession, *, student_id: int, grade: Optional[int], today: date
) -> Optional[dict[str, Any]]:
    """Программа подготовки ученика: какие курсы, к какому сроку, сколько осталось.

    Программа определяется по ФАКТИЧЕСКОЙ записи на курсы, а не по классу:
    запись — это то, что школа реально сделала, а класс ученик указывает сам и
    у 59 человек из 82 он до сих пор пустой.

    `None` — ученик не записан ни на одну программу (или списки курсов пусты в
    настройках). Тогда норма считается по-старому, от класса.
    """
    from app.core import settings_store

    oge_ids = _course_ids(settings_store.get_str("homework_program_oge_courses"))
    ege_ids = _course_ids(settings_store.get_str("homework_program_ege_courses"))
    if not oge_ids and not ege_ids:
        return None

    enrolled = set(
        (
            await db.execute(
                text(
                    "SELECT course_id FROM user_courses "
                    " WHERE user_id = :sid AND is_active = true"
                ),
                {"sid": student_id},
            )
        ).scalars().all()
    )
    # ОГЭ проверяем первым: девятикласснику могли открыть и материалы ЕГЭ, но
    # сдаёт он в этом году ОГЭ, и срок у него свой.
    if enrolled & set(oge_ids):
        kind, root_ids = "oge", oge_ids
        deadline_raw = settings_store.get_str("homework_program_oge_deadline")
        fallback = (4, 30)
    elif enrolled & set(ege_ids):
        kind, root_ids = "ege", ege_ids
        deadline_raw = settings_store.get_str("homework_program_ege_deadline")
        fallback = (3, 31)
    else:
        return None

    # tsk-912: досыпанное в пройденные темы — не остаток (правило tsk-692).
    graced = await graced_for_roots(db, student_id, root_ids)
    row = (
        await db.execute(
            text(_PROGRAM_REMAINING_SQL),
            {
                "student_id": student_id,
                "root_ids": root_ids,
                "levels": list(REQUIREMENT_LEVELS),
                "graced_tasks": list(graced.tasks),
                "graced_materials": list(graced.materials),
            },
        )
    ).mappings().one()

    total = int(row["tasks_total"]) + int(row["materials_total"])
    done = int(row["tasks_done"]) + int(row["materials_done"])
    deadline = _deadline_for(deadline_raw, exam_date_for(grade, today), fallback)
    return {
        "kind": kind,
        "deadline": deadline,
        "root_ids": root_ids,
        "remaining": max(total - done, 0),
        "tasks_remaining": max(int(row["tasks_total"]) - int(row["tasks_done"]), 0),
        "total": total,
    }


def target_per_week_for(grade: Optional[int], today: Optional[date] = None) -> int:
    """Целевая недельная норма для класса на эту дату.

    Класс не указан — считаем как 11 (решение оператора 01.09): ошибиться в
    сторону более высокой нагрузки безопаснее, чем оставить выпускника без неё.
    Норма — не приговор: выше того, что человек тянет, объём всё равно не
    поднимется (`факт × 1.2`).

    **С марта у выпускного класса норма падает.** С этого месяца одиннадцатый
    класс переходит на отработку вариантов — 1-2 полных варианта в неделю, — и
    времени на обычное ДЗ почти не остаётся. Оставить прежние 20 значило бы
    весь финиш показывать преподавателю «не дотягивает», хотя ученик занят
    ровно тем, чем должен. Считается по месяцу ЭКЗАМЕНАЦИОННОГО года: в марте
    2027 выпускник 2027 года уже на финише, а десятикласснику до его марта
    ещё год.

    Args:
        grade: класс 1-11 или None.
        today: дата расчёта; None — сегодня.

    Returns:
        Сколько элементов в неделю считать нормой.
    """
    effective = grade if grade is not None else ASSUMED_GRADE
    moment = today or date.today()

    if effective >= 11 or grade is None:
        exam_day = exam_date_for(grade, moment)
        sprint_start = date(exam_day.year, EXAM_SPRINT_FROM_MONTH, 1)
        if sprint_start <= moment <= exam_day:
            return TARGET_PER_WEEK_EXAM_SPRINT

    if effective in TARGET_PER_WEEK_BY_GRADE:
        return TARGET_PER_WEEK_BY_GRADE[effective]
    return TARGET_PER_WEEK_JUNIOR


_REMAINING_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE},
tree AS (
    SELECT uc.course_id AS member_course_id
      FROM user_courses uc
     WHERE uc.user_id = :student_id AND uc.is_active = true
    UNION
    SELECT cp.course_id
      FROM tree t
      JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
),
course_tasks AS (
    SELECT DISTINCT t.id
      FROM tasks t JOIN tree ON tree.member_course_id = t.course_id
     WHERE COALESCE(t.is_active, true)
       AND t.requirement_level = ANY(:levels)
       AND {non_service_course_filter('t')}
       AND t.id <> ALL(CAST(:graced_tasks AS int[]))
),
course_materials AS (
    SELECT DISTINCT m.id
      FROM materials m JOIN tree ON tree.member_course_id = m.course_id
     WHERE COALESCE(m.is_active, true)
       AND m.requirement_level = ANY(:levels)
       AND {non_service_course_filter('m')}
       AND m.id <> ALL(CAST(:graced_materials AS int[]))
),
tasks_done AS (
    SELECT DISTINCT tr.task_id AS id
      FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
     WHERE tr.user_id = :student_id AND tr.is_correct = true
       AND tr.task_id IN (SELECT id FROM course_tasks)
    UNION
    SELECT stp.task_id
      FROM student_task_progress stp
     WHERE stp.student_id = :student_id AND stp.status = 'skipped'
       AND stp.task_id IN (SELECT id FROM course_tasks)
),
materials_done AS (
    SELECT DISTINCT smp.material_id AS id
      FROM student_material_progress smp
     WHERE smp.student_id = :student_id AND smp.status IN ('completed', 'skipped')
       AND smp.material_id IN (SELECT id FROM course_materials)
)
SELECT (SELECT count(*) FROM course_tasks) + (SELECT count(*) FROM course_materials)
         AS total_items,
       (SELECT count(*) FROM tasks_done) + (SELECT count(*) FROM materials_done)
         AS done_items
"""

#: Завершённое ЗА НЕДЕЛЮ, по неделям — для медианы фактического темпа.
#: Ручные зачёты отсечены общим правилом проекта, а не своей копией условия.
#:
#: tsk-881: служебные курсы (`is_service`, tsk-877) в темп не идут. Вводный
#: курс проходится один раз, и его два-три десятка пунктов дают всплеск в
#: одной неделе — а от темпа поднимается и потолок нормы, и норматив, то есть
#: человеку прибавляли работы за то, что он прочитал правила школы.
_FACT_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE},
bounds AS (
    -- Окна по семь дней, отсчитанные назад ОТ МОМЕНТА РАСЧЁТА, а не
    -- календарные недели (tsk-819). Календарная нарезка бралась от `since`,
    -- и последнее окно кончалось прошлым воскресеньем: работа текущей недели
    -- не попадала ни в одно окно ни при каком дне недели, кроме понедельника.
    -- У новичка окно всего одно — и оно приходилось на неделю, в которую он
    -- ещё не занимался, то есть темп выходил нулевым (замер: пн 10, вт-вс 0).
    --
    -- CAST(...), а не `:since::timestamptz`: SQLAlchemy НЕ считает параметром
    -- имя, за которым идёт двоеточие, и `:since` уехал бы в запрос буквально —
    -- синтаксическая ошибка в неочевидном месте.
    SELECT CAST(:since AS timestamptz)
             + CAST(idx || ' weeks' AS interval) AS starts_at,
           CAST(:since AS timestamptz)
             + CAST((idx + 1) || ' weeks' AS interval) AS ends_at
      FROM generate_series(0, :weeks - 1) AS idx
)
SELECT b.starts_at::date AS week,
       td.n + md.n AS done
  FROM bounds b
  LEFT JOIN LATERAL (
        SELECT count(DISTINCT tr.task_id) AS n
          FROM task_results tr
          JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
          JOIN tasks t ON t.id = tr.task_id
         WHERE tr.user_id = :student_id AND tr.is_correct = true
           AND {real_student_results_filter('tr')}
           AND {non_service_course_filter('t')}
           AND tr.submitted_at >= b.starts_at
           AND tr.submitted_at < b.ends_at
       ) td ON true
  LEFT JOIN LATERAL (
        SELECT count(DISTINCT smp.material_id) AS n
          FROM student_material_progress smp
          JOIN materials m ON m.id = smp.material_id
         WHERE smp.student_id = :student_id AND smp.status = 'completed'
           AND smp.completed_at IS NOT NULL
           AND {real_student_material_filter('smp')}
           AND {non_service_course_filter('m')}
           AND smp.completed_at >= b.starts_at
           AND smp.completed_at < b.ends_at
       ) md ON true
 ORDER BY 1
"""

#: Остаток программы, РАЗЛОЖЕННЫЙ ПО РОДУ ЭЛЕМЕНТОВ (tsk-867). Вес живёт в
#: Python (`task_effort_service`), а не в SQL: таблица весов снимается один раз
#: на расчёт, и тащить её в каждый запрос значило бы считать телеметрию заново.
#: Поэтому база отдаёт «сколько чего осталось», а минуты собираются наверху.
#:
#: `:root_ids` пуст (NULL) — корнями берутся все курсы ученика; заданы — только
#: они. Два разных остатка нужны затем же, зачем в штучном расчёте: программа
#: подготовки задаёт норму, а все курсы — потолок «больше, чем осталось, не
#: задашь».
_REMAINING_WEIGHTS_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE},
roots AS (
    SELECT unnest(CAST(:root_ids AS int[])) AS member_course_id
    UNION
    SELECT uc.course_id
      FROM user_courses uc
     WHERE uc.user_id = :student_id AND uc.is_active = true
       AND CAST(:root_ids AS int[]) IS NULL
),
tree AS (
    SELECT member_course_id FROM roots
    UNION
    SELECT cp.course_id
      FROM tree t
      JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
),
course_tasks AS (
    SELECT DISTINCT t.id, t.difficulty_id, t.task_content->>'type' AS task_type
      FROM tasks t JOIN tree ON tree.member_course_id = t.course_id
     WHERE COALESCE(t.is_active, true) AND t.requirement_level = ANY(:levels)
       AND {non_service_course_filter('t')}
       AND t.id <> ALL(CAST(:graced_tasks AS int[]))
),
course_materials AS (
    SELECT DISTINCT m.id
      FROM materials m JOIN tree ON tree.member_course_id = m.course_id
     WHERE COALESCE(m.is_active, true) AND m.requirement_level = ANY(:levels)
       AND {non_service_course_filter('m')}
       AND m.id <> ALL(CAST(:graced_materials AS int[]))
),
tasks_done AS (
    SELECT DISTINCT tr.task_id AS id
      FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
     WHERE tr.user_id = :student_id AND tr.is_correct = true
       AND tr.task_id IN (SELECT id FROM course_tasks)
    UNION
    SELECT stp.task_id
      FROM student_task_progress stp
     WHERE stp.student_id = :student_id AND stp.status = 'skipped'
       AND stp.task_id IN (SELECT id FROM course_tasks)
),
materials_done AS (
    SELECT DISTINCT smp.material_id AS id
      FROM student_material_progress smp
     WHERE smp.student_id = :student_id AND smp.status IN ('completed', 'skipped')
       AND smp.material_id IN (SELECT id FROM course_materials)
)
SELECT 'task' AS kind, ct.difficulty_id, ct.task_type, count(*) AS n
  FROM course_tasks ct
 WHERE ct.id NOT IN (SELECT id FROM tasks_done)
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'material', NULL::int, NULL::text, count(*)
  FROM course_materials cm
 WHERE cm.id NOT IN (SELECT id FROM materials_done)
"""

#: Сделанное ЗА НЕДЕЛЮ с разбивкой по роду элементов — для минутного темпа.
#: Условия те же, что в `_FACT_SQL` (ручные зачёты отсечены общим правилом);
#: разница только в группировке, поэтому недели без работы сюда не попадают —
#: нули берутся из `_FACT_SQL`, который отдаёт полный список окон.
_FACT_WEIGHTS_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE},
bounds AS (
    SELECT CAST(:since AS timestamptz)
             + CAST(idx || ' weeks' AS interval) AS starts_at,
           CAST(:since AS timestamptz)
             + CAST((idx + 1) || ' weeks' AS interval) AS ends_at
      FROM generate_series(0, :weeks - 1) AS idx
)
SELECT b.starts_at::date AS week, 'task' AS kind,
       t.difficulty_id, t.task_content->>'type' AS task_type,
       count(DISTINCT tr.task_id) AS n
  FROM bounds b
  JOIN task_results tr
    ON tr.user_id = :student_id AND tr.is_correct = true
   AND {real_student_results_filter('tr')}
   AND tr.submitted_at >= b.starts_at AND tr.submitted_at < b.ends_at
  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
  JOIN tasks t ON t.id = tr.task_id
 WHERE {non_service_course_filter('t')}
 GROUP BY 1, 2, 3, 4
UNION ALL
-- Типы в UNION обязаны совпадать явно: голый NULL Postgres считает text и
-- отказывается склеивать с integer-колонкой сложности.
SELECT b.starts_at::date, 'material', NULL::int, NULL::text,
       count(DISTINCT smp.material_id)
  FROM bounds b
  JOIN student_material_progress smp
    ON smp.student_id = :student_id AND smp.status = 'completed'
   AND smp.completed_at IS NOT NULL
   AND {real_student_material_filter('smp')}
   AND smp.completed_at >= b.starts_at AND smp.completed_at < b.ends_at
  JOIN materials m ON m.id = smp.material_id
 WHERE {non_service_course_filter('m')}
 GROUP BY 1, 2, 3, 4
"""


def _seconds_for_rows(rows: list[Any], table: EffortTable) -> Optional[float]:
    """Сумма веса строк «род × сложность × формат × сколько» в секундах.

    `None` — таблица весов пуста (мерить нечем). Ноль строк при живой таблице
    даёт 0.0, и это другое: «ничего не осталось» — законный ответ, а «нечем
    мерить» — отказ считать.
    """
    if table.overall is None:
        return None
    total = 0.0
    for row in rows:
        count = int(row["n"] or 0)
        if row["kind"] == "material":
            # tsk-904: измеренный вес теории. Заглушка занижала его вчетверо
            # (49 секунд против измеренных 176), и остаток программы в минутах
            # выходил меньше настоящего — а из него считается норма.
            total += table.material_effort_seconds() * count
            continue
        difficulty_id = row["difficulty_id"]
        seconds = table.seconds_for(
            difficulty_id=(
                None if difficulty_id is None else int(difficulty_id)
            ),
            task_type=row["task_type"],
        )
        # `seconds_for` отступает до общей медианы, а она у живой таблицы есть
        # всегда — None здесь означал бы, что таблица опустела между двумя
        # запросами. Считаем такой элемент по общей медиане, а не пропускаем:
        # пропуск занизил бы остаток молча.
        total += (seconds if seconds is not None else table.overall) * count
    return total


async def weighted_remaining_seconds(
    db: AsyncSession,
    *,
    student_id: int,
    root_ids: Optional[list[int]],
    table: EffortTable,
) -> Optional[float]:
    """Во сколько секунд работы оценивается непройденный остаток.

    `root_ids=None` — все курсы ученика; список — только эти корни (программа
    подготовки). `None` в ответе значит «мерить нечем», а не «ничего не
    осталось».
    """
    graced = await graced_for_roots(db, student_id, root_ids)
    rows = (
        await db.execute(
            text(_REMAINING_WEIGHTS_SQL),
            {
                "student_id": student_id,
                "root_ids": root_ids,
                "levels": list(REQUIREMENT_LEVELS),
                "graced_tasks": list(graced.tasks),
                "graced_materials": list(graced.materials),
            },
        )
    ).mappings().all()
    return _seconds_for_rows(list(rows), table)


async def _weekly_minutes(
    db: AsyncSession,
    *,
    student_id: int,
    since: datetime,
    weeks: int,
    weeks_order: list[Any],
    table: EffortTable,
) -> Optional[list[float]]:
    """Сколько минут работы пришлось на каждую неделю окна.

    Недели без работы обязаны остаться в списке нулями: медиана считается по
    ним же. Их порядок берётся из `weeks_order` — того самого списка окон, по
    которому считается штучный темп, иначе две медианы разъехались бы окнами.
    """
    if table.overall is None:
        return None
    rows = (
        await db.execute(
            text(_FACT_WEIGHTS_SQL),
            {"student_id": student_id, "since": since, "weeks": weeks},
        )
    ).mappings().all()

    by_week: dict[Any, list[Any]] = {}
    for row in rows:
        by_week.setdefault(row["week"], []).append(row)
    result: list[float] = []
    for week in weeks_order:
        seconds = _seconds_for_rows(by_week.get(week, []), table)
        result.append((seconds or 0.0) / 60)
    return result


#: Когда ученик впервые что-то сделал. Нужно, чтобы не мерить темп новичка по
#: неделям, которых у него ещё не было: медиана трёх недель у человека,
#: занимающегося три дня, — это медиана [0, 0, N], то есть ноль. На проде это
#: дало «делает 0» ученице, решившей 60 заданий за одно занятие (замер 07.09).
#: tsk-881: начало считается по УЧЕБНОЙ работе, служебные курсы не в счёт.
#: Иначе правка темпа выходит боком: человек в первую неделю проходит вводный
#: курс, во вторую берётся за предмет — и окно, отсчитанное от вводного,
#: включает неделю, где учебной работы не было вовсе. Медиана [0, N] даёт
#: половину настоящего темпа, а медиана [0, 0, N] — ноль. На боевых 10.09 у
#: Машталер выходило ровно это: недели [0, 0, 36] и темп «делает 0» при
#: тридцати шести сделанных пунктах.
_FIRST_ACTIVITY_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE}
SELECT least(
    (SELECT min(tr.submitted_at)
       FROM task_results tr
       JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
       JOIN tasks t ON t.id = tr.task_id
      WHERE tr.user_id = :student_id AND {real_student_results_filter('tr')}
        AND {non_service_course_filter('t')}),
    (SELECT min(smp.completed_at)
       FROM student_material_progress smp
       JOIN materials m ON m.id = smp.material_id
      WHERE smp.student_id = :student_id AND smp.completed_at IS NOT NULL
        AND {real_student_material_filter('smp')}
        AND {non_service_course_filter('m')})
) AS first_at
"""

#: Сколько из сделанного за окно пришлось НА ЗАНЯТИЕ, а не на дом. Занятием
#: считается работа в промежутке от начала урока до его конца: расписание —
#: единственный признак «урок идёт», который есть в данных.
#:
#: Зачем разделять (требование оператора 07.09): в сводке видно «делает N», но
#: без разбивки непонятно, работает человек сам или только под присмотром
#: преподавателя. Это разные выводы и разные действия.
_LESSON_WORK_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE},
lessons AS (
    SELECT lo.scheduled_at AS starts_at,
           lo.scheduled_at
             + CAST(COALESCE(lo.duration_minutes, 60) || ' minutes' AS interval)
             AS ends_at
      FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
     WHERE lop.student_id = :student_id
       AND lo.scheduled_at >= CAST(:since AS timestamptz) - interval '1 day'
)
SELECT count(DISTINCT tr.task_id) AS n
  FROM task_results tr
  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
  JOIN tasks t ON t.id = tr.task_id
 WHERE tr.user_id = :student_id AND tr.is_correct = true
   AND {real_student_results_filter('tr')}
   AND {non_service_course_filter('t')}
   AND tr.submitted_at >= :since
   AND EXISTS (
       SELECT 1 FROM lessons l
        WHERE tr.submitted_at BETWEEN l.starts_at AND l.ends_at
   )
"""



#: Сделанное в окнах СВОИХ посещённых часов, в разрезе рода элемента — вес
#: одного часа занятия для этого ученика (tsk-914). Тот же разрез, что у
#: `_FACT_WEIGHTS_SQL`: взвешивается той же таблицей.
_OWN_LESSON_WEIGHTS_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE},
lessons AS (
    SELECT lo.scheduled_at AS starts_at,
           lo.scheduled_at
             + CAST(COALESCE(lo.duration_minutes, 60) || ' minutes' AS interval) AS ends_at
      FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
     WHERE lop.student_id = :student_id AND lop.status = 'confirmed'
       AND lo.scheduled_at >= :since AND lo.scheduled_at <= :now
)
SELECT 'task' AS kind, t.difficulty_id, t.task_content->>'type' AS task_type,
       count(DISTINCT tr.task_id) AS n
  FROM task_results tr
  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
  JOIN tasks t ON t.id = tr.task_id
 WHERE tr.user_id = :student_id AND tr.is_correct = true
   AND {real_student_results_filter('tr')}
   AND {non_service_course_filter('t')}
   AND EXISTS (SELECT 1 FROM lessons l WHERE tr.submitted_at BETWEEN l.starts_at AND l.ends_at)
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'material', NULL::int, NULL::text, count(DISTINCT smp.material_id)
  FROM student_material_progress smp
  JOIN materials m ON m.id = smp.material_id
 WHERE smp.student_id = :student_id AND smp.status = 'completed'
   AND smp.completed_at IS NOT NULL
   AND {real_student_material_filter('smp')}
   AND {non_service_course_filter('m')}
   AND EXISTS (SELECT 1 FROM lessons l WHERE smp.completed_at BETWEEN l.starts_at AND l.ends_at)
"""

#: Доля верных за то же окно — поправка на качество.
_QUALITY_SQL = f"""
WITH RECURSIVE {SERVICE_COURSES_CTE}
SELECT count(*) AS total,
       count(*) FILTER (WHERE tr.is_correct) AS correct
  FROM task_results tr
  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
  JOIN tasks t ON t.id = tr.task_id
 WHERE tr.user_id = :student_id
   AND {real_student_results_filter('tr')}
   AND {non_service_course_filter('t')}
   AND tr.submitted_at >= :since
"""


def _lesson_norm_minutes(
    rows: list[Any], *, confirmed_hours: int, table: EffortTable
) -> Optional[int]:
    """Вес одного посещённого часа в минутах работы; None — вес не измерен.

    Своих посещённых часов в окне нет — берём школьную заглушку
    `LESSON_NORM_FALLBACK_MINUTES`: нагонять новичку всё равно есть что.
    """
    if table.overall is None:
        return None
    if confirmed_hours <= 0:
        return LESSON_NORM_FALLBACK_MINUTES
    seconds = _seconds_for_rows(list(rows), table) or 0.0
    per_hour = seconds / 60 / confirmed_hours
    # Час без единой сдачи в окне (преподаватель объяснял у доски) не должен
    # обнулять нагон: ниже заглушки не опускаемся.
    return max(int(round(per_hour)), 1) if per_hour >= 1 else LESSON_NORM_FALLBACK_MINUTES


async def compute(
    db: AsyncSession, *, student_id: int, now: Optional[datetime] = None
) -> VolumePlan:
    """Посчитать норму домашней работы для ученика.

    Только чтение: ничего не пишет и не выдаёт — выдачей занимается
    `homework_service`. Отдельная функция затем, чтобы норму можно было
    показать преподавателю и проверить, не задавая ничего.

    Args:
        db: async session.
        student_id: ID ученика.
        now: момент расчёта (для тестов); по умолчанию — сейчас.

    Returns:
        `VolumePlan` — норма и всё, из чего она сложилась.
    """
    moment = now or datetime.now(timezone.utc)

    # Окно темпа не может быть длиннее, чем ученик вообще с нами (tsk-798,
    # замер 07.09): медиана трёх недель у человека, занимающегося три дня, —
    # это медиана [0, 0, N], то есть ноль. На проде так и вышло: ученица
    # решила 60 заданий за одно занятие, а в сводке стояло «делает 0», и
    # выдача ей считалась как человеку с нулевым темпом.
    first_at = (
        await db.execute(text(_FIRST_ACTIVITY_SQL), {"student_id": student_id})
    ).scalar()
    weeks_window = FACT_WEEKS
    if first_at is not None:
        days = max((moment - first_at).days, 0)
        weeks_window = max(1, min(FACT_WEEKS, -(-days // 7) or 1))
    # Окна по семь дней ровно замощают [since, moment]: последнее кончается
    # моментом расчёта, а не прошлым воскресеньем (tsk-819).
    since = moment - timedelta(weeks=weeks_window)

    grade = (
        await db.execute(
            text("SELECT school_grade FROM users WHERE id = :uid"), {"uid": student_id}
        )
    ).scalar()
    # tsk-912: прощённое по всем корням ученика — один раз на расчёт; кеш
    # правила живёт в сессии, повторные вызовы ниже его не пересчитывают.
    graced_all = await graced_for_roots(db, student_id)

    totals = (
        await db.execute(
            text(_REMAINING_SQL),
            {
                "student_id": student_id,
                "levels": list(REQUIREMENT_LEVELS),
                "graced_tasks": list(graced_all.tasks),
                "graced_materials": list(graced_all.materials),
            },
        )
    ).mappings().one()
    # Остаток ВСЕХ курсов ученика — потолок выдачи: больше, чем осталось, не
    # задашь. Норму считает программа подготовки (ниже), и это разные числа:
    # у ученика бывают курсы вне программы.
    remaining = max(int(totals["total_items"]) - int(totals["done_items"]), 0)

    weekly_rows = (
        await db.execute(
            text(_FACT_SQL),
            {"student_id": student_id, "since": since, "weeks": weeks_window},
        )
    ).mappings().all()
    weekly = [int(r["done"]) for r in weekly_rows]
    weeks_order = [r["week"] for r in weekly_rows]
    fact_per_week = float(statistics.median(weekly)) if weekly else 0.0

    # tsk-867: тот же темп, измеренный в минутах работы. Таблица весов
    # снимается ОДИН раз на расчёт: внутри неё LATERAL по всем сдачам окна,
    # и снимать её на каждый элемент значило бы считать телеметрию заново.
    effort_table = await load_effort_table(db)
    effort_measured = effort_table.overall is not None
    weekly_minutes = await _weekly_minutes(
        db,
        student_id=student_id,
        since=since,
        weeks=weeks_window,
        weeks_order=weeks_order,
        table=effort_table,
    )
    fact_minutes = (
        float(statistics.median(weekly_minutes))
        if weekly_minutes
        else (0.0 if effort_measured else None)
    )

    # Сколько из этого сделано НА ЗАНЯТИИ (требование оператора 07.09).
    # Работа на уроке в темп входила и раньше — это обычные сдачи, — но в
    # сводке была неотличима от домашней, и «делает N» нельзя было прочитать:
    # человек работает сам или только под присмотром преподавателя.
    lesson_done = int(
        (
            await db.execute(
                text(_LESSON_WORK_SQL), {"student_id": student_id, "since": since}
            )
        ).scalar()
        or 0
    )
    total_done = sum(weekly)
    lesson_share = (
        round(lesson_done / total_done, 2) if total_done > 0 else None
    )

    quality = (
        await db.execute(
            text(_QUALITY_SQL), {"student_id": student_id, "since": since}
        )
    ).mappings().one()

    # tsk-914: пропуски по неделям — план из расписания против отработанного.
    # Предикат общий (`attendance_service`): им же считают серию пропусков в
    # сводке и посещаемость на дашборде.
    attendance = (
        await attendance_service.weekly(
            db, student_ids=[student_id], since=since, until=moment
        )
    ).get(student_id, [])
    missed_lessons, missed_unpaid = attendance_service.totals(attendance)
    lesson_rows = (
        await db.execute(
            text(_OWN_LESSON_WEIGHTS_SQL),
            {"student_id": student_id, "since": since, "now": moment},
        )
    ).mappings().all()
    confirmed_hours = sum(w.attended_own for w in attendance)
    lesson_norm_minutes = _lesson_norm_minutes(
        lesson_rows, confirmed_hours=confirmed_hours, table=effort_table
    )
    total_submissions = int(quality["total"] or 0)
    correct_ratio = (
        int(quality["correct"] or 0) / total_submissions
        if total_submissions >= MIN_QUALITY_SAMPLE
        else None
    )

    exam_day = exam_date_for(grade, moment.date())
    weeks_to_exam = max((exam_day - moment.date()).days, 1) / 7.0

    # Персональная норма: личный остаток программы, делённый на недели до
    # срока. Раньше здесь стояла норма класса — одна на всех одиннадцати-
    # классников, независимо от того, прошёл человек половину курса или не
    # начинал (замечание оператора 04.09).
    program = await program_for_student(
        db, student_id=student_id, grade=grade, today=moment.date()
    )
    sprint = False
    early_target = summer_target = None
    early_day = summer_day = None
    # Вес подрезанной программы в минутах; None — вне программы или вес не
    # измерен, тогда минутный норматив падает на полный остаток (как до 09.09).
    scoped_minutes: Optional[float] = None
    if program is not None:
        remaining = program["remaining"]
        days_left = (program["deadline"] - moment.date()).days

        # tsk-869: норматив считается от ПОДРЕЗАННОЙ программы, а не от полного
        # остатка. Объём под срок и темп режет `program_scope_service`
        # (tsk-798), норму считает этот модуль (tsk-797) — до 09.09 они не
        # были связаны, и система требовала того, что сама же отменила: у Крук
        # «нужно 41 в неделю» при программе, спланированной на 30. Разрыв в 11
        # преподаватель читал как «не дотягивает», хотя ученица шла по плану.
        # Расхождение было у 40 учеников из 88, у половины — вдвое.
        remaining, scoped_minutes = await _scoped_remaining(
            db,
            student_id=student_id,
            program=program,
            fact_per_week=fact_per_week,
            today=moment.date(),
            # Вес и минутный темп передаются готовыми: без них `compute_scope`
            # не грузит таблицу весов вовсе и возвращает минуты пустыми — тогда
            # минутный норматив молча падал бы на полный остаток, то есть
            # дефект остался бы жив в одной из двух единиц (замечено на проде
            # сразу после выката: штучный стал 30, минутный остался 156).
            effort_table=effort_table if effort_measured else None,
            fact_minutes_per_week=fact_minutes if effort_measured else None,
        )

        # Не выпускник — у него есть выбор, которого нет у одиннадцати-
        # классника: закончить программу за этот учебный год или прихватить
        # лето, а весь выпускной год отдать вариантам. Одна цифра «18 в неделю
        # до марта 2028» этот выбор прячет, и разговор о летних занятиях
        # опереть не на что (требование оператора 07.09).
        if (program["deadline"] - moment.date()).days > 400:
            from app.core import settings_store

            early_day = _deadline_for(
                settings_store.get_str("homework_program_early_finish"),
                date(moment.year + 1, EXAM_MONTH, EXAM_DAY),
                (5, 31),
            )
            summer_day = _deadline_for(
                settings_store.get_str("homework_program_summer_finish"),
                date(moment.year + 1, EXAM_MONTH, EXAM_DAY),
                (8, 31),
            )
            early_target = _weekly_for(remaining, early_day, moment.date())
            summer_target = _weekly_for(remaining, summer_day, moment.date())

        if days_left > 0:
            target = max(int(-(-remaining // max(days_left / 7.0, 1e-9))), 0)
        else:
            # Срок программы прошёл: гнать по ней больше некуда, дальше идёт
            # отработка вариантов.
            target = TARGET_PER_WEEK_EXAM_SPRINT
            sprint = True
    else:
        target = target_per_week_for(grade, moment.date())
        sprint = target == TARGET_PER_WEEK_EXAM_SPRINT and (
            grade is None or grade >= 11
        )

    # tsk-867: то же самое в минутах работы. Остаток взвешивается по ТЕМ ЖЕ
    # корням, по которым посчитан штучный: программа задаёт норму, все курсы
    # ученика — потолок «больше, чем осталось, не задашь».
    remaining_seconds = (
        await weighted_remaining_seconds(
            db,
            student_id=student_id,
            root_ids=(program["root_ids"] if program is not None else None),
            table=effort_table,
        )
        if effort_measured
        else None
    )
    remaining_minutes = (
        None if remaining_seconds is None else remaining_seconds / 60
    )
    target_minutes: Optional[float] = None
    early_target_minutes: Optional[int] = None
    summer_target_minutes: Optional[int] = None
    if effort_measured:
        if program is not None and remaining_minutes is not None:
            days_left = (program["deadline"] - moment.date()).days
            if days_left > 0:
                # tsk-869: как и штучный, минутный норматив считается от
                # ПОДРЕЗАННОЙ программы. Полный вес остатка (`remaining_minutes`)
                # остаётся потолком выдачи ниже — «больше, чем осталось, не
                # задашь», — но требовать по нему нельзя: этот объём ученику
                # никто выдавать не собирается.
                target_minutes = (
                    scoped_minutes if scoped_minutes is not None else remaining_minutes
                ) / max(days_left / 7.0, 1e-9)
                # tsk-922: ранние сроки — в той же единице и от того же
                # остатка, что и основная норма. У родителя строка «можно
                # закончить раньше: 26 в неделю» стояла рядом с «нужно 51 мин
                # в неделю» — штуки и минуты в одной карточке (Курунов, 12.09).
                scoped_or_full = (
                    scoped_minutes if scoped_minutes is not None else remaining_minutes
                )
                if early_day is not None:
                    early_days = (early_day - moment.date()).days
                    early_target_minutes = int(round(
                        scoped_or_full / max(early_days / 7.0, 1e-9)
                        if early_days > 0 else scoped_or_full
                    ))
                if summer_day is not None:
                    summer_days = (summer_day - moment.date()).days
                    summer_target_minutes = int(round(
                        scoped_or_full / max(summer_days / 7.0, 1e-9)
                        if summer_days > 0 else scoped_or_full
                    ))
            else:
                # Срок программы прошёл — дальше отработка вариантов, и норма
                # та же, что в штучном расчёте, только в своей единице.
                target_minutes = float(TARGET_MINUTES_EXAM_SPRINT)
        else:
            target_minutes = float(target_minutes_for(grade, moment.date()))

    # tsk-896: успевает ли человек к сроку. От этого зависят два правила ниже —
    # шаг роста и вычет урочной работы, — и оба должны смотреть на ОДНУ шкалу.
    # Ведущая с tsk-867 минутная; штук хватает, только когда веса нет.
    if target_minutes is not None and fact_minutes is not None:
        on_track = fact_minutes >= target_minutes
    else:
        on_track = fact_per_week >= target

    #: Растим не быстрее, чем на GROWTH_FACTOR от нынешнего темпа, но не ниже
    #: минимума: у человека с нулевым темпом факт×1.2 = 0, и без пола он не
    #: получил бы ничего — то есть механика молчала бы ровно там, где она
    #: нужнее всего (18 из 60 за месяц не решили ни одного задания).
    #:
    #: tsk-896: тому, кто не успевает, шаг больше (`BEHIND_GROWTH_FACTOR`).
    #: tsk-909: и не ниже половины нормы — см. `TARGET_FLOOR_SHARE`.
    growth = GROWTH_FACTOR if on_track else BEHIND_GROWTH_FACTOR
    raw = min(
        float(target),
        max(
            fact_per_week * growth,
            target * TARGET_FLOOR_SHARE,
            float(MIN_PER_WEEK),
        ),
    )
    # Штраф за качество идёт ПОСЛЕ пола и потому сильнее его: пол защищает от
    # «задали полтора процента недели», а штраф — сознательное снижение тому,
    # кто гонит с ошибками, и отменять его полом значило бы отменить его вовсе.
    penalty = correct_ratio is not None and correct_ratio < QUALITY_THRESHOLD
    if penalty:
        raw *= QUALITY_PENALTY

    ceiling = ceiling_for(fact_per_week, on_track=on_track)
    volume = int(round(max(min(raw, float(ceiling)), float(MIN_PER_WEEK))))

    # Пропустил занятия — материал, который разбирали без него, придётся
    # пройти самому (требование оператора 02.09). Нагон применяется ПОСЛЕ
    # ограничения «не больше, чем человек тянет»: то ограничение защищает от
    # перегруза в обычной жизни, а здесь мы сознательно просим больше — но не
    # вдвое, а в полтора раза максимум.
    #
    # tsk-914: за КАЖДЫЙ непогашенный пропуск добавляется вес одного часа
    # занятия — столько работы и не случилось. Штучная шкала — ограждение,
    # и час здесь переводится в элементы по общей медиане веса.
    catch_up_items = 0
    if missed_unpaid > 0 and lesson_norm_minutes is not None:
        per_item_minutes = (effort_table.overall or 0.0) / 60 or 1.0
        catch_up_items = int(round(
            missed_unpaid * lesson_norm_minutes / per_item_minutes
        ))
    elif missed_unpaid > 0:
        catch_up_items = missed_unpaid * MIN_PER_WEEK
    volume_before_catch_up = volume
    if catch_up_items > 0:
        volume = int(round(min(
            volume + catch_up_items,
            volume * MAX_CATCH_UP_FACTOR,
            float(ceiling),
        )))
    catch_up = (
        round(volume / volume_before_catch_up, 2) if volume_before_catch_up > 0 else 1.0
    )

    # Больше, чем осталось в программе, задать нельзя — иначе выдача попросит
    # то, чего нет, и пункты в ней окажутся невыполнимыми.
    volume = min(volume, remaining)

    # tsk-867: та же формула в минутах — шаг в шаг со штучной, чтобы норма не
    # зависела от того, чем её меряют. Ведёт минутная; штучная остаётся
    # ограждением при наборе состава (см. `homework_service._next_items`).
    minutes_volume: Optional[int] = None
    catch_up_minutes = 0
    if effort_measured and target_minutes is not None and fact_minutes is not None:
        # tsk-896: ДОМА нужно не всё, что нужно за неделю, — часть человек
        # закрывает на занятии. Замечание оператора 10.09: «у большинства два
        # занятия, они перекрывают это время, зачем ДЗ?». Норма считалась из
        # ВСЕЙ недельной работы, а задавалась целиком на дом.
        #
        # Вычитаем ТОЛЬКО у того, кто успевает (решение оператора): у
        # отстающего урок и так не вытягивает срок, и урезать ему дом значило
        # бы закрепить отставание. Доля берётся из прошлых недель
        # (`lesson_share`) — другого способа предсказать урок нет.
        home_target = target_minutes
        if on_track and lesson_share is not None and lesson_share > 0:
            home_target = max(
                target_minutes * (1.0 - lesson_share), float(MIN_MINUTES_PER_WEEK)
            )
        # tsk-909: пол — от ДОМАШНЕЙ нормы, а не от недельной: у того, кто
        # успевает, часть недели закрывается на занятии, и половина от целого
        # была бы для него не полом, а надбавкой поверх вычета.
        raw_minutes = min(
            home_target,
            max(
                fact_minutes * growth,
                home_target * TARGET_FLOOR_SHARE,
                float(MIN_MINUTES_PER_WEEK),
            ),
        )
        if penalty:
            raw_minutes *= QUALITY_PENALTY
        minutes_ceiling = minutes_ceiling_for(fact_minutes, on_track=on_track)
        minutes_volume = int(round(max(
            min(raw_minutes, float(minutes_ceiling)),
            float(MIN_MINUTES_PER_WEEK),
        )))
        # tsk-914: минутная шкала ведущая — здесь нагон и есть «вес часа за
        # каждый непогашенный пропуск», с теми же двумя потолками.
        base_minutes = minutes_volume
        if missed_unpaid > 0 and lesson_norm_minutes is not None:
            wanted = missed_unpaid * lesson_norm_minutes
            minutes_volume = int(round(min(
                minutes_volume + wanted,
                minutes_volume * MAX_CATCH_UP_FACTOR,
                float(minutes_ceiling),
            )))
        # Больше остатка не задать — и нагон не исключение: у того, кому
        # осталось ноль, нагонять нечего, сколько бы он ни пропустил.
        if remaining_minutes is not None:
            cap = int(round(remaining_minutes))
            minutes_volume = min(minutes_volume, cap)
            base_minutes = min(base_minutes, cap)
        catch_up_minutes = max(minutes_volume - base_minutes, 0)
        catch_up = (
            round(minutes_volume / base_minutes, 2) if base_minutes > 0 else 1.0
        )

    #: Насколько человек не дотягивает до нормы своего класса. Считается по
    #: ФАКТУ, а не по выданному объёму: объём — это то, что мы задали, а
    #: отставание — то, что человек делает на самом деле.
    pace_gap = max(int(round(target - fact_per_week)), 0) if remaining > 0 else 0

    #: Насколько хватит программы при нынешней норме. Считается по НОРМЕ, а не
    #: по факту: вопрос «когда ученику станет нечего задавать», а не «когда он
    #: всё пройдёт».
    #:
    #: tsk-867: в минутах, когда вес измерен. Штучное «1342 элемента при норме
    #: 12 — на 111 недель» и минутное «82 часа при 75 минутах — на 66 недель»
    #: расходятся именно потому, что элементы разновесные; верно второе.
    if (
        minutes_volume is not None
        and minutes_volume > 0
        and remaining_minutes is not None
    ):
        weeks_left = int(remaining_minutes // minutes_volume)
    else:
        weeks_left = int(remaining // volume) if volume > 0 else None
    needs_more = remaining == 0 or (
        weeks_left is not None and weeks_left < PROGRAM_LOW_WEEKS
    )

    return VolumePlan(
        grade=int(grade) if grade is not None else None,
        grade_assumed=grade is None,
        exam_date=exam_day,
        weeks_to_exam=round(weeks_to_exam, 1),
        remaining_items=remaining,
        target_per_week=target,
        fact_per_week=round(fact_per_week, 1),
        correct_ratio=round(correct_ratio, 2) if correct_ratio is not None else None,
        quality_penalty_applied=penalty,
        volume_per_week=volume,
        program_kind=(program or {}).get("kind"),
        program_deadline=(program or {}).get("deadline"),
        program_tasks_remaining=(program or {}).get("tasks_remaining"),
        missed_lessons=missed_lessons,
        catch_up_factor=round(catch_up, 2),
        weeks_of_program_left=weeks_left,
        needs_more_program=needs_more,
        exam_sprint=sprint,
        # tsk-896: по той шкале, по которой считается сама норма. Штучная с
        # tsk-867 осталась ограждением, и признак на ней расходился с делом: на
        # проде 10.09 у 29 учеников из 84 стояло «программа не помещается»,
        # хотя по минутам она помещалась у всех двадцати девяти. Оператор
        # заметил это на паре Якунина/Редько: у неё признак был, у него нет,
        # притом что минутные числа почти совпадают.
        target_unreachable=(
            target_minutes > minutes_ceiling_for(fact_minutes, on_track=on_track)
            if (effort_measured and target_minutes is not None
                and fact_minutes is not None)
            else target > ceiling
        ),
        lesson_share=lesson_share,
        fact_weeks_used=weeks_window,
        early_target_per_week=early_target,
        summer_target_per_week=summer_target,
        early_target_minutes_per_week=early_target_minutes,
        summer_target_minutes_per_week=summer_target_minutes,
        early_deadline=early_day,
        summer_deadline=summer_day,
        pace_gap=pace_gap,
        effort_measured=effort_measured,
        minutes_per_week=minutes_volume,
        target_minutes_per_week=(
            int(round(target_minutes)) if target_minutes is not None else None
        ),
        fact_minutes_per_week=(
            round(fact_minutes, 1) if fact_minutes is not None else None
        ),
        remaining_minutes=(
            int(round(remaining_minutes)) if remaining_minutes is not None else None
        ),
        missed_unpaid=missed_unpaid,
        lesson_norm_minutes=lesson_norm_minutes,
        catch_up_minutes=catch_up_minutes,
    )


def volume_for_window(plan: VolumePlan, *, days: int) -> int:
    """Сколько элементов задать на промежуток в `days` дней.

    Выдача привязана не к неделе, а к следующему занятию: между занятиями
    может быть и три дня, и десять (у ученика с одним занятием в неделю и у
    ученика с двумя разный промежуток). Неделя — только единица нормы.

    Пол в один элемент: если до занятия остался день, задать «ноль» нельзя —
    выдача без состава бессмысленна.
    """
    if plan.volume_per_week <= 0:
        return 0
    scaled = plan.volume_per_week * max(days, 1) / 7.0
    return max(int(round(scaled)), 1)


def minutes_for_window(plan: VolumePlan, *, days: int) -> Optional[int]:
    """Бюджет времени на промежуток в `days` дней, в минутах.

    `None` — вес не измерен: бюджета времени нет, и набирать состав придётся
    по штукам (`volume_for_window`). Пола в одну минуту здесь нет намеренно:
    пол выдачи — это «хотя бы один элемент», и он живёт там, где собирается
    состав, а не в переводе недельной нормы в промежуток.
    """
    if plan.minutes_per_week is None or plan.minutes_per_week <= 0:
        return None
    return max(int(round(plan.minutes_per_week * max(days, 1) / 7.0)), 1)
