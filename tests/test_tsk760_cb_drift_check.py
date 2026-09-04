# -*- coding: utf-8 -*-
"""tsk-760: регулярный чек «правка есть, пометки нет».

Что здесь защищается.

1. Помеченные задания в находки не идут. Расхождение с источником никуда не
   девается после простановки пометки, и если показывать его каждую неделю, сводка
   через месяц станет списком из трёхсот старых строк, в котором новую правку никто
   не разглядит.
2. Сверка, которая не прошла, — это не «чисто». Отсутствие CB, зависший прогон,
   отчёт, которого нет: каждый случай обязан быть ненулевым кодом, иначе чек
   отчитается благополучно, не посмотрев ни на одно задание.
3. Задания, которые LMS не отдала (`unreadable`), тоже тревога: про них сверка
   ничего не утверждает.

БД не трогают: обращения к базе подменены.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import check_cb_drift  # noqa: E402
import weekly_checks  # noqa: E402

DSN = "postgresql+asyncpg://user:pass@example/learn"


def report(edited: list[str], *, unreadable: list[str] | None = None, same: int = 0) -> dict:
    rows = [{"external_uid": uid, "status": "edited_in_lms"} for uid in edited]
    rows += [{"external_uid": uid, "status": "unreadable"} for uid in unreadable or []]
    return {
        "total": len(rows) + same,
        "counts": {"same": same, "edited_in_lms": len(edited)},
        "edited_in_lms": edited,
        "rows": rows,
    }


def write_report(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "drift.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture
def db(monkeypatch):
    """Подменить оба обращения к базе: помеченные ключи и строки заданий."""
    state = {"marked": set(), "rows": {}}

    async def fake_marked(dsn, uids):
        return {uid for uid in uids if uid in state["marked"]}

    async def fake_rows(dsn, uids):
        return {uid: state["rows"].get(uid, {"id": 1, "is_active": True}) for uid in uids}

    monkeypatch.setattr(check_cb_drift, "already_marked", fake_marked)
    monkeypatch.setattr(check_cb_drift, "task_rows", fake_rows)
    monkeypatch.setenv("DATABASE_URL", DSN)
    return state


def run(report_path: str) -> int:
    return asyncio.run(check_cb_drift.main(quiet=True, report_path=report_path))


def test_помеченные_задания_не_находка(tmp_path, db, capsys):
    db["marked"] = {"wp:task:1", "tg:ege:2"}
    code = run(write_report(tmp_path, report(["wp:task:1", "tg:ege:2"], same=100)))
    assert code == 0
    assert "НАЙДЕНЫ" not in capsys.readouterr().out


def test_непомеченная_правка_это_находка(tmp_path, db, capsys):
    db["marked"] = {"wp:task:1"}
    code = run(write_report(tmp_path, report(["wp:task:1", "tg:ege:2"])))
    out = capsys.readouterr().out
    assert code == 1
    assert "tg:ege:2" in out
    assert "wp:task:1" not in out.split("НАЙДЕНЫ")[1]


def test_активность_видна_в_находке(tmp_path, db, capsys):
    db["rows"] = {"tg:ege:2": {"id": 42, "is_active": False}}
    run(write_report(tmp_path, report(["tg:ege:2"])))
    out = capsys.readouterr().out
    assert "задание 42 (скрытое)" in out


def test_нечитаемые_задания_это_тревога(tmp_path, db, capsys):
    """«LMS не отдала задание» — не то же самое, что «расхождений нет»."""
    code = run(write_report(tmp_path, report([], unreadable=["wp:task:9"])))
    out = capsys.readouterr().out
    assert code == 1
    assert "НЕ УДАЛОСЬ ПРОЧИТАТЬ" in out
    assert "wp:task:9" in out


def test_без_dsn_чек_не_молчит(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(check_cb_drift, "load_dotenv", lambda **kw: None, raising=False)
    code = asyncio.run(check_cb_drift.main(quiet=True, report_path=write_report(tmp_path, report([]))))
    assert code == 2


class TestЗапускСверки:
    """Отказ прогона обязан быть отказом, а не тихим нулём."""

    def test_нет_окружения_cb(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check_cb_drift, "CB_ROOT", tmp_path / "нет-такого")
        with pytest.raises(RuntimeError, match="окружение ContentBackbone"):
            check_cb_drift.run_drift_audit(None)

    def test_отчёта_нет_после_прогона(self, tmp_path, monkeypatch):
        venv = tmp_path / ".venv" / "Scripts"
        venv.mkdir(parents=True)
        (venv / "python.exe").write_text("", encoding="utf-8")
        monkeypatch.setattr(check_cb_drift, "CB_ROOT", tmp_path)

        class Done:
            returncode = 3
            stdout = ""
            stderr = "нет подключения к кабинету"

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Done())
        with pytest.raises(RuntimeError, match="не отдала отчёт"):
            check_cb_drift.run_drift_audit(None)

    def test_зависание_это_ошибка(self, tmp_path, monkeypatch):
        venv = tmp_path / ".venv" / "Scripts"
        venv.mkdir(parents=True)
        (venv / "python.exe").write_text("", encoding="utf-8")
        monkeypatch.setattr(check_cb_drift, "CB_ROOT", tmp_path)

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="drift", timeout=1)

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(RuntimeError, match="не уложилась"):
            check_cb_drift.run_drift_audit(None)

    def test_чужой_dsn_не_уезжает_в_cb(self, tmp_path, monkeypatch):
        """У CB своя база; DATABASE_URL этого чека — про LMS и в чужой процесс не идёт."""
        venv = tmp_path / ".venv" / "Scripts"
        venv.mkdir(parents=True)
        (venv / "python.exe").write_text("", encoding="utf-8")
        monkeypatch.setattr(check_cb_drift, "CB_ROOT", tmp_path)
        monkeypatch.setenv("DATABASE_URL", DSN)
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw.get("env") or {})
            Path(cmd[cmd.index("--out") + 1]).write_text("{}", encoding="utf-8")

            class Done:
                returncode = 0
                stdout = ""
                stderr = ""

            return Done()

        monkeypatch.setattr(subprocess, "run", fake_run)
        check_cb_drift.run_drift_audit(None)
        assert "DATABASE_URL" not in seen


class TestРеестрЧеков:
    """Чек виден сводке — иначе его находки снова некому читать (tsk-778)."""

    def test_чек_зарегистрирован(self):
        assert "cb-drift" in weekly_checks.CHECKS
        assert weekly_checks.CHECKS["cb-drift"].module == "check_cb_drift"

    def test_сводка_ждёт_чек(self):
        assert "cb-drift" in weekly_checks.DIGEST_EXPECTED

    def test_молчание_чека_попадает_в_сводку(self):
        text = weekly_checks.build_digest("2026-09-07", [])
        assert text is not None and "cb-drift" in text
