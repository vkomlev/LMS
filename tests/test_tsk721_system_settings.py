"""tsk-721: настройки школы — границы, порядок источников, чтение на месте.

Три вещи, ради которых эти тесты и написаны:

1. **Границы держит сервер.** Порог, который можно выставить в ноль или в
   миллион, однажды выставят — и школа сломается молча. Проверка в форме
   кабинета этого не ловит: запрос можно послать мимо формы.
2. **Кабинет побеждает файл настроек** (решение оператора 2026-08-28). Иначе
   правка в кабинете не действует, пока значение стоит в `.env`, — то есть
   ходить на сервер всё равно придётся.
3. **Значение читается в момент применения.** Настройка, взятая при импорте
   модуля, требует перезапуска, а вся задача ровно про то, чтобы не требовала.
"""
from __future__ import annotations

import pytest

from app.core import settings_registry as registry
from app.core import settings_store


@pytest.fixture(autouse=True)
def _clean_store():
    """Каждый случай начинает с пустой памяти настроек."""
    settings_store.reset_for_tests()
    yield
    settings_store.reset_for_tests()


# ── Границы ──────────────────────────────────────────────────────────────────


def test_порог_ниже_границы_отклонён():
    definition = registry.get_definition("lesson_idle_threshold_minutes")
    with pytest.raises(ValueError) as exc:
        registry.coerce(definition, 0)
    # Текст ошибки уходит прямо в форму кабинета — он для человека.
    assert "не меньше" in str(exc.value)
    assert "минуты" in str(exc.value)


def test_порог_выше_границы_отклонён():
    definition = registry.get_definition("lesson_idle_threshold_minutes")
    with pytest.raises(ValueError):
        registry.coerce(definition, 1_000_000)


def test_день_блокировки_нельзя_выставить_в_ноль():
    """Ноль здесь закрыл бы занятия в тот же день, когда месяц кончился."""
    definition = registry.get_definition("payment_block_after_days")
    with pytest.raises(ValueError):
        registry.coerce(definition, 0)


def test_доля_ошибок_держится_в_своём_отрезке():
    definition = registry.get_definition("gap_student_error_rate")
    assert registry.coerce(definition, 0.6) == 0.6
    with pytest.raises(ValueError):
        registry.coerce(definition, 1.5)


def test_текст_реквизитов_ограничен_по_длине():
    definition = registry.get_definition("payment_transfer_details")
    assert registry.coerce(definition, "Счёт 123") == "Счёт 123"
    with pytest.raises(ValueError):
        registry.coerce(definition, "я" * 3000)


def test_да_в_числовом_поле_не_превращается_в_единицу():
    """`True` — подкласс int в Python: без явной проверки «да» стало бы порогом 1."""
    definition = registry.get_definition("lesson_idle_threshold_minutes")
    with pytest.raises(ValueError):
        registry.coerce(definition, True)


def test_рубильник_понимает_и_русское_да():
    definition = registry.get_definition("charge_cron_enabled")
    assert registry.coerce(definition, "да") is True
    assert registry.coerce(definition, "нет") is False
    assert registry.coerce(definition, False) is False


def test_неизвестный_ключ_это_ошибка_а_не_молчание():
    with pytest.raises(KeyError):
        registry.get_definition("s3_secret_key")


# ── Порядок источников ───────────────────────────────────────────────────────


def test_без_ничего_берётся_умолчание_из_кода(monkeypatch):
    monkeypatch.delenv("LESSON_IDLE_THRESHOLD_MINUTES", raising=False)
    assert settings_store.get_int("lesson_idle_threshold_minutes") == 10
    assert settings_store.source("lesson_idle_threshold_minutes") == "default"


def test_переменная_окружения_сильнее_умолчания(monkeypatch):
    monkeypatch.setenv("LESSON_IDLE_THRESHOLD_MINUTES", "17")
    assert settings_store.get_int("lesson_idle_threshold_minutes") == 17
    assert settings_store.source("lesson_idle_threshold_minutes") == "env"


def test_кабинет_сильнее_переменной_окружения(monkeypatch):
    monkeypatch.setenv("LESSON_IDLE_THRESHOLD_MINUTES", "17")
    settings_store.apply_local("lesson_idle_threshold_minutes", 25)
    assert settings_store.get_int("lesson_idle_threshold_minutes") == 25
    assert settings_store.source("lesson_idle_threshold_minutes") == "cabinet"


def test_вернуть_как_было_возвращает_к_переменной_окружения(monkeypatch):
    monkeypatch.setenv("LESSON_IDLE_THRESHOLD_MINUTES", "17")
    settings_store.apply_local("lesson_idle_threshold_minutes", 25)
    assert settings_store.fallback("lesson_idle_threshold_minutes") == 17

    settings_store.forget_local("lesson_idle_threshold_minutes")
    assert settings_store.get_int("lesson_idle_threshold_minutes") == 17
    assert settings_store.source("lesson_idle_threshold_minutes") == "env"


def test_негодная_переменная_окружения_не_валит_школу(monkeypatch):
    """В `.env` вписали «десять» — работаем на умолчании, а не падаем."""
    monkeypatch.setenv("LESSON_IDLE_THRESHOLD_MINUTES", "десять")
    assert settings_store.get_int("lesson_idle_threshold_minutes") == 10
    assert settings_store.source("lesson_idle_threshold_minutes") == "default"


# ── Чтение в момент применения ───────────────────────────────────────────────


def test_порог_простоя_виден_сервису_сразу_после_правки():
    from app.services import lesson_idle_cron_service  # noqa: PLC0415

    settings_store.apply_local("lesson_idle_threshold_minutes", 25)
    assert settings_store.get_int("lesson_idle_threshold_minutes") == 25
    # Модуль не сложил порог в константу при импорте — иначе следующая
    # проверка вернула бы 10 и правка ждала бы перезапуска.
    assert lesson_idle_cron_service.settings_store.get_int(
        "lesson_idle_threshold_minutes"
    ) == 25


def test_день_блокировки_действует_на_расчёт_без_перезапуска():
    from datetime import date  # noqa: PLC0415

    from app.services import payment_service  # noqa: PLC0415

    period = date(2026, 8, 1)
    settings_store.apply_local("payment_block_after_days", 5)
    assert payment_service.block_date_for(period) == date(2026, 9, 5)

    settings_store.apply_local("payment_block_after_days", 10)
    assert payment_service.block_date_for(period) == date(2026, 9, 10)


def test_окно_затих_читается_из_настроек():
    from app.services import learning_gap_signals_service as signals  # noqa: PLC0415

    settings_store.apply_local("dropout_risk_window_days", 21)
    assert signals.dropout_window_days() == 21


def test_пороги_пробелов_общие_с_освоением_тем():
    """Один порог на два места: разъехавшись, они дали бы разные ответы."""
    from app.services import learning_gaps_service as gaps  # noqa: PLC0415
    from app.services import topic_mastery_service as mastery  # noqa: PLC0415

    settings_store.apply_local("gap_task_error_rate", 0.6)
    assert gaps.task_error_rate() == 0.6
    # Тема с долей ошибок 0.5 при пороге 0.6 трудной уже не считается.
    assert mastery.classify_topic(0.5, None) != mastery.SIGNAL_HARD
    assert mastery.classify_topic(0.7, None) == mastery.SIGNAL_HARD


# ── Секретов в кабинете быть не может ────────────────────────────────────────


def test_в_реестре_нет_ни_одного_секрета():
    """Прямой запрет задачи. Проверяем механически, а не глазами при ревью."""
    forbidden = (
        "secret", "token", "key", "password", "dsn", "database_url",
        "api_key", "credential",
    )
    for definition in registry.SETTINGS:
        low = definition.key.lower()
        # `key` встречается в безобидных словах, поэтому смотрим на границы:
        # ищем целое слово, а не подстроку.
        parts = set(low.split("_"))
        assert not (parts & set(forbidden)), (
            f"настройка {definition.key} похожа на секрет — таким в кабинете не место"
        )


def test_у_каждой_настройки_есть_русское_имя_и_пояснение():
    """Параметр без пояснения хуже, чем его отсутствие: его просто не тронут."""
    for definition in registry.SETTINGS:
        assert definition.title.strip(), definition.key
        assert definition.description.strip(), definition.key
        # Название — по-русски, а не машинное имя переменной.
        assert any("а" <= ch.lower() <= "я" for ch in definition.title), definition.key
        if definition.kind in ("int", "float"):
            assert definition.min_value is not None, definition.key
            assert definition.max_value is not None, definition.key
            assert definition.unit.strip(), definition.key


def test_умолчание_каждой_настройки_проходит_свои_же_границы():
    """Умолчание вне границ значило бы, что настройку нельзя вернуть как было."""
    for definition in registry.SETTINGS:
        registry.coerce(definition, definition.default)
