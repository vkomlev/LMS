"""
Тест upgrade/downgrade roundtrip для миграций M1–M5 Phase Y-1.

Запускается против реальной БД (alembic использует DATABASE_URL из .env).
Требует: alembic head = 20260428_050000_m5_guest до запуска.
"""
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[1]


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "alembic"] + list(args)
    result = subprocess.run(
        cmd, cwd=str(project_root), capture_output=True, text=True, encoding="utf-8"
    )
    return result


def test_alembic_head_is_m5():
    """Текущий head должен быть M5 (все 5 миграций применены)."""
    result = _run_alembic("current")
    assert result.returncode == 0, f"alembic current failed:\n{result.stderr}"
    assert "20260428_050000_m5_guest" in result.stdout or "m5_guest" in result.stdout, (
        f"Expected m5_guest as head, got:\n{result.stdout}"
    )


def test_alembic_downgrade_m5_then_upgrade():
    """Откатить M5 и снова накатить — должно пройти без ошибок."""
    down = _run_alembic("downgrade", "20260428_040000_m4_audit")
    assert down.returncode == 0, f"downgrade M5 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M5 downgrade failed:\n{up.stderr}"

    current = _run_alembic("current")
    assert "20260428_050000_m5_guest" in current.stdout or "m5_guest" in current.stdout


def test_alembic_downgrade_m4_then_upgrade():
    """Откатить до M3, затем накатить обратно."""
    down = _run_alembic("downgrade", "20260428_030000_m3_sessions")
    assert down.returncode == 0, f"downgrade M4 failed:\n{down.stderr}"

    up = _run_alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade head after M4 downgrade failed:\n{up.stderr}"
