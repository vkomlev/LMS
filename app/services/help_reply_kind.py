"""Тип ответа на заявку помощи (tsk-1147).

Способ, которым преподаватель помог ученику: текстом, голосом на занятии,
видеоразбором или консультацией в Телемосте. Тип хранится у ответа
(`help_request_replies.reply_kind`); клиент показывает предвыбор по этой же
догадке, преподаватель правит одним нажатием.

Правило догадки живёт здесь одно: им размечена история на проде и его же
применяет сервер, если клиент тип не прислал. Предвыбор в SPW и боте —
зеркало этих шаблонов; менять их вместе.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

ReplyKind = Literal["text", "voice", "video", "telemost"]
REPLY_KINDS: tuple[str, ...] = ("text", "voice", "video", "telemost")

#: Ссылка на встречу в Яндекс Телемосте.
_TELEMOST_RE = re.compile(r"telemost\.(?:yandex|360\.yandex)\.", re.IGNORECASE)
#: Ссылка на запись: видеохостинги, файл на Диске, прямой видеофайл.
_VIDEO_RE = re.compile(
    r"(?:youtube\.com/|youtu\.be/|rutube\.ru/|vk\.com/video|vkvideo\.ru/"
    r"|disk\.yandex\.[a-z]+/|yadi\.sk/|\.mp4\b|\.mov\b|\.webm\b)",
    re.IGNORECASE,
)


def guess_reply_kind(body: Optional[str]) -> ReplyKind:
    """Догадаться о способе ответа по тексту.

    Телемост важнее видео: ссылка на встречу означает живую консультацию, даже
    если рядом лежит ссылка на запись.
    """
    text = body or ""
    if _TELEMOST_RE.search(text):
        return "telemost"
    if _VIDEO_RE.search(text):
        return "video"
    return "text"
