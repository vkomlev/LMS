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
            "Из чего сложилась норма: надо/факт/качество/класс/недель до "
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
    remaining_items: int = Field(description="Незавершённых элементов программы")
    need_per_week: float = Field(description="Сколько нужно в неделю, чтобы успеть")
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
    weeks_behind: int = Field(
        description=(
            "На сколько недель программа опаздывает при НЫНЕШНЕМ темпе; "
            "0 — успевает. Считается по факту, а не по норме"
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
