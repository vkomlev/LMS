"""Схемы домашней работы (tsk-741, фаза 3)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HomeworkItemRead(BaseModel):
    """Один элемент выдачи с отметкой выполнения."""

    kind: Literal["task", "material"] = Field(
        description="task — задание, material — теория (её тоже учат дома)"
    )
    item_id: int = Field(description="ID задания или материала")
    course_id: Optional[int] = Field(
        default=None, description="Узел курса, которому принадлежит элемент"
    )
    title: Optional[str] = None
    done: bool = Field(
        description=(
            "Выполнено. Считается у источника: задание — есть верный "
            "результат (в том числе ручной зачёт преподавателя: он закрыл "
            "задание сам), материал — есть отметка прохождения. Отдельной "
            "отметки «сделал домашку» в базе нет намеренно. В расчёт ТЕМПА "
            "ручные зачёты, наоборот, не идут — это разные вопросы"
        )
    )
    position: int = Field(description="Порядок в выдаче — учебный")


class HomeworkRead(BaseModel):
    """Действующая домашняя работа ученика."""

    id: int
    student_id: int
    issued_at: datetime
    due_at: datetime = Field(description="Срок — обычно начало следующего занятия")
    source: Literal["auto", "teacher"]
    issued_by: Optional[int] = None
    occurrence_id: Optional[int] = None
    planned_volume: int = Field(
        description="Норма формулы на момент выдачи — снимок, не пересчитывается"
    )
    volume_details: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Из чего сложилась норма: цель класса, факт, качество, недель до "
            "экзамена. Нужен, чтобы объяснить человеку конкретное число"
        ),
    )
    note: Optional[str] = None
    items: list[HomeworkItemRead] = Field(default_factory=list)
    total: int
    done: int
    is_overdue: bool = Field(
        description="Срок прошёл, а сделано не всё. Ничего не блокирует — "
        "решение оператора 01.09: невыполненное ДЗ это показатель, а не долг"
    )


class HomeworkVolumeRead(BaseModel):
    """Норма домашней работы и всё, из чего она сложилась (без выдачи)."""

    grade: Optional[int] = Field(default=None, description="Класс ученика")
    grade_assumed: bool = Field(
        description="Класс не указан — считали по 11 (решение оператора 01.09)"
    )
    exam_date: str = Field(description="Дата ближайшего экзамена для этого класса")
    weeks_to_exam: float
    remaining_items: int = Field(
        description=(
            "Незавершённых элементов программы. Не знаменатель нормы — курс "
            "это банк заданий, а не конечная программа, — а потолок: больше, "
            "чем осталось, задать нельзя"
        )
    )
    target_per_week: int = Field(
        description=(
            "Сколько нужно в неделю ЭТОМУ ученику, чтобы закончить программу "
            "к сроку: его личный остаток обязательных элементов, делённый на "
            "оставшиеся недели. Может быть больше потолка выдачи — это правда "
            "о разрыве, и она показывается преподавателю как есть. Ученику "
            "число не показывается. Если ученик не записан ни на одну "
            "программу подготовки, берётся прежняя норма по классу"
        )
    )
    program_kind: Optional[str] = Field(
        default=None,
        description="ege | oge | null — ученик вне программ подготовки",
    )
    program_deadline: Optional[str] = Field(
        default=None,
        description=(
            "К какому дню программу нужно закончить. Год — по классу ученика: "
            "одиннадцатикласснику ближайший, десятикласснику следующий"
        ),
    )
    program_tasks_remaining: Optional[int] = Field(
        default=None,
        description=(
            "Обязательных ЗАДАНИЙ программы осталось. Материалы считаются "
            "отдельно и входят в общий остаток"
        ),
    )
    fact_per_week: float = Field(
        description="Сколько человек делает сейчас — медиана за 3 недели"
    )
    correct_ratio: Optional[float] = Field(
        default=None, description="Доля верных сдач; null — сдач слишком мало"
    )
    quality_penalty_applied: bool = Field(
        description="Объём уменьшен на четверть: доля верных ниже 60%"
    )
    volume_per_week: int = Field(description="Итоговая норма на неделю")
    weeks_of_program_left: Optional[int] = Field(
        default=None,
        description=(
            "На сколько недель хватит остатка программы при этой норме; "
            "null — задавать нечего. Идущего с опережением видно ЗАРАНЕЕ, а не "
            "в день, когда ему стало нечего задать"
        ),
    )
    needs_more_program: bool = Field(
        description=(
            "Программы осталось меньше чем на 4 недели (или её нет вовсе) — "
            "пора добавить ученику курс"
        )
    )
    missed_lessons: int = Field(
        description=(
            "Занятий пропущено за окно расчёта. Перенесённые сюда НЕ входят: "
            "перенос состоится, нагонять нечего"
        )
    )
    catch_up_factor: float = Field(
        description=(
            "Во сколько раз объём увеличен, чтобы нагнать пропущенное; "
            "1.0 — не увеличен. Потолок 1.5: пропустивший чаще всего и есть "
            "отстающий, и удвоенная выдача для него — повод бросить"
        )
    )
    exam_sprint: bool = Field(
        description=(
            "Норма снижена: с марта выпускной класс отрабатывает варианты "
            "(1-2 в неделю), и на обычное ДЗ времени почти не остаётся"
        )
    )
    program_core_total: Optional[int] = Field(
        default=None,
        description=(
            "Несокращаемых элементов программы осталось: теория, все номера "
            "ЕГЭ, материалы. Проходится целиком — выбросить оттуда что-то "
            "значит не пройти номер вовсе"
        ),
    )
    program_drill_total: Optional[int] = Field(
        default=None,
        description="Заданий отработки (лёгкие и средние) осталось всего",
    )
    program_drill_allowed: Optional[int] = Field(
        default=None,
        description=(
            "Сколько отработки помещается ученику до срока при его темпе. "
            "Меньше `program_drill_total` — значит программа подобрана под "
            "него: ядро целиком, отработка частью"
        ),
    )
    program_core_trimmed: bool = Field(
        default=False,
        description=(
            "Бюджета не хватает даже на ядро — программа сокращена по номерам "
            "ЕГЭ. Пустой `program_dropped_courses` при этом значит, что резать "
            "было нечего: приоритеты номеров не размечены, и система не стала "
            "решать за методиста"
        ),
    )
    program_dropped_courses: list[str] = Field(
        default_factory=list,
        description=(
            "Названия номеров, выпавших из программы этого ученика. Выпадает "
            "номер ЦЕЛИКОМ — с теорией и отработкой: половина разбора не "
            "готовит ни к чему"
        ),
    )
    target_unreachable: bool = Field(
        default=False,
        description=(
            "Нужная скорость выше потолка выдачи — программа в оставшийся "
            "срок не помещается физически. Утверждение про ПРОГРАММУ И СРОК, "
            "а не про ученика: решается сужением состава обязательного или "
            "сдвигом срока, а не давлением на человека"
        ),
    )
    pace_gap: int = Field(
        description=(
            "На сколько элементов в неделю человек не дотягивает до нормы "
            "своего класса; 0 — дотягивает. Считается по факту, а не по "
            "выданному объёму"
        )
    )


class HomeworkIssueRequest(BaseModel):
    """Тело выдачи домашней работы преподавателем."""

    due_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Срок; должен быть в будущем. Не передан — сервер берёт начало "
            "СЛЕДУЮЩЕГО занятия ученика, а если занятий в расписании нет — "
            "неделю. Тот же срок, что у автоматической выдачи: два разных "
            "ответа на «до когда» означали бы, что преподаватель и система "
            "задают на разные сроки"
        ),
    )
    volume: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "Сколько элементов задать вместо расчёта формулы. Норма всё равно "
            "считается и сохраняется — иначе потом не понять, от чего отступили"
        ),
    )
    note: Optional[str] = Field(
        default=None, max_length=500, description="Комментарий ученику"
    )
