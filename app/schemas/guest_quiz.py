"""Схемы гостевого квиза-лид-магнита (tsk-053, фаза 1).

Отдельный контракт, а не расширение `learning_guest`: там смысл «решить демо-задачу
и узнать, верно ли», здесь — «пройти опрос и получить рекомендацию». У квиз-вопроса
верного варианта нет вовсе (ADR-0003), поэтому `is_correct`/`score` в ответах квиза
не участвуют — они бы врали.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class QuizOption(BaseModel):
    """Вариант ответа. Баллы по шкалам наружу не отдаются — это внутренняя механика подбора."""

    id: str = Field(..., description="Устойчивый ID варианта")
    text: str = Field(..., description="Текст варианта")


class QuizQuestion(BaseModel):
    """Вопрос квиза с уже отмеченным ответом, если гость на него отвечал."""

    task_id: int
    order: int = Field(..., description="Порядковый номер вопроса, начиная с 1")
    type: Literal["SC_Qw", "MC_Qw"] = Field(..., description="Одиночный или множественный выбор")
    stem: str = Field(..., description="Текст вопроса")
    options: List[QuizOption]
    selected_option_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Что гость выбрал ранее. Не null — вопрос уже отвечен; ответ можно "
            "поменять, опрос не экзамен"
        ),
    )


class QuizResponse(BaseModel):
    """Ответ на GET /learning/guest/quiz/{course_uid}."""

    course_uid: str
    title: str
    description: Optional[str] = None
    questions: List[QuizQuestion]
    answered_count: int = Field(..., description="Сколько вопросов уже отвечено этой гостевой сессией")
    total_count: int
    is_complete: bool = Field(..., description="True — отвечены все вопросы, можно показывать итог")


class QuizAnswerRequest(BaseModel):
    """Тело POST /learning/guest/quiz/answers."""

    task_id: int = Field(..., description="ID вопроса квиза")
    selected_option_ids: List[str] = Field(
        ...,
        min_length=1,
        description="Выбранные варианты: ровно один для SC_Qw, один и более для MC_Qw",
    )


class QuizAnswerResponse(BaseModel):
    """Ответ на POST /learning/guest/quiz/answers — прогресс, а не «верно/неверно»."""

    task_id: int
    answered_count: int
    total_count: int
    is_complete: bool


class QuizRecommendation(BaseModel):
    """Программа, подобранная по итогам квиза."""

    course_uid: str
    title: str
    description: Optional[str] = None


class QuizResultResponse(BaseModel):
    """Ответ на GET /learning/guest/quiz/{course_uid}/result.

    `recommendation = null` — законный исход, а не ошибка: правило маршрутизации
    может не сработать (например, шкалы сравнялись). Тогда экран честно зовёт
    разобраться в переписке, а не подсовывает случайную программу.
    """

    course_uid: str
    title: str
    is_complete: bool
    answered_count: int
    total_count: int
    scales: Dict[str, int] = Field(
        default_factory=dict, description="Накопленные баллы по шкалам этой гостевой сессии"
    )
    recommendation: Optional[QuizRecommendation] = None
    contact_url: str = Field(..., description="Ссылка на переписку с уже заполненным сообщением")
    lead_submitted: bool = Field(
        ..., description="True — контакт из этой гостевой сессии уже оставлен"
    )

    model_config = ConfigDict(from_attributes=True)


class QuizLeadRequest(BaseModel):
    """Тело POST /learning/guest/quiz/{course_uid}/lead."""

    contact: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="Телефон, почта или ник — как человеку удобнее; формат не навязываем",
    )
    full_name: Optional[str] = Field(
        default=None, max_length=200, description="Имя, если человек его указал"
    )


class QuizLeadResponse(BaseModel):
    """Ответ на POST /learning/guest/quiz/{course_uid}/lead."""

    lead_id: int
    already_submitted: bool = Field(
        ...,
        description=(
            "True — контакт из этой сессии уже был оставлен, повторная отправка "
            "обновила существующую заявку, а не завела вторую"
        ),
    )
