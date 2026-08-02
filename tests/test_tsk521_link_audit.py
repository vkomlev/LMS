"""tsk-521: регулярная проверка целостности ссылок на файлы в контенте.

Связи «материал → файл» в базе нет, поэтому битую ссылку не видно, пока на неё
не наткнётся человек: в tsk-519 такая провисела полгода.

Тик по устройству проходит **всю** базу, а не подсунутую тестом строку, поэтому
проверки здесь смотрят на конкретную ссылку в `broken_targets`, а не на общий
счётчик находок: в dev-БД лежит реальный контент со своими ссылками.

Сценарии:
- целый файл материала в находки не попадает
- пропавший файл материала попадает и рождает уведомление методисту
- пропавшее CAS-медиа задания находится так же
- ссылка на свой сайт проверяется, чужие домены — нет (418/429 у них не дефект)
- выключенные материалы не проверяются
- ссылка на страницу (без расширения файла) не проверяется
- повторный тик в пределах отсрочки молчит — ежедневная проверка не спамит
- недоступное хранилище прерывает прогон, а не рисует все ссылки битыми
- второй worker отступает по advisory-lock
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Dict
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services import link_audit_service
from app.utils.exceptions import DomainError


async def _pick_root(db) -> int:
    row = (
        await db.execute(
            text(
                "SELECT id FROM courses "
                "WHERE id NOT IN (SELECT course_id FROM course_parents) LIMIT 1"
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нужен хотя бы один корневой курс")
    return int(row[0])


async def _create_material(
    db, *, course_id: int, content: Dict[str, Any], is_active: bool = True
) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, is_active) "
            "VALUES (:t, 'image', CAST(:c AS jsonb), :cid, :act) RETURNING id"
        ),
        {
            "t": f"tsk521-{random.randint(10**8, 10**10)}",
            "c": json.dumps(content),
            "cid": course_id,
            "act": is_active,
        },
    )
    mid = res.scalar_one()
    await db.commit()
    return mid


def _file_content(file_id: str) -> Dict[str, Any]:
    return {
        "sources": [{"url": f"/api/v1/materials/files/{file_id}", "type": "file"}],
        "default_source": 0,
    }


async def _cleanup(db, *, material_ids: list[int]) -> None:
    if material_ids:
        await db.execute(text("DELETE FROM materials WHERE id = ANY(:m)"), {"m": material_ids})
    await db.execute(
        text("DELETE FROM notifications WHERE kind = :k"),
        {"k": link_audit_service.NOTIFICATION_KIND},
    )
    await db.commit()


async def _notifications(db) -> list[Any]:
    res = await db.execute(
        text(
            "SELECT id, user_id, title, content, payload FROM notifications "
            "WHERE kind = :k ORDER BY id"
        ),
        {"k": link_audit_service.NOTIFICATION_KIND},
    )
    return res.fetchall()


def _new_name() -> str:
    """Уникальное CAS-имя, которого заведомо нет в реальном контенте."""
    return f"{hashlib.sha256(uuid4().bytes).hexdigest()}.png"


@pytest.fixture
def storage(monkeypatch):
    """Подменяет хранилище: «нет» только у перечисленных имён, остальное цело.

    Так тест управляет своей ссылкой и не объявляет битым реальный контент
    dev-БД, который тик проходит заодно.
    """
    missing: set[str] = set()

    async def material_exists(file_id: str) -> bool:
        return file_id not in missing

    async def media_exists(sha_ext: str) -> bool:
        return sha_ext not in missing

    monkeypatch.setattr(
        link_audit_service.material_files_storage, "material_file_exists", material_exists
    )
    monkeypatch.setattr(link_audit_service, "_cas_media_exists", media_exists)
    return missing


@pytest.fixture
def no_own_hosts(monkeypatch):
    """Убирает свои домены из охвата: тесту про хранилище сеть не нужна."""
    monkeypatch.setenv("LINK_AUDIT_OWN_HOSTS", "")

    async def _fail(*args, **kwargs):  # pragma: no cover — срабатывание = дефект
        raise AssertionError("тик полез в сеть, хотя охват доменов пуст")

    monkeypatch.setattr("httpx.AsyncClient.head", _fail)
    monkeypatch.setattr("httpx.AsyncClient.get", _fail)


# ─── находки и молчание ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intact_file_is_not_reported(db, db_session_factory, storage, no_own_hosts):
    """Файл на месте — в находках его нет."""
    course_id = await _pick_root(db)
    file_id = _new_name()  # в `missing` не добавляем — значит цел
    mid = await _create_material(db, course_id=course_id, content=_file_content(file_id))
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert summary["locked"] is True
        assert f"material:{file_id}" not in summary["broken_targets"], summary
    finally:
        await _cleanup(db, material_ids=[mid])


@pytest.mark.asyncio
async def test_missing_file_is_reported_to_methodist(
    db, db_session_factory, storage, no_own_hosts
):
    """Пропавший файл — находка и уведомление методисту с примером и источником."""
    course_id = await _pick_root(db)
    file_id = _new_name()
    storage.add(file_id)
    mid = await _create_material(db, course_id=course_id, content=_file_content(file_id))
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert summary["broken_targets"].get(f"material:{file_id}") == "файла нет в хранилище"
        assert summary["notified"] >= 1, summary

        rows = await _notifications(db)
        assert rows, "уведомление не создано"
        payload = rows[0][4]
        example = next(
            (ex for ex in payload["examples"] if ex["target"] == f"material:{file_id}"), None
        )
        assert example is not None, payload
        assert f"material {mid}" in example["where"], example
    finally:
        await _cleanup(db, material_ids=[mid])


@pytest.mark.asyncio
async def test_missing_task_media_is_reported(db, db_session_factory, storage, no_own_hosts):
    """Медиа задания проверяется наравне с файлами материалов."""
    course_id = await _pick_root(db)
    sha_ext = _new_name()
    storage.add(sha_ext)
    content = {"text": f'<img src="/api/v1/media/{sha_ext}"/>', "format": "html"}
    mid = await _create_material(db, course_id=course_id, content=content)
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert summary["broken_targets"].get(f"media:{sha_ext}") == "медиа нет в хранилище"
    finally:
        await _cleanup(db, material_ids=[mid])


# ─── охват проверки ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_foreign_hosts_are_not_checked(db, db_session_factory, storage, monkeypatch):
    """Чужие сайты не трогаем: их 418/429 — защита от роботов, а не битая ссылка.

    Свои домены оставлены в охвате, а мок сети роняет тест, если запрос уйдёт
    на чужой хост.
    """
    monkeypatch.setenv("LINK_AUDIT_OWN_HOSTS", "victor-komlev.ru")
    foreign = "https://kompege.ru/images/tsk521-not-real.png"

    class _Resp:
        status_code = 200

    async def _head(self, target, **kwargs):
        assert "kompege.ru" not in target, f"запрос ушёл на чужой хост: {target}"
        assert "sdamgia" not in target, f"запрос ушёл на чужой хост: {target}"
        return _Resp()

    monkeypatch.setattr("httpx.AsyncClient.head", _head)

    course_id = await _pick_root(db)
    content = {
        "text": (
            f'<img src="{foreign}"/>'
            '<img src="https://ege.sdamgia.ru/formula/svg/92/tsk521.svg"/>'
        ),
        "format": "html",
    }
    mid = await _create_material(db, course_id=course_id, content=content)
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert foreign not in summary["broken_targets"], summary
    finally:
        await _cleanup(db, material_ids=[mid])


@pytest.mark.asyncio
async def test_own_host_file_is_checked(db, db_session_factory, storage, monkeypatch):
    """Картинка со своего сайта проверяется — это наш контент, чинить нам."""
    monkeypatch.setenv("LINK_AUDIT_OWN_HOSTS", "victor-komlev.ru")
    url = f"https://victor-komlev.ru/wp-content/uploads/2026/07/tsk521-{uuid4().hex}.png"

    class _Resp:
        def __init__(self, code: int) -> None:
            self.status_code = code

    async def _head(self, target, **kwargs):
        # Остальной контент dev-БД считаем целым — проверяем свою ссылку.
        return _Resp(404 if target == url else 200)

    monkeypatch.setattr("httpx.AsyncClient.head", _head)

    course_id = await _pick_root(db)
    content = {"text": f'<img src="{url}"/>', "format": "html"}
    mid = await _create_material(db, course_id=course_id, content=content)
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert summary["broken_targets"].get(url) == "HTTP 404", summary
    finally:
        await _cleanup(db, material_ids=[mid])


@pytest.mark.asyncio
async def test_page_link_is_not_checked(db, db_session_factory, storage, monkeypatch):
    """Ссылка на страницу своего сайта — не файл, проверять нечего."""
    monkeypatch.setenv("LINK_AUDIT_OWN_HOSTS", "victor-komlev.ru")
    page = f"https://victor-komlev.ru/kurs-tsk521-{uuid4().hex}/"

    class _Resp:
        status_code = 200

    async def _head(self, target, **kwargs):
        assert target != page, "страница не должна проверяться как файл"
        return _Resp()

    monkeypatch.setattr("httpx.AsyncClient.head", _head)

    course_id = await _pick_root(db)
    content = {"text": f'<a href="{page}">курс</a>', "format": "html"}
    mid = await _create_material(db, course_id=course_id, content=content)
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert page not in summary["broken_targets"], summary
    finally:
        await _cleanup(db, material_ids=[mid])


@pytest.mark.asyncio
async def test_inactive_material_is_skipped(db, db_session_factory, storage, no_own_hosts):
    """Выключенный материал ученику не показывается — и в проверку не идёт."""
    course_id = await _pick_root(db)
    file_id = _new_name()
    storage.add(file_id)
    mid = await _create_material(
        db, course_id=course_id, content=_file_content(file_id), is_active=False
    )
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert f"material:{file_id}" not in summary["broken_targets"], summary
    finally:
        await _cleanup(db, material_ids=[mid])


# ─── частота уведомлений ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_second_tick_within_cooldown_stays_quiet(
    db, db_session_factory, storage, no_own_hosts
):
    """Ежедневный тик не превращает одну незамеченную ссылку в поток писем."""
    course_id = await _pick_root(db)
    file_id = _new_name()
    storage.add(file_id)
    mid = await _create_material(db, course_id=course_id, content=_file_content(file_id))
    try:
        first = await link_audit_service.link_audit_tick(db_session_factory)
        second = await link_audit_service.link_audit_tick(db_session_factory)
        assert first["notified"] >= 1, first
        assert f"material:{file_id}" in second["broken_targets"], "находка никуда не делась"
        assert second["notified"] == 0, "повторное уведомление в пределах отсрочки"
    finally:
        await _cleanup(db, material_ids=[mid])


# ─── отказ хранилища ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_outage_aborts_run_instead_of_flagging_everything(
    db, db_session_factory, monkeypatch, no_own_hosts
):
    """Хранилище недоступно — прогон прерывается.

    «Нет ответа» не то же самое, что «файла нет»: иначе одна сетевая ошибка
    объявила бы битыми все ссылки разом и завалила методиста ложной тревогой.
    """
    course_id = await _pick_root(db)
    file_id = _new_name()
    mid = await _create_material(db, course_id=course_id, content=_file_content(file_id))

    async def _outage(file_id: str) -> bool:
        raise DomainError("Хранилище файлов недоступно", status_code=503)

    monkeypatch.setattr(
        link_audit_service.material_files_storage, "material_file_exists", _outage
    )
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert summary["broken"] == 0, summary
        assert summary.get("error"), "прогон обязан пометить себя недостоверным"
        assert await _notifications(db) == [], "ложная тревога отправлена"
    finally:
        await _cleanup(db, material_ids=[mid])


@pytest.mark.asyncio
async def test_unexpected_failure_is_not_passed_off_as_clean_run(
    db, db_session_factory, monkeypatch, no_own_hosts
):
    """Программная ошибка внутри проверки помечает прогон, а не молчит."""
    course_id = await _pick_root(db)
    file_id = _new_name()
    mid = await _create_material(db, course_id=course_id, content=_file_content(file_id))

    async def _boom(file_id: str) -> bool:
        raise RuntimeError("что-то пошло не так")

    monkeypatch.setattr(
        link_audit_service.material_files_storage, "material_file_exists", _boom
    )
    try:
        summary = await link_audit_service.link_audit_tick(db_session_factory)
        assert "RuntimeError" in str(summary.get("error")), summary
        assert await _notifications(db) == []
    finally:
        await _cleanup(db, material_ids=[mid])


# ─── разбор своих доменов ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url, hosts, expected",
    [
        ("https://victor-komlev.ru/a.png", ["victor-komlev.ru"], True),
        ("https://www.victor-komlev.ru/a.png", ["victor-komlev.ru"], True),
        ("https://api.learn.victor-komlev.ru/a.png", ["victor-komlev.ru"], True),
        ("https://kompege.ru/a.png", ["victor-komlev.ru"], False),
        # Домен-ловушка: срезать «www.» через lstrip нельзя — он режет любые
        # символы из набора {w, .} и превращает `wiki.ru` в `iki.ru`.
        ("https://wiki.ru/a.png", ["wiki.ru"], True),
        ("https://iki.ru/a.png", ["wiki.ru"], False),
    ],
)
def test_own_host_detection(url, hosts, expected):
    assert link_audit_service._is_own_host(url, hosts) is expected


# Защита от двойного запуска (advisory-lock) проверяется отдельным модулем
# `test_tsk521_link_audit_lock.py`: там нужны два НЕЗАВИСИМЫХ соединения, а
# значит собственный engine — и весь такой модуль выпадает из транзакционной
# изоляции. Держать из-за одного теста без изоляции ещё девять не за что.
