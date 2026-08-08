"""tsk-593: чек об оплате уезжает в объектное хранилище, в своё пространство.

Чек — платёжный документ: по нему разбирают спор об оплате. На диске
приложения он не переживёт переезд машины (так уже потеряли все файлы
материалов, tsk-519), а лежать в одной куче с учебными вложениями не должен —
у него другой круг читателей.

Покрываем:
- (а) чек попадает в бакет под префикс `receipts/`, на диске его нет;
- (б) владелец скачивает его потоком через приложение — ссылка на бакет
      наружу не выдаётся;
- (в) посторонний ученик не получает чужой чек и в новом хранилище;
- (г) не заведённый платёж (дубль) не оставляет чек-сироту в хранилище.
"""
from __future__ import annotations

import pytest

from app.services import attachment_storage
from tests.test_tsk010_payments import (
    _charge_id,
    _recalc,
    _submit,
)
from tests.test_tsk505_marketer_pricing import _auth, _new_user
from tests.test_tsk511_charges_breaks import _setup
from tests.test_tsk593_attachment_storage import _FakeS3, fake_s3  # noqa: F401
from tests.test_tsk010_payments import _login_as

pytestmark = pytest.mark.asyncio


async def test_receipt_lands_in_its_own_space(db, client, fake_s3):  # noqa: F811
    """Чек лежит под `receipts/`, а не рядом с работами учеников и не на диске."""
    env = await _setup(db, "tsk593-receipt")
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=1000)
    assert resp.status_code == 201, resp.text

    receipt_keys = [k for k in fake_s3.objects if k.startswith("receipts/")]
    assert len(receipt_keys) == 1, f"чек не попал в своё пространство: {list(fake_s3.objects)}"
    assert not [k for k in fake_s3.objects if k.startswith("attempts/")]

    stored_name = receipt_keys[0].split("/", 1)[1]
    on_disk = attachment_storage.local_dir(attachment_storage.RECEIPTS) / stored_name
    assert not on_disk.exists(), "чек всё ещё ложится на диск приложения"


async def test_owner_downloads_receipt_as_stream(db, client, fake_s3):  # noqa: F811
    """Файл идёт через приложение: прямой ссылки на бакет человек не получает."""
    env = await _setup(db, "tsk593-receipt-get")
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    submitted = await _submit(client, student_token, charge_id=charge_id, amount_minor=1000)
    payment_id = submitted.json()["id"]

    resp = await client.get(
        f"/api/v1/me/payments/{payment_id}/receipt", headers=_auth(student_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"\x89PNG\r\n\x1a\n test receipt"
    # Тип — по имени в хранилище, а не по присланному клиентом заголовку.
    assert resp.headers["content-type"].startswith("image/png")
    assert "s3.test" not in str(resp.headers)


async def test_stranger_still_cannot_read_receipt(db, client, fake_s3):  # noqa: F811
    """Переезд в хранилище не должен ослабить проверку прав."""
    env = await _setup(db, "tsk593-receipt-acl")
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    submitted = await _submit(client, student_token, charge_id=charge_id, amount_minor=1000)
    payment_id = submitted.json()["id"]

    stranger_id, _ = await _new_user(db, role="student", name="tsk593-stranger")
    _, stranger_token = await _login_as(db, stranger_id)
    resp = await client.get(
        f"/api/v1/me/payments/{payment_id}/receipt", headers=_auth(stranger_token)
    )
    assert resp.status_code == 404, resp.text


async def test_refused_duplicate_leaves_no_orphan_file(db, client, fake_s3):  # noqa: F811
    """Платёж не завёлся — чек в хранилище не остаётся: ссылки на него нет ни у кого."""
    env = await _setup(db, "tsk593-receipt-dup")
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    first = await _submit(client, student_token, charge_id=charge_id, amount_minor=1000)
    assert first.status_code == 201, first.text
    before = {k for k in fake_s3.objects if k.startswith("receipts/")}

    second = await _submit(client, student_token, charge_id=charge_id, amount_minor=1000)
    assert second.status_code == 409, second.text
    after = {k for k in fake_s3.objects if k.startswith("receipts/")}
    assert after == before, "после отказа в хранилище остался чек-сирота"
