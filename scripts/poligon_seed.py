"""Идемпотентный сид/сброс учебного полигона QA (tsk-182).

Запуск на сервере полигона из /opt/lms-poligon (рабочая директория важна —
скрипт читает `.env.<tier>` из текущей директории, а не прод `.env`):

    venv/bin/python scripts/poligon_seed.py --tier dev   --reset
    venv/bin/python scripts/poligon_seed.py --tier test  --reset
    venv/bin/python scripts/poligon_seed.py --tier stage --reset

Без `--reset` — только досеивание отсутствующего (идемпотентно, ON CONFLICT
DO NOTHING по бизнес-ключам): безопасно гонять повторно.

Safety guard (пересмотрен 2026-07-25 — полигон теперь делит Postgres-инстанс
с прод, `5.42.107.253`, по решению оператора: см. docs/briefs/2026-07-25-
tsk182-poligon-timeweb.md, раздел «Изоляция — что изменилось»). Блокировка
по хосту БОЛЬШЕ НЕ РАБОТАЕТ как защита — хост теперь ЛЕГИТИМНО совпадает с
прод-хостом. Guard строится на:
  1. точном allowlist имени БД — ТОЛЬКО {poligon_dev, poligon_test, poligon_stage},
     не префиксом (префиксная проверка была бы слабее на общем инстансе:
     опечатка вроде `poligon_dev_backup_of_learn` прошла бы префиксный чек);
  2. явном блоке реальных прод-имён БД (`learn`, `content_backbone`);
  3. явном блоке реальных прод-ролей подключения (`lms_prod`, `cb_prod`).
Независим от `db_write_gate.py` (тот хук распознаёт хост+DML-сигнатуру и
будет спрашивать `DBCHECK_OK=1` на КАЖДУЮ write-команду полигона — ожидаемое
трение на общем инстансе, не баг).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TIERS = ("dev", "test", "stage")

# Точный allowlist — единственные 3 БД, с которыми этот скрипт вправе работать.
# НЕ префиксная проверка (см. docstring выше — общий Postgres-инстанс с прод
# требует точного совпадения, не "начинается с").
_ALLOWED_DBS = {"poligon_dev", "poligon_test", "poligon_stage"}

# Реальные прод-имена БД на том же инстансе — двойная защита сверх allowlist
# (избыточно с ним, но explicit-check дешевле, чем полагаться на одну проверку).
_FORBIDDEN_DBS = {"learn", "content_backbone"}

# Реальные прод-роли подключения (см. ~/.claude/hooks/db_write_gate.py,
# PROD_SIGNATURES) — даже если бы имя БД как-то совпало, чужой ролью скрипт
# работать отказывается.
_FORBIDDEN_ROLES = {"lms_prod", "cb_prod"}

# Роли из посевных данных (см. app/models/roles.py — свободные строки, id
# заранее неизвестен, поэтому лукап по имени, не по id).
# ID заданы явно, не auto-increment: `roles.id` — INTEGER NOT NULL БЕЗ
# sequence/serial в реальной схеме (см. baseline_pre_alembic_schema — таблица
# создана до появления Alembic, id всегда проставлялся вручную) — INSERT без
# id падает NotNullViolationError. id=4 для 'student' ОБЯЗАН совпадать с
# `STUDENT_ROLE_ID = 4`, захардкоженным в app/services/auth/
# role_assign_service.py (self-heal при /auth/test/issue-session) — иначе
# self-heal попытается вставить в user_roles ссылку на несуществующий
# role_id и упадёт FK-нарушением. teacher/methodist ничем не хардкожены —
# id выбраны произвольно, лишь бы не совпадали.
ROLES = (
    (4, "student"),
    (1, "teacher"),
    (2, "methodist"),
)

# Тестовые персоны — email на TLD `.test` (RFC 2606, никогда не резолвится в
# реальный ящик) — гарантия отсутствия ПД на уровне формата данных.
_STUDENT_COUNT = 5


def _assert_safe_target(database_url: str) -> None:
    """Защита от случайного запуска сброса на проде — на ОБЩЕМ Postgres-инстансе.

    Хост здесь НЕ проверяется (легитимно совпадает с прод-хостом, см. docstring
    модуля) — вся защита на точном имени БД + явном блоке прод-имён/ролей.
    """
    parsed = urlparse(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    host = parsed.hostname or ""
    dbname = (parsed.path or "").lstrip("/")
    role = parsed.username or ""

    if role in _FORBIDDEN_ROLES:
        raise RuntimeError(
            f"ОТКАЗ: роль подключения {role!r} — реальная прод-роль. "
            "Полигон должен подключаться под своей ролью (poligon_<tier>_app)."
        )
    if dbname in _FORBIDDEN_DBS:
        raise RuntimeError(
            f"ОТКАЗ: имя БД {dbname!r} — реальная прод-БД. "
            "Проверьте DATABASE_URL в .env.<tier> — это не должно быть learn/content_backbone."
        )
    if dbname not in _ALLOWED_DBS:
        raise RuntimeError(
            f"ОТКАЗ: имя БД {dbname!r} не входит в allowlist {_ALLOWED_DBS}. "
            "Скрипт работает ТОЛЬКО с точными именами poligon_dev/poligon_test/poligon_stage."
        )
    logger.info("Safety guard пройден: host=%s db=%s role=%s", host, dbname, role)


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=:t)"
        ),
        {"t": table_name},
    )
    return bool(result.scalar())


# Таблицы, которые сброс НИКОГДА не трогает — управляются Alembic, не сидом.
_PROTECTED_TABLES = {"alembic_version"}


async def reset_data(conn) -> None:
    """TRUNCATE всех прикладных таблиц (кроме alembic_version) с RESTART IDENTITY CASCADE.

    Схему (DDL) сброс не трогает — она обновляется отдельно `alembic upgrade
    head` в deploy-скрипте, до вызова этого сида.
    """
    result = await conn.execute(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        )
    )
    tables = [row[0] for row in result.fetchall() if row[0] not in _PROTECTED_TABLES]
    if not tables:
        logger.warning("Не найдено ни одной прикладной таблицы — схема ещё не мигрирована?")
        return
    quoted = ", ".join(f'"{t}"' for t in tables)
    logger.info("TRUNCATE %d таблиц (RESTART IDENTITY CASCADE)", len(tables))
    await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


async def seed_roles(conn) -> None:
    for role_id, name in ROLES:
        await conn.execute(
            text(
                "INSERT INTO roles (id, name) VALUES (:id, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": role_id, "name": name},
        )


async def seed_users(conn) -> dict[str, int]:
    """Создаёт тестовых персон, возвращает {персона: user_id}."""
    ids: dict[str, int] = {}

    async def _upsert_user(email: str, full_name: str) -> int:
        result = await conn.execute(
            text(
                "INSERT INTO users (email, full_name) VALUES (:email, :name) "
                "ON CONFLICT (email) WHERE email IS NOT NULL DO UPDATE SET full_name = EXCLUDED.full_name "
                "RETURNING id"
            ),
            {"email": email, "name": full_name},
        )
        return result.scalar_one()

    for i in range(1, _STUDENT_COUNT + 1):
        key = f"student-{i:02d}"
        email = f"poligon-{key}@example.test"
        uid = await _upsert_user(email, f"Тестовый ученик {i}")
        ids[key] = uid
        await _assign_role(conn, uid, "student")

    teacher_id = await _upsert_user("poligon-teacher-01@example.test", "Тестовый преподаватель")
    ids["teacher-01"] = teacher_id
    await _assign_role(conn, teacher_id, "teacher")

    methodist_id = await _upsert_user("poligon-methodist-01@example.test", "Тестовый методист")
    ids["methodist-01"] = methodist_id
    await _assign_role(conn, methodist_id, "methodist")

    return ids


async def _assign_role(conn, user_id: int, role_name: str) -> None:
    role_id_result = await conn.execute(
        text("SELECT id FROM roles WHERE name = :name"), {"name": role_name}
    )
    role_id = role_id_result.scalar_one()
    await conn.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "r": role_id},
    )
    await conn.execute(
        text(
            "INSERT INTO identity_link (user_id, kind, value) VALUES (:u, 'email', "
            "(SELECT email FROM users WHERE id = :u)) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id},
    )


async def seed_courses(conn) -> dict[str, int]:
    """2 курса минимального объёма — под сами дефекты + Г9 SQL-практику.

    Не пытается воспроизвести реальный курс «Тестировщик ПО» целиком — тот
    живёт на проде (WP+LMS), полигон лишь даёт объект тестирования.
    """
    ids: dict[str, int] = {}
    courses = [
        ("poligon-basics", "Полигон: Основы веб-тестирования", 990),
        ("poligon-api", "Полигон: API и SQL для тестировщика", 1490),
    ]
    for uid_str, title, price in courses:
        result = await conn.execute(
            text(
                "INSERT INTO courses (course_uid, title, price, access_level) "
                "VALUES (:uid, :title, :price, 'self_guided') "
                "ON CONFLICT (course_uid) DO UPDATE SET title = EXCLUDED.title "
                "RETURNING id"
            ),
            {"uid": uid_str, "title": title, "price": price},
        )
        ids[uid_str] = result.scalar_one()
    return ids


async def seed_sql_practice_inconsistencies(conn) -> None:
    """Намеренно противоречивая строка для упражнений Г9 (SQL).

    Курс с отрицательной ценой (в каталоге НЕ виден — API-роутер явно исключает
    `course_uid LIKE 'poligon-sql-anomaly-%'`, коллизии с UI-дефектами нет,
    только прямой SQL-запрос `SELECT * FROM courses WHERE price < 0` находит
    аномалию) — соответствует разделу «Тестовая БД с предсказуемыми данными»
    из tsk-182.

    Изначальный план (осиротевшая запись `user_courses` на несуществующий
    `course_id`) оказался невыполним: `user_courses.course_id` — FK ON DELETE
    CASCADE на `courses.id`, Postgres физически не даст вставить строку на
    несуществующий курс (и не оставляет сирот при удалении — сам смысл
    CASCADE). Второй анти-пример дан ниже, в `seed_promo_codes` — дубль
    редемпшна STUDENT20 (та же аномалия, что и класс 8 дефект-реестра,
    предсказуемая и валидная относительно реальных FK-ограничений).
    """
    await conn.execute(
        text(
            "INSERT INTO courses (course_uid, title, price, access_level) VALUES "
            "('poligon-sql-anomaly-negative-price', 'Полигон: аномалия (не публиковать)', -500, 'self_guided') "
            "ON CONFLICT (course_uid) DO NOTHING"
        )
    )


async def seed_promo_codes(conn) -> None:
    """Промокоды SUMMER2026/STUDENT20 — точное соответствие урокам 6.4/6.7.

    Таблица `poligon_promo_codes` — часть нового кода `poligon`-ветки (см.
    deploy/poligon/new-code/ в LMS и SPW репозиториях), создаётся собственной
    Alembic-миграцией. Если миграция ещё не применена — сид пропускает этот
    шаг с явным предупреждением, а не падает.
    """
    if not await _table_exists(conn, "poligon_promo_codes"):
        logger.warning(
            "Таблица poligon_promo_codes отсутствует — миграция new-code ещё не "
            "применена. Промокоды не засеяны (не блокирует остальной сид)."
        )
        return
    await conn.execute(
        text(
            "INSERT INTO poligon_promo_codes (code, discount_percent, max_uses_per_account) "
            "VALUES (:code, :pct, :max_uses) ON CONFLICT (code) DO NOTHING"
        ),
        [
            {"code": "SUMMER2026", "pct": 15, "max_uses": 1},
            {"code": "STUDENT20", "pct": 20, "max_uses": 1},
        ],
    )

    # Г9 SQL-аномалия (замена невыполнимой "осиротевшей" user_courses, см.
    # seed_sql_practice_inconsistencies) — предсказуемый дубль редемпшна
    # STUDENT20 для student-01, ровно демонстрирующий класс 8 дефект-реестра
    # прямым SQL: `SELECT user_id, promo_code, COUNT(*) FROM
    # poligon_promo_redemptions GROUP BY 1,2 HAVING COUNT(*) > 1`.
    student_row = await conn.execute(
        text("SELECT id FROM users WHERE email = 'poligon-student-01@example.test'")
    )
    student_id = student_row.scalar_one_or_none()
    if student_id is not None:
        for _ in range(2):
            await conn.execute(
                text(
                    "INSERT INTO poligon_promo_redemptions (user_id, promo_code) "
                    "VALUES (:u, 'STUDENT20')"
                ),
                {"u": student_id},
            )


async def run(tier: str, do_reset: bool) -> None:
    env_file = project_root / f".env.{tier}"
    if not env_file.exists():
        raise RuntimeError(
            f"Не найден {env_file} — первичная настройка сервера не завершена (см. deploy/poligon/README.md)"
        )
    load_dotenv(dotenv_path=env_file, encoding="utf-8-sig", override=True)

    import os

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(f"DATABASE_URL не задан в {env_file}")

    _assert_safe_target(database_url)

    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        if do_reset:
            await reset_data(conn)

        await seed_roles(conn)
        user_ids = await seed_users(conn)
        course_ids = await seed_courses(conn)
        await seed_sql_practice_inconsistencies(conn)
        await seed_promo_codes(conn)

        logger.info(
            "Сид завершён: tier=%s пользователей=%d курсов=%d",
            tier, len(user_ids), len(course_ids),
        )
    await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", required=True, choices=TIERS)
    parser.add_argument(
        "--reset", action="store_true",
        help="TRUNCATE всех прикладных таблиц перед сидом (деструктивно для текущей песочницы)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.tier, args.reset))
        return 0
    except Exception:
        logger.exception("poligon_seed.py упал")
        return 1


if __name__ == "__main__":
    sys.exit(main())
