"""tsk-010 — приём оплаты: факт денег поверх посчитанного начисления.

Проверяем не «эндпоинт отвечает 200», а денежные инварианты: платёж попадает
ровно в своё начисление, частичная оплата не выдаётся за полную, решение по
платежу принимается один раз, чужие суммы и чеки не видны, а принятые деньги
не исчезают вместе со строкой месяца при пересчёте.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services import charge_service, payment_service, user_merge_service
from tests.test_tsk505_marketer_pricing import (
    _auth,
    _enroll,
    _new_course,
    _new_group,
    _new_user,
    _price_course,
)
from tests.test_tsk511_charges_breaks import _setup, PERIOD

pytestmark = pytest.mark.asyncio

_RECEIPT = ("cheque.png", b"\x89PNG\r\n\x1a\n test receipt", "image/png")


def _receipt_files() -> set[str]:
    """Что сейчас лежит в каталоге чеков — чтобы ловить мусор после отказов."""
    upload_dir = Settings().payment_receipts_upload_dir
    if not upload_dir.exists():
        return set()
    return {p.name for p in upload_dir.iterdir() if p.is_file()}


async def _charge_id(db, *, student_id: int, period: date = PERIOD) -> int:
    row = (
        await db.execute(
            text(
                "SELECT id FROM student_monthly_charge "
                "WHERE student_id = :s AND period = :p"
            ),
            {"s": student_id, "p": period},
        )
    ).first()
    assert row is not None, "начисление не заведено — проверь настройку теста"
    return int(row.id)


async def _recalc(db, *, student_id: int, period: date = PERIOD) -> None:
    await charge_service.recalculate_for_student(db, student_id=student_id, period=period)


async def _submit(
    client,
    token: str,
    *,
    charge_id: int,
    amount_minor: int,
    paid_on: date | None = None,
    note: str | None = None,
):
    data = {"charge_id": str(charge_id), "amount_minor": str(amount_minor)}
    if paid_on is not None:
        data["paid_on"] = paid_on.isoformat()
    if note is not None:
        data["payer_note"] = note
    return await client.post(
        "/api/v1/me/payments",
        data=data,
        files={"file": _RECEIPT},
        headers=_auth(token),
    )


async def _marketer_charge(client, token: str, student_id: int) -> dict:
    resp = await client.get(
        f"/api/v1/marketer/charges?period={PERIOD.isoformat()}", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    found = next((c for c in resp.json() if c["student_id"] == student_id), None)
    assert found is not None
    return found


async def test_partial_payment_leaves_the_rest_as_debt(db, client):
    """Частичная оплата уменьшает долг, но не выдаёт месяц за оплаченный."""
    env = await _setup(db, "pay-partial", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    total = (await _marketer_charge(client, env["token"], env["student_id"]))["total_minor"]
    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=total // 2)
    assert resp.status_code == 201, resp.text
    payment_id = resp.json()["id"]

    # Пока платёж ждёт решения — он не долг, но и не оплата.
    row = await _marketer_charge(client, env["token"], env["student_id"])
    assert row["paid_minor"] == 0
    assert row["pending_minor"] == total // 2
    assert row["due_minor"] == total

    confirmed = await client.post(
        f"/api/v1/marketer/payments/{payment_id}/confirm",
        json={},
        headers=_auth(env["token"]),
    )
    assert confirmed.status_code == 200, confirmed.text

    row = await _marketer_charge(client, env["token"], env["student_id"])
    assert row["paid_minor"] == total // 2
    assert row["pending_minor"] == 0
    assert row["due_minor"] == total - total // 2, "остаток обязан остаться долгом"


async def test_payment_lands_in_its_own_charge(db, client):
    """Платёж привязывается к своей паре «группа + месяц», а не к соседней."""
    env = await _setup(db, "pay-target", price=550000)
    await _recalc(db, student_id=env["student_id"])
    await _recalc(db, student_id=env["student_id"], period=charge_service.next_month(PERIOD))
    this_month = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=this_month, amount_minor=100000)
    assert resp.status_code == 201, resp.text
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    paid_period = (
        await db.execute(
            text("SELECT period, group_id FROM student_payment WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert paid_period.period == PERIOD
    assert paid_period.group_id == env["group_id"]

    # Соседний месяц остался нетронутым.
    next_row = (
        await db.execute(
            text(
                "SELECT id FROM student_monthly_charge "
                "WHERE student_id = :s AND period = :p"
            ),
            {"s": env["student_id"], "p": charge_service.next_month(PERIOD)},
        )
    ).first()
    assert next_row is not None
    resp = await client.get(
        f"/api/v1/marketer/charges?period={charge_service.next_month(PERIOD).isoformat()}",
        headers=_auth(env["token"]),
    )
    neighbour = next(
        c for c in resp.json() if c["student_id"] == env["student_id"]
    )
    assert neighbour["paid_minor"] == 0


async def test_decision_is_made_once(db, client):
    """Повторное решение по тому же платежу не проходит.

    Иначе два маркетолога, открывшие очередь одновременно, переписали бы
    решение друг друга, а сумма оплаченного зависела бы от порядка кликов.
    """
    env = await _setup(db, "pay-once", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=100000)
    payment_id = resp.json()["id"]

    first = await client.post(
        f"/api/v1/marketer/payments/{payment_id}/confirm",
        json={},
        headers=_auth(env["token"]),
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/marketer/payments/{payment_id}/reject",
        json={"note": "передумал"},
        headers=_auth(env["token"]),
    )
    assert second.status_code == 404, "решение по платежу принимается один раз"

    row = await _marketer_charge(client, env["token"], env["student_id"])
    assert row["paid_minor"] == 100000


async def test_rejected_payment_is_not_money(db, client):
    """Отклонённый платёж не считается ни оплатой, ни заявкой."""
    env = await _setup(db, "pay-reject", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=550000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/reject",
        json={"note": "чек не читается"},
        headers=_auth(env["token"]),
    )

    row = await _marketer_charge(client, env["token"], env["student_id"])
    assert row["paid_minor"] == 0
    assert row["pending_minor"] == 0
    assert row["due_minor"] == row["total_minor"]

    # Причину отказа ученик видит — иначе отказ выглядит как сбой.
    mine = await client.get("/api/v1/me/charges", headers=_auth(student_token))
    payment = mine.json()[0]["payments"][0]
    assert payment["status"] == "rejected"
    assert payment["review_note"] == "чек не читается"


async def test_student_cannot_pay_someone_elses_charge(db, client):
    """Чужое начисление недоступно — и отвечает так же, как несуществующее."""
    mine = await _setup(db, "pay-mine", price=550000)
    other = await _setup(db, "pay-other", price=550000)
    await _recalc(db, student_id=other["student_id"])
    other_charge = await _charge_id(db, student_id=other["student_id"])
    _, student_token = await _login_as(db, mine["student_id"])

    resp = await _submit(client, student_token, charge_id=other_charge, amount_minor=100000)
    assert resp.status_code == 404

    missing = await _submit(client, student_token, charge_id=10**7, amount_minor=100000)
    assert missing.status_code == 404
    assert missing.json()["detail"] == resp.json()["detail"], (
        "ответ про чужое и про несуществующее начисление обязан совпадать"
    )


async def test_receipt_is_not_readable_by_other_students(db, client):
    """Чек видит только плательщик и маркетолог."""
    env = await _setup(db, "pay-receipt", price=550000)
    stranger = await _setup(db, "pay-stranger", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, owner_token = await _login_as(db, env["student_id"])
    _, stranger_token = await _login_as(db, stranger["student_id"])

    resp = await _submit(client, owner_token, charge_id=charge_id, amount_minor=100000)
    payment_id = resp.json()["id"]

    own = await client.get(
        f"/api/v1/me/payments/{payment_id}/receipt", headers=_auth(owner_token)
    )
    assert own.status_code == 200

    foreign = await client.get(
        f"/api/v1/me/payments/{payment_id}/receipt", headers=_auth(stranger_token)
    )
    assert foreign.status_code == 404

    by_marketer = await client.get(
        f"/api/v1/marketer/payments/{payment_id}/receipt", headers=_auth(env["token"])
    )
    assert by_marketer.status_code == 200


async def test_receipt_is_served_by_disk_type_not_by_sent_name(db, client):
    """Тип отдаваемого чека берётся с диска, а не из присланного имени.

    Файл с именем `evil.svg`, загруженный под видом картинки, не должен
    вернуться как `image/svg+xml`: это активное содержимое.
    """
    env = await _setup(db, "pay-mime-out", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await client.post(
        "/api/v1/me/payments",
        data={"charge_id": str(charge_id), "amount_minor": "100000"},
        files={"file": ("evil.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/png")},
        headers=_auth(student_token),
    )
    assert resp.status_code == 201, resp.text
    payment_id = resp.json()["id"]

    for url, token in (
        (f"/api/v1/me/payments/{payment_id}/receipt", student_token),
        (f"/api/v1/marketer/payments/{payment_id}/receipt", env["token"]),
    ):
        got = await client.get(url, headers=_auth(token))
        assert got.status_code == 200, got.text
        assert "svg" not in got.headers["content-type"], got.headers["content-type"]
        assert got.headers["content-type"].startswith("image/png")


async def test_student_cannot_reach_marketer_queue(db, client):
    """Очередь подтверждения закрыта от ученика."""
    env = await _setup(db, "pay-gate", price=550000)
    _, student_token = await _login_as(db, env["student_id"])

    resp = await client.get("/api/v1/marketer/payments", headers=_auth(student_token))
    assert resp.status_code == 403


async def test_parent_pays_for_child_and_sees_only_own_child(db, client):
    """Родитель платит за своего ребёнка и не видит чужих."""
    env = await _setup(db, "pay-parent", price=550000)
    stranger = await _setup(db, "pay-parent-other", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])

    parent_id, parent_token = await _new_user(db, role="parent", name="parent-010")
    await db.execute(
        text(
            "INSERT INTO parent_student_links (parent_id, student_id) VALUES (:p, :s)"
        ),
        {"p": parent_id, "s": env["student_id"]},
    )
    await db.commit()

    resp = await _submit(client, parent_token, charge_id=charge_id, amount_minor=250000)
    assert resp.status_code == 201, resp.text

    seen = await client.get(
        f"/api/v1/me/charges?student_id={env['student_id']}", headers=_auth(parent_token)
    )
    assert seen.status_code == 200
    assert seen.json()[0]["pending_minor"] == 250000

    denied = await client.get(
        f"/api/v1/me/charges?student_id={stranger['student_id']}",
        headers=_auth(parent_token),
    )
    assert denied.status_code == 404


async def test_paid_charge_survives_recalculation(db, client):
    """Месяц с принятыми деньгами не исчезает, когда считать стало не из чего.

    Пересчёт умеет удалять открытую строку месяца; вместе с ней ушли бы и
    привязанные к ней платежи.
    """
    env = await _setup(db, "pay-survive", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=550000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    # Курс перестал продаваться — считать месяц больше не из чего.
    # Группа снимается вместе со статусом: непроданный курс без тарифной
    # группы — того требует проверка в самой таблице цен.
    await db.execute(
        text(
            "UPDATE course_pricing SET sale_status = 'not_for_sale', group_id = NULL "
            "WHERE course_id = :c"
        ),
        {"c": env["course_id"]},
    )
    await db.commit()
    await charge_service.recalculate_student_group(
        db, student_id=env["student_id"], group_id=env["group_id"], period=PERIOD
    )
    await db.commit()

    still_there = (
        await db.execute(
            text("SELECT id FROM student_monthly_charge WHERE id = :id"),
            {"id": charge_id},
        )
    ).first()
    assert still_there is not None, "начисление с оплатой удалять нельзя"

    payment_rows = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert payment_rows.n == 1


async def test_export_lists_confirmed_by_payment_date(db, client):
    """Выгрузка для сверки с «Мой налог» идёт по дню платежа."""
    env = await _setup(db, "pay-export", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    paid_on = date.today() - timedelta(days=3)
    resp = await _submit(
        client, student_token, charge_id=charge_id, amount_minor=550000, paid_on=paid_on
    )
    payment_id = resp.json()["id"]

    # До подтверждения выгрузка пуста: сверять нечего, деньги ещё не приняты.
    empty = await client.get(
        f"/api/v1/marketer/payments/export?date_from={paid_on}&date_to={date.today()}",
        headers=_auth(env["token"]),
    )
    assert empty.status_code == 200
    assert all(r["id"] != payment_id for r in empty.json())

    await client.post(
        f"/api/v1/marketer/payments/{payment_id}/confirm",
        json={},
        headers=_auth(env["token"]),
    )
    listed = await client.get(
        f"/api/v1/marketer/payments/export?date_from={paid_on}&date_to={date.today()}",
        headers=_auth(env["token"]),
    )
    row = next(r for r in listed.json() if r["id"] == payment_id)
    assert row["on_date"] == paid_on.isoformat()

    # За пределами периода платежа его в выгрузке нет.
    outside = await client.get(
        f"/api/v1/marketer/payments/export?date_from={date.today()}&date_to={date.today()}",
        headers=_auth(env["token"]),
    )
    assert all(r["id"] != payment_id for r in outside.json())


async def test_receipt_rejects_foreign_file_type(db, client):
    """Чеком принимается изображение или PDF, а не что угодно."""
    env = await _setup(db, "pay-mime", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await client.post(
        "/api/v1/me/payments",
        data={"charge_id": str(charge_id), "amount_minor": "100000"},
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/x-msdownload")},
        headers=_auth(student_token),
    )
    assert resp.status_code == 415


async def test_second_identical_receipt_is_refused(db, client):
    """Двойное нажатие «отправить» не превращается в двойные деньги.

    Ответ мог потеряться по дороге, и человек нажимает ещё раз. Пропустив
    второй чек, мы заведём вторые деньги, которые маркетолог подтвердит, не
    имея повода усомниться: в очереди две одинаковые строки выглядят нормально.
    """
    env = await _setup(db, "pay-dup", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    paid_on = date.today()
    first = await _submit(
        client, student_token, charge_id=charge_id, amount_minor=550000, paid_on=paid_on
    )
    assert first.status_code == 201, first.text

    second = await _submit(
        client, student_token, charge_id=charge_id, amount_minor=550000, paid_on=paid_on
    )
    assert second.status_code == 409, second.text

    rows = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert rows.n == 1

    # После решения по первому такая же сумма законна: это доплата равными частями.
    await client.post(
        f"/api/v1/marketer/payments/{first.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )
    third = await _submit(
        client, student_token, charge_id=charge_id, amount_minor=550000, paid_on=paid_on
    )
    assert third.status_code == 201, third.text


async def test_overpayment_is_visible(db, client):
    """Переплата не растворяется в «оплачено».

    Ученик мог ошибиться на порядок, а мог заплатить до того, как месяц
    подешевел из-за перерыва. И там, и там долг равен нулю — но деньги есть.
    """
    env = await _setup(db, "pay-over", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=1000000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    row = await _marketer_charge(client, env["token"], env["student_id"])
    assert row["due_minor"] == 0
    assert row["overpaid_minor"] == 1000000 - row["total_minor"]

    mine = await client.get("/api/v1/me/charges", headers=_auth(student_token))
    assert mine.json()[0]["overpaid_minor"] == 1000000 - row["total_minor"]


async def test_queue_shows_charge_amount_next_to_payment(db, client):
    """В очереди видно сумму месяца и остаток — иначе решение принимается вслепую."""
    env = await _setup(db, "pay-context", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    # Ошибка на порядок: 55 000 ₽ вместо 5 500 ₽.
    await _submit(client, student_token, charge_id=charge_id, amount_minor=5500000)

    queue = await client.get("/api/v1/marketer/payments?status=pending", headers=_auth(env["token"]))
    row = next(p for p in queue.json() if p["student_id"] == env["student_id"])
    assert row["amount_minor"] == 5500000
    assert row["charge_total_minor"] == 550000
    assert row["charge_due_minor"] == 550000, "остаток до подтверждения — весь месяц"


async def test_amount_over_limit_is_refused_and_leaves_no_file(db, client):
    """Сумма сверх допустимой отбивается, файл на диске не остаётся."""
    env = await _setup(db, "pay-huge", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    before = _receipt_files()
    resp = await _submit(
        client, student_token, charge_id=charge_id, amount_minor=10**12
    )
    assert resp.status_code == 422, resp.text
    assert _receipt_files() == before, "после отказа файл чека остался на диске"


async def test_service_key_is_locked_out_of_money(db, client):
    """Сервисный ключ в денежный контур не заходит — ни в чей кабинет."""
    settings = Settings()
    key = settings.valid_api_keys[0]
    headers = {"X-API-Key": key}

    assert (await client.get("/api/v1/me/charges", headers=headers)).status_code == 403
    assert (
        await client.get("/api/v1/marketer/payments", headers=headers)
    ).status_code == 403
    assert (
        await client.get("/api/v1/me/payments/1/receipt", headers=headers)
    ).status_code == 403


async def test_merge_stops_when_source_has_money(db, client):
    """Слияние учёток не уносит подтверждённые платежи на мёртвый аккаунт."""
    env = await _setup(db, "pay-merge", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await _submit(client, student_token, charge_id=charge_id, amount_minor=550000)
    await client.post(
        f"/api/v1/marketer/payments/{resp.json()['id']}/confirm",
        json={},
        headers=_auth(env["token"]),
    )

    target_id, _ = await _new_user(db, role="student", name="merge-target-010")
    merged = await user_merge_service.merge_users(
        db, source_id=env["student_id"], target_id=target_id
    )
    assert merged is False, "учётку с деньгами молча сливать нельзя"

    still_here = (
        await db.execute(
            text("SELECT count(*) AS n FROM student_payment WHERE student_id = :s"),
            {"s": env["student_id"]},
        )
    ).one()
    assert still_here.n == 1


async def test_cyrillic_receipt_name_survives(db, client):
    """Имя чека на русском не превращается в «png» — по нему разбирают спор."""
    env = await _setup(db, "pay-name", price=550000)
    await _recalc(db, student_id=env["student_id"])
    charge_id = await _charge_id(db, student_id=env["student_id"])
    _, student_token = await _login_as(db, env["student_id"])

    resp = await client.post(
        "/api/v1/me/payments",
        data={"charge_id": str(charge_id), "amount_minor": "550000"},
        files={"file": ("чек за август.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        headers=_auth(student_token),
    )
    assert resp.status_code == 201, resp.text

    stored = (
        await db.execute(
            text("SELECT receipt_name, receipt_file FROM student_payment WHERE id = :id"),
            {"id": resp.json()["id"]},
        )
    ).one()
    assert "август" in stored.receipt_name
    # На диск кладём расширение по подтверждённому типу, а не по имени.
    assert stored.receipt_file.endswith(".png")


def test_overdue_needs_grace_to_pass():
    """Просрочка наступает не раньше срока с запасом и не при поданном чеке."""
    period = date(2026, 9, 1)
    due = payment_service.due_date_for(period)
    grace_end = due + timedelta(days=payment_service.settings.payment_grace_days)

    on_grace = payment_service.payment_state(
        total_minor=550000, paid_minor=0, pending_minor=0, period=period, today=grace_end
    )
    assert on_grace.is_overdue is False

    after = payment_service.payment_state(
        total_minor=550000,
        paid_minor=0,
        pending_minor=0,
        period=period,
        today=grace_end + timedelta(days=1),
    )
    assert after.is_overdue is True

    # Чек приложен и ждёт решения — это не долг ученика, а наша очередь.
    waiting = payment_service.payment_state(
        total_minor=550000,
        paid_minor=0,
        pending_minor=550000,
        period=period,
        today=grace_end + timedelta(days=30),
    )
    assert waiting.is_overdue is False


def test_due_date_does_not_slip_into_next_month():
    """Срок оплаты остаётся внутри своего месяца даже при крупном числе."""
    payment_service.settings.payment_due_day = 31
    try:
        assert payment_service.due_date_for(date(2026, 2, 1)) == date(2026, 2, 28)
    finally:
        payment_service.settings.payment_due_day = 5


async def _login_as(db, user_id: int) -> tuple[int, str]:
    """Выдать сессионный токен уже существующему пользователю."""
    from app.services.auth.session_service import create_session

    token, _, _ = await create_session(db, user_id=user_id)
    await db.commit()
    return user_id, token
