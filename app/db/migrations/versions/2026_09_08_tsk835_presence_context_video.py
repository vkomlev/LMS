"""tsk-835: контекст присутствия «video» — просмотр видео не молчание.

Датчик простоя (tsk-591) считает признаком жизни действие руками: касание,
ввод, прокрутка. Видео в уроке встроено кросс-доменным плеером ВК
(`vk.com/video_ext.php` в iframe), и нажатия внутри него до страницы не
доходят вовсе — браузер их туда не пускает. Ученик двадцать минут смотрит
разбор, ни одного события на странице нет, и через десять минут преподаватель
получает «не появляется в кабинете».

Это не теория: из 51 эпизода простоя на бою **21 пришёлся на просмотр видео**
(ученик закрыл видеоматериал прямо в окне эпизода), из них 14 — «открыл и
молчит». Уведомлений `student_idle` ушло 122, непрочитанными остались 99: сигнал
и без того тонет, а две пятых его — ложь.

Здесь только расширение перечня допустимых значений `student_presence.context`:
кабинет начнёт присылать `video`, когда на странице открыт видеоплеер, а тик
простоя по этому контексту тревогу «молчит» поднимать не будет. Данные не
меняются, значений `video` в таблице пока нет.

Откат безопасен ровно потому, что старых строк с `video` не существует: перед
возвратом прежнего ограничения такие строки переводятся в `material` — это их
честный смысл (видео и есть материал), и ни одна строка не теряется.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "tsk835_presence_context_video"
down_revision: Union[str, None] = "tsk813_task_order_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "student_presence_context_check"
_TABLE = "student_presence"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "context IS NULL OR context IN ('task', 'material', 'course', 'video', 'other')",
    )


def downgrade() -> None:
    # Строки с новым значением не должны мешать возврату ограничения.
    op.execute(
        "UPDATE student_presence SET context = 'material' WHERE context = 'video'"
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "context IS NULL OR context IN ('task', 'material', 'course', 'other')",
    )
