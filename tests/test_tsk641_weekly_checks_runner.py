# -*- coding: utf-8 -*-
"""tsk-641: общий вход планировщика для еженедельных чеков.

Главное, что здесь защищается, — отказ подключения. Чек, молча ушедший на dev-базу
вместо прода, отчитается «чисто» по пустой локальной базе, и это худший вид поломки:
он выглядит как успех. Поэтому `prod_dsn` обязан падать, а не подставлять запасной
вариант.

Второе — тишина журнала. Еженедельная запись «всё хорошо» полезна только пока она
одна строка; если подшивать туда же служебную шапку чека («База: …»), настоящая
находка утонет. Прежние обёртки на PowerShell вывод при коде 0 отбрасывали, и это
поведение сохранено.

БД не трогают.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import weekly_checks  # noqa: E402


class TestProdDsn:
    """Подключение берётся из .mcp.json и только оттуда."""

    def _config(self, tmp_path: Path, payload: dict) -> Path:
        path = tmp_path / ".mcp.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_схема_приводится_к_asyncpg(self, tmp_path, monkeypatch):
        """Чек порядка разделов поднимает сессию приложения и другую форму не примет."""
        cfg = self._config(
            tmp_path,
            {"mcpServers": {"learn_prod_db": {"args": ["postgresql://u:p@host:5432/learn"]}}},
        )
        monkeypatch.setattr(weekly_checks, "MCP_CONFIG", cfg)

        assert weekly_checks.prod_dsn() == "postgresql+asyncpg://u:p@host:5432/learn"

    def test_уже_приведённую_схему_не_ломает(self, tmp_path, monkeypatch):
        cfg = self._config(
            tmp_path,
            {"mcpServers": {"learn_prod_db": {"args": ["postgresql+asyncpg://u:p@host/learn"]}}},
        )
        monkeypatch.setattr(weekly_checks, "MCP_CONFIG", cfg)

        assert weekly_checks.prod_dsn() == "postgresql+asyncpg://u:p@host/learn"

    def test_нет_файла_это_ошибка_а_не_запасной_вариант(self, tmp_path, monkeypatch):
        monkeypatch.setattr(weekly_checks, "MCP_CONFIG", tmp_path / "нет-такого.json")

        with pytest.raises(RuntimeError, match="не найден"):
            weekly_checks.prod_dsn()

    def test_нет_боевого_подключения_это_ошибка(self, tmp_path, monkeypatch):
        """Молчаливый уход на dev дал бы отчёт «чисто» по пустой локальной базе."""
        cfg = self._config(tmp_path, {"mcpServers": {"learn_public_db": {"args": ["postgresql://x"]}}})
        monkeypatch.setattr(weekly_checks, "MCP_CONFIG", cfg)

        with pytest.raises(RuntimeError, match="боевого подключения"):
            weekly_checks.prod_dsn()

    def test_пароль_в_описание_базы_не_попадает(self):
        """Строка про базу уходит в журнал — секрету там не место."""
        описание = weekly_checks._describe("postgresql+asyncpg://user:s3cret@host:5432/learn")

        assert описание == "host:5432/learn"
        assert "s3cret" not in описание


class TestRegistry:
    """Реестр чеков описывает то, что действительно есть."""

    def test_все_модули_существуют_и_имеют_main(self):
        """Опечатка в имени модуля молча выключила бы чек до первого понедельника."""
        for name, check in weekly_checks.CHECKS.items():
            module = importlib.import_module(check.module)
            assert callable(getattr(module, "main", None)), f"{name}: у {check.module} нет main()"

    def test_журналы_у_чеков_разные(self):
        logs = [check.log for check in weekly_checks.CHECKS.values()]
        assert len(logs) == len(set(logs))


class TestLogBranches:
    """Что попадает в журнал при каждом коде выхода."""

    @pytest.fixture(autouse=True)
    def _no_db(self, monkeypatch, tmp_path):
        """Подменяем и запуск чека, и каталог журналов — база не нужна."""
        monkeypatch.setattr(weekly_checks, "LOG_DIR", tmp_path)
        monkeypatch.setattr(weekly_checks, "prod_dsn", lambda: "postgresql+asyncpg://u:p@h:5432/learn")
        monkeypatch.chdir(tmp_path)

    def _run(self, monkeypatch, tmp_path, check_name: str, code: int, output: str) -> str:
        monkeypatch.setattr(weekly_checks, "run_check", lambda check: (code, output))
        weekly_checks.main([check_name])
        log = weekly_checks.CHECKS[check_name].log
        return (tmp_path / log).read_text(encoding="utf-8")

    def test_чисто_это_одна_строка(self, monkeypatch, tmp_path):
        текст = self._run(monkeypatch, tmp_path, "section-order", 0, "")

        assert "OK: порядок разделов верный" in текст
        assert текст.count("\n") == 1

    def test_шапка_чека_в_журнал_не_подшивается(self, monkeypatch, tmp_path):
        """report_on_zero=False: код 0 → только строка «OK», вывод отбрасывается.

        Иначе каждую неделю в журнал падало бы «База: …», и находку в нём было бы
        не разглядеть — ровно так вели себя прежние обёртки на PowerShell.
        """
        текст = self._run(monkeypatch, tmp_path, "section-order", 0, "База: 1.2.3.4 / learn")

        assert "OK: порядок разделов верный" in текст
        assert "База:" not in текст

    def test_смежный_сигнал_у_сверки_незачётов_сохраняется(self, monkeypatch, tmp_path):
        """report_on_zero=True: её тихий режим по-настоящему молчит, значит вывод значим."""
        текст = self._run(monkeypatch, tmp_path, "stale-verdicts", 0, "тип задания сменили")

        assert "есть что посмотреть" in текст
        assert "тип задания сменили" in текст

    def test_находка_попадает_целиком_и_с_подсказкой(self, monkeypatch, tmp_path):
        текст = self._run(monkeypatch, tmp_path, "ungradable", 1, "задание 42 без правила")

        assert "НАЙДЕНЫ непроверяемые задания" in текст
        assert "задание 42 без правила" in текст
        assert "tsk-361" in текст

    def test_ошибка_чека_видна_в_журнале(self, monkeypatch, tmp_path):
        текст = self._run(monkeypatch, tmp_path, "ungradable", 2, "не задан DATABASE_URL")

        assert "ОШИБКА чека (код 2)" in текст
        assert "не задан DATABASE_URL" in текст

    def test_падение_чека_не_роняет_запуск_молча(self, monkeypatch, tmp_path):
        """Под pythonw traceback показать некому — он обязан оказаться в журнале."""
        def взорвать(check):
            raise RuntimeError("соединение отвалилось")

        monkeypatch.setattr(weekly_checks, "run_check", взорвать)
        код = weekly_checks.main(["ungradable"])
        текст = (tmp_path / "ungradable_tasks_check.log").read_text(encoding="utf-8")

        assert код == 2
        assert "ОШИБКА чека" in текст
        assert "соединение отвалилось" in текст

    def test_куда_ходил_чек_записано(self, monkeypatch, tmp_path):
        """Подмена базы — самый неприятный отказ, по журналу она должна быть видна."""
        текст = self._run(monkeypatch, tmp_path, "ungradable", 0, "")

        assert "h:5432/learn" in текст

    def test_сводка_получает_строку_на_каждый_прогон(self, monkeypatch, tmp_path):
        """tsk-777: одно место, где видно всю неделю разом.

        Журнал чека отвечает «что нашли», сводка — «отработали ли чеки и у кого есть
        что смотреть». Без неё картину приходилось собирать, открывая пять журналов и
        сверяя в них даты.
        """
        self._run(monkeypatch, tmp_path, "ungradable", 1, "задание 42 без правила")
        сводка = (tmp_path / weekly_checks.SUMMARY_LOG).read_text(encoding="utf-8")

        assert "ungradable" in сводка
        assert "ЕСТЬ НАХОДКИ" in сводка
        assert "logs/ungradable_tasks_check.log" in сводка

    def test_флаг_без_консоли_выставлен_до_запуска_чека(self, monkeypatch, tmp_path):
        """Из-за этой строки чек не зовёт os.system("chcp") — и окно не моргает.

        Два чека делают этот вызов при импорте. Под `pythonw` консоли нет, cmd.exe
        получил бы свою — то есть вернулось бы ровно то мигание, ради которого чеки
        и переводили на `pythonw`. Строка выглядит необязательной, поэтому закреплена
        тестом.
        """
        monkeypatch.delenv("LMS_CHECK_NO_CONSOLE", raising=False)
        увиденное: dict[str, str | None] = {}

        def запомнить(check):
            увиденное["флаг"] = __import__("os").environ.get("LMS_CHECK_NO_CONSOLE")
            return 0, ""

        monkeypatch.setattr(weekly_checks, "run_check", запомнить)
        weekly_checks.main(["ungradable"])

        assert увиденное["флаг"] == "1"


class TestExitCodes:
    """tsk-777: что именно планировщик считает ошибкой.

    Планировщик Windows семантики кода не знает: любой ненулевой результат он
    показывает как `LastTaskResult` со значком ошибки. Пока находки возвращались
    единицей, четыре задачи из пяти месяцами стояли «с ошибкой», отрабатывая штатно, —
    и на этом фоне настоящий сбой было не отличить. Ненулевой код теперь означает ровно
    одно: чек не дошёл до конца.
    """

    @pytest.fixture(autouse=True)
    def _no_db(self, monkeypatch, tmp_path):
        monkeypatch.setattr(weekly_checks, "LOG_DIR", tmp_path)
        monkeypatch.setattr(weekly_checks, "prod_dsn", lambda: "postgresql+asyncpg://u:p@h:5432/learn")
        monkeypatch.chdir(tmp_path)

    def test_находки_это_не_ошибка_задачи(self, monkeypatch):
        monkeypatch.setattr(weekly_checks, "run_check", lambda check: (1, "задание 42 без правила"))

        assert weekly_checks.main(["ungradable"]) == 0

    def test_чисто_тоже_ноль(self, monkeypatch):
        monkeypatch.setattr(weekly_checks, "run_check", lambda check: (0, ""))

        assert weekly_checks.main(["section-order"]) == 0

    def test_флагом_находки_снова_дают_единицу(self, monkeypatch):
        """Машинный признак никуда не делся — он просто больше не идёт планировщику."""
        monkeypatch.setattr(weekly_checks, "run_check", lambda check: (1, "задание 42 без правила"))

        assert weekly_checks.main(["ungradable", "--fail-on-findings"]) == 1

    def test_флаг_не_превращает_чистый_прогон_в_ошибку(self, monkeypatch):
        monkeypatch.setattr(weekly_checks, "run_check", lambda check: (0, ""))

        assert weekly_checks.main(["section-order", "--fail-on-findings"]) == 0

    def test_сбой_чека_остаётся_ненулевым(self, monkeypatch):
        """Ради этого исхода задачи и заведены: он обязан быть виден в планировщике."""
        monkeypatch.setattr(weekly_checks, "run_check", lambda check: (2, "не задан DATABASE_URL"))

        assert weekly_checks.main(["ungradable"]) == 2

    def test_сбой_виден_и_в_сводке(self, monkeypatch, tmp_path):
        def взорвать(check):
            raise RuntimeError("соединение отвалилось")

        monkeypatch.setattr(weekly_checks, "run_check", взорвать)
        код = weekly_checks.main(["ungradable"])
        сводка = (tmp_path / weekly_checks.SUMMARY_LOG).read_text(encoding="utf-8")

        assert код == 2
        assert "СБОЙ" in сводка


class TestDigest:
    """tsk-778: недельная сводка оператору в Telegram.

    Смысл — не в самой отправке, а в её избирательности. Журналы месяцами лежали
    непрочитанными; сводка, приходящая каждую неделю «всё хорошо», стала бы такими же
    журналами за месяц. Поэтому молчание при чистой неделе закреплено тестом наравне
    с доставкой при находках.
    """

    ПОЛНЫЙ_ДЕНЬ = [
        "2026-09-07 08:40  cb-drift           чисто [h:5432/learn]",
        "2026-09-07 09:00  section-order      чисто [h:5432/learn]",
        "2026-09-07 09:10  ungradable         чисто [h:5432/learn]",
        "2026-09-07 09:20  stale-verdicts     чисто",
        "2026-09-07 09:25  slow-requests      чисто [h:5432/learn]",
        "2026-09-07 09:28  tutor-outcomes     чисто [h:5432/learn]",
    ]

    def test_чистая_неделя_сводки_не_рождает(self):
        assert weekly_checks.build_digest("2026-09-07", self.ПОЛНЫЙ_ДЕНЬ) is None

    def test_находка_доходит_до_сводки(self):
        строки = self.ПОЛНЫЙ_ДЕНЬ[:-1] + [
            "2026-09-07 09:28  tutor-outcomes     ЕСТЬ НАХОДКИ — logs/tutor_outcomes_check.log",
        ]
        текст = weekly_checks.build_digest("2026-09-07", строки)

        assert текст is not None
        assert "Есть находки" in текст
        assert "tutor-outcomes" in текст
        assert "section-order" not in текст  # чистые чеки оператора не занимают

    def test_молчащий_чек_это_тревога(self):
        """Отказ, выглядящий как тишина: задача не отработала, строки просто нет."""
        текст = weekly_checks.build_digest("2026-09-07", self.ПОЛНЫЙ_ДЕНЬ[:-1])

        assert текст is not None
        assert "МОЛЧАТ" in текст
        assert "tutor-outcomes" in текст

    def test_день_без_единой_записи_это_тревога(self):
        """Машина спала весь понедельник — узнать об этом надо от сводки, а не случайно."""
        текст = weekly_checks.build_digest("2026-09-07", [])

        assert текст is not None
        assert "МОЛЧАТ" in текст

    def test_сбой_чека_отделён_от_находок(self):
        строки = self.ПОЛНЫЙ_ДЕНЬ[:-1] + [
            "2026-09-07 09:28  tutor-outcomes     СБОЙ — подробности в logs/tutor_outcomes_check.log",
        ]
        текст = weekly_checks.build_digest("2026-09-07", строки)

        assert "НЕ ОТРАБОТАЛИ:" in текст
        assert "Есть находки" not in текст

    def test_ручная_пометка_в_журнале_не_становится_тревогой(self):
        """Человек дописывает в журнал свободные строки — и в них попадаются те же слова.

        Принять такую строку за состояние чека — значит слать оператору тревогу о том,
        что он сам же и написал.
        """
        строки = self.ПОЛНЫЙ_ДЕНЬ + [
            "2026-09-07 09:40  ^^^ СБОЙ выше — учебный прогон, настоящего сбоя не было",
        ]

        assert weekly_checks.build_digest("2026-09-07", строки) is None

    def test_повторный_прогон_перекрывает_прежнюю_запись(self):
        """Чек перезапустили руками и он стал чистым — в сводке важно последнее состояние."""
        строки = self.ПОЛНЫЙ_ДЕНЬ + [
            "2026-09-07 10:00  tutor-outcomes     ЕСТЬ НАХОДКИ — logs/tutor_outcomes_check.log",
            "2026-09-07 10:30  tutor-outcomes     чисто [h:5432/learn]",
        ]

        assert weekly_checks.build_digest("2026-09-07", строки) is None

    def test_недоставленная_сводка_это_ненулевой_код(self, monkeypatch, tmp_path):
        """Молча потерянная сводка вернула бы ровно ту слепоту, ради которой она заведена."""
        monkeypatch.setattr(weekly_checks, "LOG_DIR", tmp_path)
        monkeypatch.setattr(
            weekly_checks, "summary_lines_for", lambda day: self.ПОЛНЫЙ_ДЕНЬ[:-1]
        )

        def не_дошло(text):
            raise RuntimeError("Telegram ответил 502")

        monkeypatch.setattr(weekly_checks, "send_telegram", не_дошло)
        код = weekly_checks.run_digest("2026-09-07")
        сводка = (tmp_path / weekly_checks.SUMMARY_LOG).read_text(encoding="utf-8")

        assert код == 2
        assert "НЕ ОТПРАВЛЕНА" in сводка

    def test_токен_ищется_в_переменных_окружения_раньше_чужой_папки(self, monkeypatch):
        monkeypatch.setenv("WEEKLY_CHECKS_TG_TOKEN", "123:abc")
        monkeypatch.setenv("WEEKLY_CHECKS_TG_CHAT_ID", "777")

        assert weekly_checks.tg_credentials() == ("123:abc", "777")

    def test_без_токена_это_ошибка_а_не_тихий_пропуск(self, monkeypatch, tmp_path):
        """Пропажа чужого файла не должна выглядеть как «сводки не было о чём слать»."""
        monkeypatch.delenv("WEEKLY_CHECKS_TG_TOKEN", raising=False)
        monkeypatch.setattr(weekly_checks, "TG_ENV", tmp_path / "нет-такого.env")

        with pytest.raises(RuntimeError, match="нет токена бота"):
            weekly_checks.tg_credentials()
