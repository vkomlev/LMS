"""tsk-010 — оплата в кабинете ученика и родителя.

Ученик видит свои начисления и прикладывает чек; родитель делает то же за
своего ребёнка. Больше здесь ничего нет: подтверждает платёж маркетолог
(`marketer_payments`), а сумму месяца считает `charge_service`.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.payment import StudentChargeRead
from app.services import attachment_storage, payment_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
settings = Settings()

router = APIRouter(prefix="/me", tags=["me_payments"])

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9А-Яа-яЁё._-]")
#: Чек — это картинка или PDF. Всё остальное просто не откроется у маркетолога.
#: Расширение на диске берём отсюда же, а не из присланного имени: иначе
#: `evil.html` с заголовком картинки лёг бы на диск как .html.
_ALLOWED_RECEIPT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

#: Потолок суммы платежа. Колонка `amount_minor` — 4-байтовое целое, и число
#: сверх него роняло запись ошибкой БД уже после того, как файл лёг на диск.
MAX_PAYMENT_MINOR = 100_000_000  # 1 000 000 ₽


async def _drop_stored_receipt(stored_name: str) -> None:
    """Убрать записанный чек, если платёж завести не удалось.

    Иначе в хранилище копится чужой платёжный документ, которому в базе не
    соответствует ничего: удалить его потом будет нечем — ссылки-то нет.
    Сбой самой уборки не должен подменять исходную ошибку, ради которой её и
    затеяли, поэтому он только логируется.
    """
    try:
        await attachment_storage.delete(attachment_storage.RECEIPTS, stored_name)
    except Exception:
        logger.warning(
            "tsk-593: не удалось убрать чек из хранилища имя=%s", stored_name, exc_info=True
        )


def _safe_name(filename: Optional[str]) -> str:
    """Имя для показа человеку: кириллицу сохраняем, служебные символы гасим.

    Раньше здесь оставались только латиница и цифры, и «чек за август.png»
    превращался в «png» — у маркетолога все чеки назывались одинаково, а имя
    оригинала для разбора спора терялось.
    """
    base = Path(filename or "receipt").name
    safe = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    return safe or "receipt"


async def receipt_response(payment: dict) -> StreamingResponse:
    """Отдать файл чека.

    Тип содержимого выводим из ИМЕНИ В ХРАНИЛИЩЕ, а не из имени, присланного
    загрузившим: расширение мы поставили сами по подтверждённому типу, а
    присланное имя — чужой ввод. Иначе `evil.svg`, загруженный под видом
    картинки, уезжал бы обратно как `image/svg+xml` — то есть активным
    содержимым, и вся защита держалась бы на одном заголовке «скачать».

    tsk-593: чек лежит в объектном хранилище, в СВОЁМ пространстве ключей
    (`receipts/`), отдельно от учебных вложений — это платёжный документ с
    другим кругом читателей. Наружу он идёт только потоком через эту функцию:
    вызывающие эндпоинты проверяют, что смотрит либо владелец начисления
    (ученик или его родитель), либо маркетолог. Прямая ссылка на бакет не
    выдаётся ни при каких условиях.
    """
    stored_name = os.path.basename(payment["receipt_file"])
    try:
        opened = await attachment_storage.open_stream(
            attachment_storage.RECEIPTS, stored_name
        )
    except DomainError as exc:
        raise HTTPException(exc.status_code, exc.detail)

    if opened is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл чека не найден")

    stream, media_type = opened
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={
            "Content-Disposition": attachment_storage.content_disposition(
                payment["receipt_name"] or "receipt"
            )
        },
    )


async def _resolve_student(
    db: AsyncSession, *, current_user: CurrentUser, student_id: Optional[int]
) -> int:
    """Чей кабинет открыт: свой или ребёнка.

    Родитель обязан назвать ребёнка явно и только из своих привязок — иначе
    перебором номеров всплыли бы чужие суммы и фамилии.
    """
    if student_id is None or student_id == current_user.id:
        return current_user.id
    allowed = await payment_service.student_ids_for_parent(db, parent_id=current_user.id)
    if student_id not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    return student_id


@router.get(
    "/charges",
    response_model=list[StudentChargeRead],
    summary="Мои начисления и оплаты",
    description=(
        "Начисления по месяцам: сколько начислено, сколько уже оплачено, "
        "сколько ждёт подтверждения и что осталось. Родитель смотрит начисления "
        "ребёнка, передав его student_id."
    ),
)
async def my_charges(
    student_id: Optional[int] = Query(
        default=None, description="Ребёнок родителя; своё — не указывать"
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[StudentChargeRead]:
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Начисления доступны только пользователю, не сервисному ключу",
        )
    target = await _resolve_student(db, current_user=current_user, student_id=student_id)
    rows = await payment_service.list_student_charges(db, student_id=target)
    return [StudentChargeRead(**r) for r in rows]


@router.post(
    "/payments",
    status_code=status.HTTP_201_CREATED,
    summary="Приложить чек об оплате",
    description=(
        "Заявка уходит маркетологу на подтверждение. Долгом такая сумма уже не "
        "считается, но и оплаченной становится только после подтверждения."
    ),
)
async def submit_payment(
    charge_id: int = Form(..., description="Начисление, за которое платят"),
    amount_minor: int = Form(..., gt=0, description="Сумма платежа в копейках"),
    paid_on: Optional[date] = Form(default=None, description="День платежа"),
    payer_note: Optional[str] = Form(default=None, max_length=500),
    file: UploadFile = File(..., description="Чек: изображение или PDF"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Оплата принимается только от пользователя, не от сервисного ключа",
        )
    if paid_on is not None and paid_on > date.today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Дата платежа не может быть в будущем"
        )
    # Начисление ищем от лица платящего: родитель платит за ребёнка, поэтому
    # владельцем начисления должен оказаться ребёнок, а не сам родитель.
    charge = await payment_service.charge_for_student(
        db, charge_id=charge_id, student_id=current_user.id
    )
    if charge is None:
        for child_id in await payment_service.student_ids_for_parent(
            db, parent_id=current_user.id
        ):
            charge = await payment_service.charge_for_student(
                db, charge_id=charge_id, student_id=child_id
            )
            if charge is not None:
                break
    if charge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Начисление не найдено")

    return await store_receipt_payment(
        db,
        charge=charge,
        amount_minor=amount_minor,
        paid_on=paid_on,
        payer_note=payer_note,
        file=file,
        submitted_by=current_user.id,
    )


async def store_receipt_payment(
    db: AsyncSession,
    *,
    charge: dict,
    amount_minor: int,
    paid_on: Optional[date],
    payer_note: Optional[str],
    file: UploadFile,
    submitted_by: Optional[int],
) -> dict:
    """Сохранить чек на диск и завести платёж.

    Вынесено из роутера, потому что тем же путём идёт оплата родителя по
    гостевой ссылке (tsk-010): у него нет учётной записи, и `submitted_by`
    пустой — но проверки файла, размера и дубля должны быть теми же. Две копии
    этого кода разъехались бы ровно на той проверке, про которую забыли: при
    выносе так и вышло — публичный контур остался без проверки типа файла и
    предела суммы, пока это не поймал тест.
    """
    if amount_minor > MAX_PAYMENT_MINOR:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Сумма платежа больше допустимой ({MAX_PAYMENT_MINOR // 100} ₽)",
        )
    extension = _ALLOWED_RECEIPT_TYPES.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Чек принимается изображением (JPEG, PNG, HEIC, WebP) или файлом PDF",
        )

    original_name = _safe_name(file.filename)
    # Расширение — от подтверждённого типа, а не от присланного имени.
    stored_name = f"{charge['student_id']}_{uuid4().hex}{extension}"

    # tsk-593: чек уходит в объектное хранилище. До этого он лежал на диске
    # приложения — на том же корневом разделе, где уже потеряли все файлы
    # материалов при переезде машины (tsk-519). Для платёжного документа это
    # хуже вдвойне: спор об оплате разбирают именно по нему.
    try:
        await attachment_storage.store_upload(
            attachment_storage.RECEIPTS, stored_name, file
        )
    except DomainError as exc:
        raise HTTPException(exc.status_code, exc.detail)

    try:
        payment_id = await payment_service.create_manual_payment(
            db,
            student_id=charge["student_id"],
            group_id=charge["group_id"],
            period=charge["period"],
            amount_minor=amount_minor,
            paid_on=paid_on,
            payer_note=(payer_note or "").strip() or None,
            receipt_file=stored_name,
            receipt_name=original_name,
            submitted_by=submitted_by,
        )
    except payment_service.DuplicatePaymentError:
        await _drop_stored_receipt(stored_name)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Такой чек уже отправлен и ждёт подтверждения — второй раз отправлять не нужно",
        ) from None
    except Exception:
        await _drop_stored_receipt(stored_name)
        logger.exception(
            "tsk-010: не удалось записать платёж ученика %s за %s",
            charge["student_id"],
            charge["period"],
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Не удалось принять платёж, попробуйте ещё раз"
        ) from None
    return {"id": payment_id, "status": "pending"}


@router.get(
    "/payments/{payment_id}/receipt",
    summary="Скачать свой чек",
)
async def download_own_receipt(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Чеки доступны только пользователю, не сервисному ключу",
        )
    payment = await payment_service.get_receipt(db, payment_id=payment_id)
    if payment is None or payment["receipt_file"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Чек не найден")
    allowed = {current_user.id, *await payment_service.student_ids_for_parent(
        db, parent_id=current_user.id
    )}
    if payment["student_id"] not in allowed:
        # Тот же ответ, что и на несуществующий чек: разница в ответах сама по
        # себе рассказала бы, какие номера платежей заняты.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Чек не найден")
    return await receipt_response(payment)
