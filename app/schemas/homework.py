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
            "Целевая недельная норма этого класса: 11 — 20, 10 и 9 — 12, "
            "младше — 8. Именно через неё класс влияет на нагрузку"
        )
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
    exam_sprint: bool = Field(
        description=(
            "Норма снижена: с марта выпускной класс отрабатывает варианты "
            "(1-2 в неделю), и на обычное ДЗ времени почти не остаётся"
        )
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
