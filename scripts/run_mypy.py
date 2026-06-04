"""Run the reproducible LMS mypy contour with the active Python environment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "app/services/sheets_parser_service.py",
    "app/services/tasks_service.py",
    "app/api/v1/tasks_extra.py",
    "app/schemas/task_content.py",
    "app/schemas/tasks.py",
]


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(PROJECT_ROOT / "mypy.ini"),
        *TARGETS,
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
