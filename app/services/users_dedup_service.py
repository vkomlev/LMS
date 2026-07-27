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

Оператор явно выбрал (AskUserQuestion): это НЕ auto-link и НЕ "это вы?"-диалог
в UI — только список кандидатов на дубль для ручного разбора оператором/
методистом (см. `scripts/find_duplicate_candidates.py`, read-only). Слияние
самих учёток — отдельный write-скрипт `scripts/merge_users.py` по протоколу
/db-check, не автоматика.

Fuzzy-сравнение — stdlib `difflib.SequenceMatcher` (без сторонних
зависимостей, в проекте таких нет): сортировка токенов делает сравнение
нечувствительным к порядку слов, `SequenceMatcher` на объединённых строках
даёт устойчивость к опечаткам и неполному вводу (частичное совпадение
подстроки повышает ratio).
"""
from __future__ import annotations

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
