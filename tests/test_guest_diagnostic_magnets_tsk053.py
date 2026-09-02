"""Механизм зондов держит несколько лид-магнитов (tsk-053, фаза 3).

Фаза 2 сделала диагностику ЕГЭ, и рекомендация с текстами были зашиты в модуль: курс
«ЕГЭ по информатике» и фразы про экзамен. Фаза 3 добавила второй магнит — «Готов ли ты
к Backend?», — и зашитость стала прямой ошибкой: человек, проверявший готовность к
backend, получил бы предложение готовиться к ЕГЭ.

Здесь проверяется именно это разделение: что магнит говорит своими словами, ведёт в свою
программу, и что незнакомый магнит не роняет страницу, но и не обещает того, чего нет.
Плюс сверка содержимого третьего магнита — зонды пишутся руками, и опечатка в теме или
пропущенный вариант видны только счётом.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services import guest_diagnostic_service as svc
from app.services.guest_diagnostic_service import DEFAULT_MAGNET as DEFAULT

pytestmark = pytest.mark.requires_redis

_MAGNET_UID = "pytest:tsk053-magnet"
_TOPIC_UID = "pytest:tsk053-magnet-topic"
_TARGET_UID = "pytest:tsk053-magnet-target"
_TARGET_TITLE = "Программа этого магнита"

_STATE: dict[str, object] = {}

_COPY = svc.MagnetCopy(
    recommendation_course_uid=_TARGET_UID,
    contact_weak="Прошёл проверку: {solved} из {total}. Просели: {themes}.",
    contact_strong="Прошёл проверку: {solved} из {total}. Всё сошлось.",
    perfect_note="Своя фраза про то, куда расти дальше.",
    lead_note="Своя подпись у формы контакта.",
)


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_rate_limits():
    import os

    import redis.asyncio as aioredis

    redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/2"), decode_responses=True
    )
    patterns = [
        "guest_session:*",
        "diag_read:*",
        "diag_answer:*",
        "diag_answer_session:*",
        "diag_lead:*",
        "diag_lead_session:*",
    ]
    try:
        for pat in patterns:
            async for key in redis.scan_iter(match=pat, count=200):
                await redis.delete(key)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _seed_magnet(db):
    """Магнит из одной темы, курс-тема и курс-программа, куда он рекомендует."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()

    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, course_uid, "
                "is_public_demo) VALUES ('pytest магнит', 'о нём', 'auto_check', :uid, TRUE) "
                "RETURNING id"
            ),
            {"uid": _MAGNET_UID},
        )
    ).scalar_one()
    for uid, title in ((_TOPIC_UID, "Курс по теме"), (_TARGET_UID, _TARGET_TITLE)):
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
                "VALUES (:t, 'self_guided', :uid, FALSE)"
            ),
            {"t": title, "uid": uid},
        )

    content = {
        "type": "SA",
        "stem": "Единственный зонд магнита",
        "lead_magnet": True,
        "diagnostic_topic": {
            "code": "m1",
            "title": "Тема 1. Проверочная",
            "course_uid": _TOPIC_UID,
        },
    }
    rules = {
        "max_score": 1,
        "auto_check": True,
        "scoring_mode": "all_or_nothing",
        "manual_review_required": False,
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
        "short_answer": {
            "use_regex": False,
            "regex": None,
            "normalization": ["trim", "lower"],
            "accepted_answers": [{"value": "42", "score": 1}],
        },
    }
    task_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, order_position, is_active) "
                    "VALUES (:uid, 1, CAST(:c AS jsonb), :course, :diff, CAST(:r AS jsonb), "
                    "1, TRUE) RETURNING id"
                ),
                {
                    "uid": f"{_MAGNET_UID}:m1:v1",
                    "c": json.dumps(content, ensure_ascii=False),
                    "course": course_id,
                    "diff": difficulty_id,
                    "r": json.dumps(rules, ensure_ascii=False),
                },
            )
        ).scalar_one()
    )
    _STATE["course_id"] = course_id
    _STATE["task_id"] = task_id
    await db.commit()

    try:
        yield
    finally:
        await db.execute(
            text("DELETE FROM leads WHERE quiz_course_id = :c"), {"c": course_id}
        )
        await db.execute(
            text("DELETE FROM courses WHERE course_uid IN (:a, :b, :c)"),
            {"a": _MAGNET_UID, "b": _TOPIC_UID, "c": _TARGET_UID},
        )
        await db.commit()


@pytest.fixture()
def registered_magnet(monkeypatch):
    """Магнит записан в реестр — как настоящие ЕГЭ и Backend."""
    monkeypatch.setitem(svc.MAGNETS, _MAGNET_UID, _COPY)


async def _start_session(client) -> None:
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201


async def _solve(client, value: str = "42"):
    return await client.post(
        "/api/v1/learning/guest/diagnostic/answers",
        json={"task_id": _STATE["task_id"], "value": value},
    )


async def _result(client) -> dict:
    return (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_MAGNET_UID}/result")
    ).json()


# ─── магнит говорит своими словами ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_uses_own_copy_and_program(client, registered_magnet):
    """Итог берёт тексты и программу этого магнита, а не соседнего."""
    await _start_session(client)
    await _solve(client)

    body = await _result(client)
    assert body["perfect_note"] == _COPY.perfect_note
    assert body["lead_note"] == _COPY.lead_note
    assert body["recommendation_course_uid"] == _TARGET_UID
    assert body["recommendation_title"] == _TARGET_TITLE
    # Ровно то, из-за чего разделение и делалось: ни слова про ЕГЭ у чужого магнита.
    assert "ЕГЭ" not in body["contact_url"]
    assert "ЕГЭ" not in body["perfect_note"]


@pytest.mark.asyncio
async def test_contact_message_carries_score_and_weak_topics(client, registered_magnet):
    """Заготовка сообщения подставляет счёт и просевшие темы этого прохождения."""
    from urllib.parse import unquote

    await _start_session(client)
    await _solve(client, value="мимо")

    body = await _result(client)
    message = unquote(body["contact_url"].split("?text=", 1)[1])
    assert "0 из 1" in message
    assert "Тема 1" in message


@pytest.mark.asyncio
async def test_strong_message_when_nothing_failed(client, registered_magnet):
    """Решил всё — сообщение берётся из второй заготовки, без списка тем."""
    from urllib.parse import unquote

    await _start_session(client)
    await _solve(client)

    body = await _result(client)
    message = unquote(body["contact_url"].split("?text=", 1)[1])
    assert "Всё сошлось" in message
    assert "Просели" not in message


# ─── магнит без своей строки в реестре ──────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_magnet_still_works_but_promises_nothing(client):
    """Забыли завести тексты — страница живёт, но программу не называет.

    Молча подставлять чужую рекомендацию нельзя: человек пошёл бы записываться не на
    то, что проверял. Пустая рекомендация честнее — и её видно в логе предупреждением.
    """
    await _start_session(client)
    await _solve(client)

    body = await _result(client)
    assert body["is_complete"] is True
    assert body["recommendation_course_uid"] is None
    assert body["recommendation_title"] is None
    assert body["perfect_note"]
    assert body["lead_note"]
    assert body["contact_url"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_missing_target_course_does_not_break_result(client, monkeypatch):
    """Программу переименовали — итог отдаётся, но несуществующей не обещает."""
    monkeypatch.setitem(
        svc.MAGNETS,
        _MAGNET_UID,
        svc.MagnetCopy(
            recommendation_course_uid="pytest:такого-курса-нет",
            contact_weak=_COPY.contact_weak,
            contact_strong=_COPY.contact_strong,
            perfect_note=_COPY.perfect_note,
            lead_note=_COPY.lead_note,
        ),
    )
    await _start_session(client)
    await _solve(client)

    body = await _result(client)
    assert body["is_complete"] is True
    assert body["recommendation_course_uid"] is None
    assert body["recommendation_title"] is None


# ─── содержимое третьего магнита ────────────────────────────────────────────

def _load_seed_module():
    """Скрипт наполнения лежит вне пакета — подгружаем по пути."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "tsk053_seed_backend.py"
    spec = importlib.util.spec_from_file_location("tsk053_seed_backend", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_probes_are_complete_and_consistent():
    """Зонды «Готов ли ты к Backend?»: восемь тем, у каждой три варианта с ответом.

    Пишутся руками, и пропущенный вариант тихо сломал бы отбор по хешу — часть
    посетителей получала бы одну и ту же задачу. Считаем механически.
    """
    seed = _load_seed_module()

    codes = [t["code"] for t in seed.TOPICS]
    assert len(codes) == 8
    assert len(set(codes)) == len(codes), "коды тем повторяются"
    assert set(seed.PROBES) == set(codes), "тема без зондов или зонды без темы"

    for topic in seed.TOPICS:
        assert topic["title"].strip(), f"{topic['code']}: пустое название темы"
        assert topic["course_uid"].strip(), f"{topic['code']}: некуда вести при провале"
        variants = seed.PROBES[topic["code"]]
        assert len(variants) == 3, f"{topic['code']}: вариантов {len(variants)}, а не 3"
        stems = {v["stem"] for v in variants}
        assert len(stems) == 3, f"{topic['code']}: варианты повторяют условие"
        for v in variants:
            assert v["stem"].strip(), f"{topic['code']}: пустое условие"
            assert v["answer"].strip(), f"{topic['code']}: пустой эталон"


def test_backend_magnet_is_registered_and_points_at_existing_program():
    """У третьего магнита есть своя строка в реестре, и она не про ЕГЭ."""
    seed = _load_seed_module()
    copy = svc.MAGNETS.get(seed.BACKEND_UID)
    assert copy is not None, "магнит завели, а тексты для него — нет"
    assert copy.recommendation_course_uid, "проверка готовности без программы бессмысленна"
    for line in (copy.contact_weak, copy.contact_strong, copy.perfect_note, copy.lead_note):
        assert "ЕГЭ" not in line and "экзамен" not in line, f"текст от диагностики ЕГЭ: {line}"
    # Плейсхолдеры должны подставляться, а не уехать в сообщение как есть.
    assert copy.contact_weak.format(solved=1, total=2, themes="Тема").count("{") == 0
    assert copy.contact_strong.format(solved=1, total=2).count("{") == 0


@pytest.mark.parametrize("uid", sorted(svc.MAGNETS) + ["__default__"])
def test_every_magnet_template_survives_formatting(uid):
    """Заготовка сообщения собирается у КАЖДОГО магнита, а не только у проверенного.

    Сообщение собирается через ``str.format``: лишняя фигурная скобка в тексте — и
    ручка итога отвечает пятисоткой вместо экрана с результатом. Экран это последний
    шаг воронки, ломать его опечаткой в тексте нельзя, а поймать иначе — только руками
    на живой странице. Проверяем механически, включая запасной набор текстов.
    """
    copy = DEFAULT if uid == "__default__" else svc.MAGNETS[uid]

    weak = copy.contact_weak.format(solved=3, total=8, themes="Тема 1, Тема 2")
    strong = copy.contact_strong.format(solved=8, total=8)
    for rendered in (weak, strong):
        assert "{" not in rendered and "}" not in rendered, f"{uid}: плейсхолдер не подставился"
        assert "3" in weak and "8" in strong
    assert copy.perfect_note.strip(), f"{uid}: пустой текст для решивших всё"
    assert copy.lead_note.strip(), f"{uid}: пустая подпись у формы контакта"
