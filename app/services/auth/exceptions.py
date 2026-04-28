"""Общие исключения auth-сервисов (см. ADR-0021 §2).

Вынесено в отдельный модуль чтобы оба magic_link_service и
vk_oauth_service могли raise один тип, а роутеры — единообразно
маппить его в HTTP 409 identity_conflict.
"""
from typing import Iterable


class IdentityConflictError(Exception):
    """Identity (email/tg/vk) уже привязан к другому пользователю.

    Auto-merge запрещён ADR-0021 §2 — защита от identity-takeover.
    Linking нескольких identities к одному user возможен только через
    explicit /me/identity/{kind}/link с одноразовым link_token (Y-3).

    Атрибуты:
        conflict_kind: классификатор конфликта ("email_already_linked" |
            "email_already_linked_to_orphan_user" | ...)
        existing_kinds: список kind значений existing identity_link записей
            пользователя-владельца (пустой если orphan — identity_link
            удалён, но users.email остался).
    """

    def __init__(self, conflict_kind: str, existing_kinds: Iterable[str]) -> None:
        self.conflict_kind = conflict_kind
        self.existing_kinds = list(existing_kinds)
        super().__init__(f"identity_conflict: {conflict_kind}")
