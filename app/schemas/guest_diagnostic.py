"""Схемы гостевой диагностики-зондов (tsk-053, фазы 2-3).

Отдельный контракт от квиза (`guest_quiz`), хотя поверхность похожа. Разница
принципиальная: в квизе верного ответа нет вовсе и подбор идёт по шкалам
предпочтений; здесь у каждой задачи есть эталон, и итог — карта тем, где человек
справился, а где нет.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticQuestion(BaseModel):
    """Задача-зонд. Эталон и разбор наружу не отдаются — их человек увидит в итоге."""

    task_id: int
    order: int = Field(..., description="Порядковый номер, начиная с 1")
    topic_code: str = Field(..., description="Код темы магнита, например z14 или b3")
    topic_title: str = Field(..., description="Название темы для экрана")
    stem: str
    answered: bool = Field(..., description="Отвечал ли уже на неё этот посетитель")
    answer_value: Optional[str] = Field(
        default=None, description="Что человек ответил ранее — чтобы вернуться и поправить"
    )


class DiagnosticResponse(BaseModel):
    """Ответ на GET /learning/guest/diagnostic/{course_uid}."""

    course_uid: str
    title: str
    description: Optional[str] = None
    questions: List[DiagnosticQuestion]
    answered_count: int
    total_count: int
    is_complete: bool


class DiagnosticAnswerRequest(BaseModel):
    """Тело POST /learning/guest/diagnostic/answers."""

    task_id: int
    value: str = Field(
        ..., max_length=200, description="Краткий ответ: число или одно слово"
    )


class DiagnosticAnswerResponse(BaseModel):
    """Ответ на приём решения.

    Правильность здесь НЕ возвращается намеренно: иначе диагностика превращается в
    угадайку — человек подбирал бы ответ по отклику, и итог перестал бы что-либо
    измерять. Разбор он получает один раз, в конце.
    """

    task_id: int
    answered_count: int
    total_count: int
    is_complete: bool


class DiagnosticTopicResult(BaseModel):
    """Итог по одной теме."""

    topic_code: str
    topic_title: str
    is_correct: bool
    your_answer: Optional[str] = None
    correct_answer: Optional[str] = Field(
        default=None, description="Верный ответ — показывается только в итоге"
    )
    course_uid: Optional[str] = Field(
        default=None, description="Курс по этой теме, если её стоит подтянуть"
    )


class DiagnosticResultResponse(BaseModel):
    """Ответ на GET /learning/guest/diagnostic/{course_uid}/result."""

    course_uid: str
    title: str
    is_complete: bool
    solved: int = Field(..., description="Сколько задач решено верно")
    total: int
    topics: List[DiagnosticTopicResult]
    weak_topics: List[DiagnosticTopicResult] = Field(
        default_factory=list, description="Темы, которые стоит подтянуть в первую очередь"
    )
    recommendation_course_uid: Optional[str] = Field(
        default=None, description="Программа, которую предлагаем целиком"
    )
    recommendation_title: Optional[str] = None
    contact_url: str
    perfect_note: str = Field(
        ..., description="Что написать человеку, решившему все задачи: куда расти дальше"
    )
    lead_note: str = Field(..., description="Подпись у формы контакта: чем именно поможем")
    lead_submitted: bool

    model_config = ConfigDict(from_attributes=True)
