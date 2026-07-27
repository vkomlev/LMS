"""tsk-442: расширенный маппинг ФИО (нечёткое сравнение для поиска дублей).

Покрывает `normalize_name_tokens`/`fuzzy_name_match_score` (порядок слов,
опечатки, неполная фамилия, отброс отчества), `find_duplicate_candidates`
(кандидаты среди пользователей БД, is_active фильтр, has_identity флаг) и
`select_auto_merge_pairs` (безопасный отбор для автослияния — синтетические
`DuplicateCandidate`, БД не нужна).
"""
from __future__ import annotations

import random

import pytest

from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.repos.users_repo import UsersRepository
from app.services.users_dedup_service import (
    DuplicateCandidate,
    fuzzy_name_match_score,
    find_duplicate_candidates,
    normalize_name_tokens,
    select_auto_merge_pairs,
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


def _cand(a_id, a_name, a_has_id, b_id, b_name, b_has_id, score) -> DuplicateCandidate:
    return DuplicateCandidate(
        user_a_id=a_id, user_a_name=a_name, user_a_has_identity=a_has_id,
        user_b_id=b_id, user_b_name=b_name, user_b_has_identity=b_has_id,
        score=score,
    )


def test_auto_merge_selects_floating_plus_registered_high_score():
    c = _cand(101, "Ястребцов Елисей", False, 102, "Елисей Ястребцов", True, 1.0)
    auto, manual = select_auto_merge_pairs([c], auto_threshold=0.9)
    assert manual == []
    assert len(auto) == 1
    assert auto[0].source_id == 101  # без identity — деактивируется
    assert auto[0].target_id == 102  # с identity — получатель


def test_auto_merge_skips_when_both_have_identity():
    """Регрессия на реальный инцидент: Комлев Виктор id=142 + Виктор Комлев
    id=2 — оба уже входили под своей identity, score=1.0. Без этой защиты
    первый прод-прогон авто-слил бы два РЕАЛЬНЫХ разных аккаунта."""
    c = _cand(142, "Комлев Виктор", True, 2, "Виктор Комлев", True, 1.0)
    auto, manual = select_auto_merge_pairs([c], auto_threshold=0.9)
    assert auto == []
    assert manual == [c]


def test_auto_merge_skips_when_neither_has_identity():
    c = _cand(1, "Иванов Иван", False, 2, "Иван Иванов", False, 1.0)
    auto, manual = select_auto_merge_pairs([c], auto_threshold=0.9)
    assert auto == []
    assert manual == [c]


def test_auto_merge_skips_below_threshold():
    c = _cand(1, "Петров Пётр", False, 2, "Пётр Петров", True, 0.8)
    auto, manual = select_auto_merge_pairs([c], auto_threshold=0.9)
    assert auto == []
    assert manual == [c]


def test_auto_merge_skips_ambiguous_multiple_matches_for_same_floating():
    # Один "плавающий" похож сразу на ДВУХ зарегистрированных — неясно, с кем сливать.
    c1 = _cand(1, "Сидоров Иван", False, 2, "Иван Сидоров", True, 0.95)
    c2 = _cand(1, "Сидоров Иван", False, 3, "Иван Сидорин", True, 0.91)
    auto, manual = select_auto_merge_pairs([c1, c2], auto_threshold=0.9)
    assert auto == []
    assert len(manual) == 2 and c1 in manual and c2 in manual


def test_auto_merge_skips_ambiguous_multiple_matches_for_same_registered():
    # ДВА разных "плавающих" похожи на одного зарегистрированного — тоже неоднозначно.
    c1 = _cand(1, "Кузнецова Анна", False, 3, "Анна Кузнецова", True, 0.95)
    c2 = _cand(2, "Кузнецова Анна-2", False, 3, "Анна Кузнецова", True, 0.91)
    auto, manual = select_auto_merge_pairs([c1, c2], auto_threshold=0.9)
    assert auto == []
    assert len(manual) == 2 and c1 in manual and c2 in manual
