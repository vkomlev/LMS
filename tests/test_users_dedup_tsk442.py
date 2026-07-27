"""tsk-442: расширенный маппинг ФИО (нечёткое сравнение для поиска дублей).

Покрывает `normalize_name_tokens`/`fuzzy_name_match_score` (порядок слов,
опечатки, неполная фамилия, отброс отчества) и
`find_duplicate_candidates` (кандидаты среди пользователей БД, is_active
фильтр, has_identity флаг).
"""
from __future__ import annotations

import random

import pytest

from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.repos.users_repo import UsersRepository
from app.services.users_dedup_service import (
    fuzzy_name_match_score,
    find_duplicate_candidates,
    normalize_name_tokens,
)


def test_normalize_strips_patronymic_and_sorts():
    assert normalize_name_tokens("Иванов Иван Иванович") == ["иван", "иванов"]
    assert normalize_name_tokens("Иван Иванов") == ["иван", "иванов"]


def test_normalize_keeps_two_token_name_as_is():
    # Без отчества (2 токена) — ничего не отбрасываем, даже если похоже на фамилию.
    assert normalize_name_tokens("Ильич Ленин") == ["ильич", "ленин"]


def test_normalize_empty():
    assert normalize_name_tokens(None) == []
    assert normalize_name_tokens("") == []


def test_fuzzy_match_word_order_swapped():
    score = fuzzy_name_match_score("Иванов Иван", "Иван Иванов")
    assert score == 1.0


def test_fuzzy_match_ignores_patronymic():
    score = fuzzy_name_match_score("Иванов Иван Иванович", "Иван Иванов")
    assert score == 1.0


def test_fuzzy_match_tolerates_typo():
    score = fuzzy_name_match_score("Петров Алексей", "Петров Алексий")
    assert score > 0.85


def test_fuzzy_match_partial_surname():
    score = fuzzy_name_match_score("Сидоров Максим", "Сидор Максим")
    assert score > 0.8


def test_fuzzy_match_different_people_low_score():
    score = fuzzy_name_match_score("Иванов Иван", "Петрова Мария")
    assert score < 0.4


@pytest.mark.asyncio
async def test_find_duplicate_candidates_matches_floating_vs_registered(db):
    suffix = random.randint(10**8, 10**10)
    floating = Users(email=None, password_hash=None, full_name="Серебрякова Екатерина", tg_id=None)
    registered = Users(
        email=None, password_hash=None, full_name="Екатерина Серебрякова", tg_id=900_000_000 + suffix,
    )
    unrelated = Users(email=None, password_hash=None, full_name="Совсем Другой Человек", tg_id=None)
    db.add_all([floating, registered, unrelated])
    await db.flush()
    db.add(IdentityLink(user_id=registered.id, kind="tg", value=str(registered.tg_id)))
    await db.commit()

    candidates = await find_duplicate_candidates(db, threshold=0.72)
    pair_ids = {(c.user_a_id, c.user_b_id) for c in candidates}
    match = next(
        (c for c in candidates if {c.user_a_id, c.user_b_id} == {floating.id, registered.id}),
        None,
    )
    assert match is not None, pair_ids
    assert match.score == 1.0
    floating_side = match if match.user_a_id == floating.id else match
    # Один из двух — floating (без identity), другой — с identity.
    has_identity_flags = {
        (match.user_a_id, match.user_a_has_identity),
        (match.user_b_id, match.user_b_has_identity),
    }
    assert (floating.id, False) in has_identity_flags
    assert (registered.id, True) in has_identity_flags


@pytest.mark.asyncio
async def test_find_duplicate_candidates_excludes_inactive(db):
    suffix = random.randint(10**8, 10**10)
    a = Users(email=None, password_hash=None, full_name=f"Тестов Тест {suffix}", tg_id=None)
    b = Users(
        email=None, password_hash=None, full_name=f"Тест Тестов {suffix}", tg_id=None,
        is_active=False,
    )
    db.add_all([a, b])
    await db.commit()

    candidates = await find_duplicate_candidates(db, threshold=0.72)
    pair_ids = {(c.user_a_id, c.user_b_id) for c in candidates}
    assert (a.id, b.id) not in pair_ids and (b.id, a.id) not in pair_ids


@pytest.mark.asyncio
async def test_search_by_full_name_excludes_merged_away_accounts(db):
    """tsk-442: после слияния source не должен всплывать в пикерах
    "Добавить ученика"/"Назначить курс" — их наполняет этот же поиск."""
    suffix = random.randint(10**8, 10**10)
    active = Users(email=None, password_hash=None, full_name=f"Дубликатов Дубль {suffix}", tg_id=None)
    merged_away = Users(
        email=None, password_hash=None, full_name=f"Дубликатов Дубль-старый {suffix}",
        tg_id=None, is_active=False,
    )
    db.add_all([active, merged_away])
    await db.commit()

    results = await UsersRepository().search_by_full_name_with_role(db, q=f"Дубликатов Дубль {suffix}")
    result_ids = {u.id for u in results}
    assert active.id in result_ids
    assert merged_away.id not in result_ids
