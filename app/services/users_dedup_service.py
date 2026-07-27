"""tsk-442: расширенный маппинг ФИО для поиска кандидатов на дубль-аккаунт.

Контекст: "плавающие" ученики заводятся вручную (по календарю/расписанию) с
одним только `full_name`, без email/tg_id/vk_id. Когда такой ученик потом
регистрируется сам (TG-бот/magic-link/VK), сопоставления по ФИО НЕТ НИГДЕ —
`get_or_create_user_by_*` матчит строго по identity (tg_id/email/vk_id), при
несовпадении просто создаёт новый аккаунт (см. tg_init_service.py,
magic_link_service.py, vk_oauth_service.py). Люди вводят ФИО по-разному:
меняют местами имя/фамилию (несмотря на подсказку в форме), опечатываются,
вводят фамилию не полностью. Отчество в сравнении не участвует вовсе —
по решению оператора.

Оператор явно выбрал (AskUserQuestion): это НЕ auto-link на самой
регистрации и НЕ "это вы?"-диалог в UI. Дальше — по итогам первого реального
запуска (2026-07-27) оператор попросил автослияние для пар с высокой
уверенностью (порог 0.85-0.9), остальное — на ручной разбор.

`select_auto_merge_pairs` — обязательная защита, не опция: автослияние
разрешено ТОЛЬКО когда ровно у одной стороны пары есть identity_link
(email/tg/vk), и эта пара — единственная в обе стороны (у "плавающего" нет
других кандидатов и у "зарегистрированного" нет других кандидатов). Без
этой защиты первый же реальный прогон на проде авто-слил бы Комлев Виктор
id=142 (уже входил) + Виктор Комлев id=2 (уже входил) — score=1.00, но это
два РЕАЛЬНЫХ разных аккаунта оператора, не дубль-ошибка регистрации: обе
стороны уже имеют identity — сигнал "кто из двух — 'настоящий'" отсутствует,
такие пары остаются на ручной разбор независимо от score. Слияние
выполняет `scripts/merge_users.py` (write, протокол /db-check) — вручную
по кандидату из списка или автоматически из
`scripts/tsk442_auto_merge_duplicates.py`.

Fuzzy-сравнение — stdlib `difflib.SequenceMatcher` (без сторонних
зависимостей, в проекте таких нет): сортировка токенов делает сравнение
нечувствительным к порядку слов, `SequenceMatcher` на объединённых строках
даёт устойчивость к опечаткам и неполному вводу (частичное совпадение
подстроки повышает ratio).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_link import IdentityLink
from app.models.users import Users

# Типичные суффиксы русских отчеств — при 3+ токенах последний токен с таким
# суффиксом отбрасывается перед сравнением (отчество в матчинг не участвует).
_PATRONYMIC_SUFFIXES = ("ович", "евич", "ич", "овна", "евна", "ична", "инична")

DEFAULT_MATCH_THRESHOLD = 0.72
DEFAULT_AUTO_MERGE_THRESHOLD = 0.9


def normalize_name_tokens(full_name: str | None) -> list[str]:
    """ФИО → нормализованные токены без отчества, отсортированные (порядок слов не важен)."""
    if not full_name:
        return []
    tokens = [t.lower() for t in full_name.replace("ё", "е").split() if t]
    if len(tokens) >= 3 and tokens[-1].endswith(_PATRONYMIC_SUFFIXES):
        tokens = tokens[:-1]
    return sorted(tokens)


def fuzzy_name_match_score(a: str | None, b: str | None) -> float:
    """0..1 — насколько похожи два ФИО (без учёта порядка слов и отчества).

    Токены сортируются и склеиваются в одну строку — `SequenceMatcher.ratio()`
    на такой паре устойчив и к перестановке имени/фамилии, и к опечаткам, и к
    неполному вводу (частичная подстрока всё равно даёт высокий ratio).
    """
    tokens_a = normalize_name_tokens(a)
    tokens_b = normalize_name_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return SequenceMatcher(None, " ".join(tokens_a), " ".join(tokens_b)).ratio()


@dataclass
class DuplicateCandidate:
    user_a_id: int
    user_a_name: str
    user_a_has_identity: bool
    user_b_id: int
    user_b_name: str
    user_b_has_identity: bool
    score: float


async def find_duplicate_candidates(
    db: AsyncSession, *, threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> list[DuplicateCandidate]:
    """Кандидаты на дубль-аккаунт среди активных пользователей.

    Читает всех `is_active=true` пользователей с непустым `full_name`,
    попарно сравнивает (в масштабе школы — сотни строк, полный перебор
    дешевле, чем городить SQL-эвристики) и возвращает пары с
    `score >= threshold`, отсортированные по убыванию похожести. Для каждой
    стороны отмечает, есть ли у неё хоть одна identity_link (email/tg/vk) —
    "плавающие" аккаунты (без единой identity) — самые вероятные кандидаты
    на слияние С только что зарегистрировавшимся дублем.
    """
    users_res = await db.execute(
        select(Users.id, Users.full_name).where(
            Users.is_active.is_(True), Users.full_name.is_not(None),
        )
    )
    users = users_res.all()

    identity_res = await db.execute(select(IdentityLink.user_id).distinct())
    user_ids_with_identity = {row[0] for row in identity_res.all()}

    candidates: list[DuplicateCandidate] = []
    for i, (id_a, name_a) in enumerate(users):
        for id_b, name_b in users[i + 1:]:
            score = fuzzy_name_match_score(name_a, name_b)
            if score >= threshold:
                candidates.append(
                    DuplicateCandidate(
                        user_a_id=id_a, user_a_name=name_a,
                        user_a_has_identity=id_a in user_ids_with_identity,
                        user_b_id=id_b, user_b_name=name_b,
                        user_b_has_identity=id_b in user_ids_with_identity,
                        score=score,
                    )
                )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


@dataclass
class AutoMergePair:
    source_id: int  # "плавающий" (без identity) — деактивируется
    source_name: str
    target_id: int  # уже входил(а) под своей identity — получатель данных
    target_name: str
    score: float


def select_auto_merge_pairs(
    candidates: list[DuplicateCandidate], *, auto_threshold: float = DEFAULT_AUTO_MERGE_THRESHOLD,
) -> tuple[list[AutoMergePair], list[DuplicateCandidate]]:
    """Разделить кандидатов на (автослияние, ручной разбор).

    В автослияние попадает пара, только если ОДНОВРЕМЕННО:
    1. `score >= auto_threshold`;
    2. ровно у ОДНОЙ стороны есть identity_link (иначе непонятно, кто из
       двух "настоящий" — см. предупреждение в докстринге модуля про
       Комлев/Виктор Комлев id=142/id=2);
    3. пара единственная в обе стороны — у "плавающего" нет других
       кандидатов-совпадений и у "зарегистрированного" нет других
       кандидатов-совпадений (иначе неоднозначно, с кем именно сливать).

    Всё остальное (включая сами нарушения условий 2-3) уходит в manual —
    той же структурой `DuplicateCandidate`, что и раньше.
    """
    eligible: list[tuple[int, int, DuplicateCandidate]] = []  # (floating_id, registered_id, c)
    manual: list[DuplicateCandidate] = []

    for c in candidates:
        if c.score < auto_threshold or c.user_a_has_identity == c.user_b_has_identity:
            manual.append(c)
            continue
        if not c.user_a_has_identity:
            floating_id, registered_id = c.user_a_id, c.user_b_id
        else:
            floating_id, registered_id = c.user_b_id, c.user_a_id
        eligible.append((floating_id, registered_id, c))

    floating_counts = Counter(f for f, _r, _c in eligible)
    registered_counts = Counter(r for _f, r, _c in eligible)

    auto_pairs: list[AutoMergePair] = []
    for floating_id, registered_id, c in eligible:
        if floating_counts[floating_id] > 1 or registered_counts[registered_id] > 1:
            manual.append(c)
            continue
        source_name = c.user_a_name if c.user_a_id == floating_id else c.user_b_name
        target_name = c.user_a_name if c.user_a_id == registered_id else c.user_b_name
        auto_pairs.append(
            AutoMergePair(
                source_id=floating_id, source_name=source_name,
                target_id=registered_id, target_name=target_name,
                score=c.score,
            )
        )
    return auto_pairs, manual
