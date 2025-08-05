from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class AccessRequestFlag(str, Enum):
    completed = "completed"
    rejected = "rejected"
    not_ready = "not_ready"
    wait_for_pay = 'wait for pay',
    pay_completed = 'pay completed',


class AccessRequestBase(BaseModel):
    user_id: int = Field(..., description="ID пользователя")
    role_id: int = Field(..., description="ID запрашиваемой роли")
    flag: AccessRequestFlag = Field(
        AccessRequestFlag.not_ready, description="Статус запроса"
    )


class AccessRequestCreate(AccessRequestBase):
    """
    Схема создания — тот же набор полей, но без id и requested_at.
    """


class AccessRequestUpdate(BaseModel):
    """
    Для обновления разрешаем менять только флаг.
    """
    flag: AccessRequestFlag = Field(..., description="Новый статус запроса")

    model_config = ConfigDict(extra="forbid")


class AccessRequestRead(AccessRequestBase):
    """
    Схема чтения — возвращает все поля, включая id и requested_at.
    """
    id: int
    requested_at: datetime

    model_config = ConfigDict(from_attributes=True)

class AccessRequestReadDetailed(AccessRequestRead):
    """
    Все поля AccessRequestRead + full_name пользователя и name роли.
    """
    user_full_name: str
    role_name: str

    model_config = ConfigDict(from_attributes=True)
