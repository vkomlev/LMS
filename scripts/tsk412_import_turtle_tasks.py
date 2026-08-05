# -*- coding: utf-8 -*-
"""tsk-412: перенос 18 заданий курса 165 «Черепашья графика» из текста
материалов 314/316/317 в `tasks` (course_id=165).

ЗАЧЕМ
Материалы 314/316/317 хранят задания текстом внутри содержимого (нумерованный
список + `[spoiler]` с решением) — `tasks` для course_id=165 пуст (0 строк,
перепроверено 2026-08-05). Полный контекст и решение оператора — в
D:\\Work\\Root\\tasks\\tsk-412-*.md.

ОБЪЁМ (решение оператора 2026-08-05, AskUserQuestion)
Материал 314 «Задания на закрепление темы» (10 узоров) сам НЕ помечен как
необязательный — это основной проверяемый материал. Автопроверка через
песочницу (`app.services.turtle_sandbox`): тип SA, эталон — трасса исполнения
эталонного решения (не сам код). requirement_level=required.

Материалы 316/317 (8 заданий: события + анимация) САМИ помечают себя как
чтение «для общего кругозора» вне проверяемой программы «Задание 6», и часть
эталонов там структурно нерабочая (незасеянная случайность, отсутствующая
логика столкновений, `while True` без screen.update()). Оператор решил НЕ
строить для них автопроверку состояния: SA_COM, requirement_level=recommended,
manual_review_required=True (учитель может посмотреть код в комментарии),
без эталона.

ПОРЯДОК ЗАДАНИЙ / ТРИГГЕР
Вставка с явным order_position 1..18 в course_id=165, который сейчас содержит
0 заданий — trg_set_task_order_position отрабатывает штатно (сдвигать нечего).

ИДЕМПОТЕНТНОСТЬ
Скрипт не запускается повторно при tasks_cnt > 0 для course_id=165 (см. main).

Запуск: dry-run по умолчанию (ничего не пишет, всегда ROLLBACK);
  PYTHONPATH=. python scripts/tsk412_import_turtle_tasks.py
  PYTHONPATH=. DBCHECK_OK=1 python scripts/tsk412_import_turtle_tasks.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.solution_rules import SolutionRules, TurtleSimRules, TurtleTrace  # noqa: E402
from app.services.turtle_sandbox.executor import run_student_code  # noqa: E402

COURSE_ID = 165

# ---------- Материал 314 — 10 узоров, автопроверка через песочницу ----------

_TURTLE_SIM_TASKS: List[Dict[str, Any]] = [
    {
        "stem": "Нарисуйте квадрат со стороной 50 пикселей: цикл из 4 повторений "
                "«шаг вперёд на 50 — поворот на 90°».",
        "difficulty_id": 1,
        "reference_code": (
            "import turtle\nt = turtle.Turtle()\n"
            "for _ in range(4):\n    t.forward(50)\n    t.right(90)\nturtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте узор из 12 отрезков длиной 100 пикселей, поворачивая "
                "на 150° после каждого отрезка.",
        "difficulty_id": 1,
        "reference_code": (
            "import turtle\nt = turtle.Turtle()\n"
            "for _ in range(12):\n    t.forward(100)\n    t.right(150)\nturtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте узор из 6 повторений: круг радиусом 50, отрезок длиной "
                "50, поворот на 60°.",
        "difficulty_id": 2,
        "reference_code": (
            "import turtle\nt = turtle.Turtle()\n"
            "for _ in range(6):\n    t.circle(50)\n    t.forward(50)\n    t.right(60)\n"
            "turtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте 6 кругов радиусом 50, каждый своим цветом радуги: "
                "используйте colorsys.hsv_to_rgb(hue, 1, 1), где hue равномерно "
                "меняется от 0 до 1 (i / 6.0).",
        "difficulty_id": 2,
        "reference_code": (
            "import turtle\nimport colorsys\nt = turtle.Turtle()\n"
            "for i in range(6):\n    hue = i / 6.0\n"
            "    t.color(colorsys.hsv_to_rgb(hue, 1, 1))\n    t.circle(50)\nturtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте цветную спираль из 100 отрезков: длина i-го отрезка "
                "равна i, после каждого — поворот на 45° и цвет "
                "colorsys.hsv_to_rgb(i / 100.0, 1, 1).",
        "difficulty_id": 2,
        "reference_code": (
            "import turtle\nimport colorsys\nt = turtle.Turtle()\n"
            "for i in range(100):\n    hue = i / 100.0\n"
            "    t.color(colorsys.hsv_to_rgb(hue, 1, 1))\n"
            "    t.forward(i)\n    t.right(45)\nturtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте радужное «колесо» из 360 отрезков длиной 100: поворот "
                "на 30° после каждого, цвет colorsys.hsv_to_rgb(i / 360.0, 1, 1).",
        "difficulty_id": 2,
        "reference_code": (
            "import turtle\nimport colorsys\nt = turtle.Turtle()\n"
            "for i in range(360):\n    hue = i / 360.0\n"
            "    t.color(colorsys.hsv_to_rgb(hue, 1, 1))\n"
            "    t.forward(100)\n    t.right(30)\nturtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте узор из 4 треугольников со стороной 50, каждый своим "
                "цветом радуги (colorsys.hsv_to_rgb(i / 4.0, 1, 1)); после "
                "каждого треугольника сместитесь вперёд ещё на 50.",
        "difficulty_id": 3,
        "reference_code": (
            "import turtle\nimport colorsys\nt = turtle.Turtle()\n"
            "for i in range(4):\n    hue = i / 4.0\n"
            "    t.color(colorsys.hsv_to_rgb(hue, 1, 1))\n"
            "    for _ in range(3):\n        t.forward(50)\n        t.left(120)\n"
            "    t.forward(50)\nturtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Используя рекурсию, нарисуйте фрактальное дерево: рекурсивная "
                "функция при длине ветви больше 5 пикселей должна пройти вперёд, "
                "повернуть налево на 30°, вызвать себя с длиной × 0.7, повернуть "
                "направо на 60°, снова вызвать себя с длиной × 0.7, повернуть "
                "налево на 30° и вернуться назад тем же путём.",
        "difficulty_id": 4,
        "reference_code": (
            "import turtle\n"
            "def draw_fractal(t, length):\n"
            "    if length > 5:\n"
            "        t.forward(length)\n"
            "        t.left(30)\n"
            "        draw_fractal(t, length * 0.7)\n"
            "        t.right(60)\n"
            "        draw_fractal(t, length * 0.7)\n"
            "        t.left(30)\n"
            "        t.backward(length)\n"
            "t = turtle.Turtle()\n"
            "draw_fractal(t, 100)\n"
            "turtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [],
    },
    {
        "stem": "Нарисуйте 36 отрезков случайной длины (от 50 до 150 пикселей), "
                "каждый своим случайным цветом радуги, поворачивая на 170° после "
                "каждого. Используйте random.random() для цвета "
                "(colorsys.hsv_to_rgb(random.random(), 1, 1)) и "
                "random.randint(50, 150) для длины — сид для воспроизводимости "
                "устанавливает сама проверяющая система, вызывать random.seed() "
                "в программе не нужно.",
        "difficulty_id": 3,
        "reference_code": (
            "import turtle\nimport random\nimport colorsys\nt = turtle.Turtle()\n"
            "for _ in range(36):\n    hue = random.random()\n"
            "    t.color(colorsys.hsv_to_rgb(hue, 1, 1))\n"
            "    size = random.randint(50, 150)\n"
            "    t.forward(size)\n    t.right(170)\nturtle.done()\n"
        ),
        "random_seed": 20260805,
        "synthetic_clicks": [],
    },
    {
        "stem": "Обработайте клик мышью: по клику черепаха должна поднять перо, "
                "переместиться в точку клика, опустить перо и нарисовать квадрат "
                "со стороной 50 пикселей (цикл из 4 повторений «вперёд на 50 — "
                "поворот на 90°»).",
        "difficulty_id": 3,
        "reference_code": (
            "import turtle\n"
            "def draw_square(t, size):\n"
            "    for _ in range(4):\n        t.forward(size)\n        t.left(90)\n"
            "def on_click(x, y):\n"
            "    t.penup()\n    t.goto(x, y)\n    t.pendown()\n"
            "    draw_square(t, 50)\n"
            "t = turtle.Turtle()\n"
            "turtle.onscreenclick(on_click)\n"
            "turtle.done()\n"
        ),
        "random_seed": None,
        "synthetic_clicks": [[37.0, -52.0]],
    },
]

# ---------- Материалы 316/317 — 8 заданий событий/анимации, без автопроверки ----------

_MANUAL_SA_COM_TASKS: List[Dict[str, Any]] = [
    {
        "stem": "«Лови мишень»: напишите программу, где по клику мыши рядом с "
                "мишенью (кругом) начисляется очко, после чего мишень "
                "перемещается в новое случайное место.",
        "difficulty_id": 3,
    },
    {
        "stem": "«Избегай препятствий»: разместите на экране несколько "
                "препятствий (квадратов) в случайных точках и реализуйте "
                "проверку столкновения черепахи с ближайшим препятствием.",
        "difficulty_id": 3,
    },
    {
        "stem": "«Лабиринт»: нарисуйте простой лабиринт из стен и реализуйте "
                "управление черепахой стрелками клавиатуры (Up/Down/Left/Right).",
        "difficulty_id": 3,
    },
    {
        "stem": "«Плавающий остров»: смоделируйте остров, который со временем "
                "поднимается или опускается относительно уровня воды, с условием "
                "проигрыша при опускании ниже уровня воды.",
        "difficulty_id": 3,
    },
    {
        "stem": "«Пинг-понг»: реализуйте игру, где две черепахи-ракетки "
                "управляются клавишами, а третья черепаха-мяч движется и "
                "отскакивает от границ поля и ракеток.",
        "difficulty_id": 3,
    },
    {
        "stem": "«Бегущая черепаха»: создайте анимацию, в которой черепаха "
                "непрерывно движется по экрану, оставляя след, периодически "
                "меняя его цвет.",
        "difficulty_id": 3,
    },
    {
        "stem": "«Маятник»: смоделируйте раскачивание маятника (поворот "
                "черепахи-стержня вперёд-назад) с изменением цвета в "
                "зависимости от текущего положения.",
        "difficulty_id": 4,
    },
    {
        "stem": "«Калейдоскоп»: используя несколько черепах, двигающихся "
                "синхронно в разные стороны, создайте калейдоскопический узор.",
        "difficulty_id": 4,
    },
]


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


def _task_content(stem: str, task_type: str) -> dict:
    return {
        "code": None,
        "stem": stem,
        "tags": None,
        "type": task_type,
        "media": None,
        "title": None,
        "prompt": None,
        "options": None,
        "course_uid": None,
        "difficulty_code": None,
    }


def _build_turtle_sim_solution_rules(spec: Dict[str, Any]) -> SolutionRules:
    reference = run_student_code(
        spec["reference_code"],
        random_seed=spec["random_seed"],
        synthetic_clicks=spec["synthetic_clicks"],
        max_steps=5000,
        timeout_sec=5.0,
    )
    if not reference.ok:
        raise RuntimeError(
            f"Эталонное решение не исполнилось в песочнице: {reference.error} {reference.message}\n"
            f"{spec['reference_code']}"
        )
    return SolutionRules(
        max_score=1,
        turtle_sim=TurtleSimRules(
            expected_trace=TurtleTrace.model_validate(reference.trace),
            random_seed=spec["random_seed"],
            synthetic_clicks=spec["synthetic_clicks"],
            tolerance_px=0.75,
            max_steps=5000,
            timeout_sec=5.0,
        ),
    )


def _build_manual_solution_rules() -> SolutionRules:
    return SolutionRules(max_score=1, auto_check=False, manual_review_required=True)


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        course_row = await conn.fetchrow("SELECT id, title FROM courses WHERE id = $1", COURSE_ID)
        if course_row is None:
            raise RuntimeError(f"Курс {COURSE_ID} не найден.")
        existing_tasks = await conn.fetchval("SELECT count(*) FROM tasks WHERE course_id = $1", COURSE_ID)

        print("=" * 78)
        print(f"tsk-412 · импорт заданий курса {COURSE_ID} «{course_row['title']}» · "
              f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
        print("=" * 78)
        print(f"Заданий в курсе сейчас: {existing_tasks} (ожидание 0 — иначе стоп, идемпотентность)")

        print(f"\nВычисляю эталонные трассы для {len(_TURTLE_SIM_TASKS)} заданий (материал 314)...")
        built_turtle_sim: List[SolutionRules] = []
        for i, spec in enumerate(_TURTLE_SIM_TASKS, start=1):
            rules = _build_turtle_sim_solution_rules(spec)
            n_seg = len(rules.turtle_sim.expected_trace.segments)
            print(f"  [{i:>2}] difficulty={spec['difficulty_id']} сегментов={n_seg} "
                  f"seed={spec['random_seed']} clicks={spec['synthetic_clicks']}")
            built_turtle_sim.append(rules)

        print(f"\nЗаданий с ручной проверкой (материалы 316/317): {len(_MANUAL_SA_COM_TASKS)}")
        for i, spec in enumerate(_MANUAL_SA_COM_TASKS, start=11):
            print(f"  [{i:>2}] difficulty={spec['difficulty_id']} {spec['stem'][:70]!r}")

        total = len(_TURTLE_SIM_TASKS) + len(_MANUAL_SA_COM_TASKS)

        if existing_tasks > 0:
            print(f"\nВ курсе {COURSE_ID} уже есть {existing_tasks} заданий — пропускаю "
                  "вставку (идемпотентность). Если это повторный частичный запуск — "
                  "разбираться вручную, скрипт не различает частичное/полное состояние.")
            return

        if not apply:
            print(f"\nDRY-RUN: {total} заданий будут вставлены (order_position 1..{total}). "
                  "Ничего не записано. Повтор с --apply.")
            return

        async with conn.transaction():
            order_position = 1
            for spec, rules in zip(_TURTLE_SIM_TASKS, built_turtle_sim):
                await conn.execute(
                    "INSERT INTO tasks (course_id, max_score, task_content, difficulty_id, "
                    "solution_rules, order_position, is_active, requirement_level) "
                    "VALUES ($1, 1, $2::jsonb, $3, $4::jsonb, $5, true, 'required')",
                    COURSE_ID,
                    json.dumps(_task_content(spec["stem"], "SA")),
                    spec["difficulty_id"],
                    rules.model_dump_json(),
                    order_position,
                )
                order_position += 1

            for spec in _MANUAL_SA_COM_TASKS:
                rules = _build_manual_solution_rules()
                await conn.execute(
                    "INSERT INTO tasks (course_id, max_score, task_content, difficulty_id, "
                    "solution_rules, order_position, is_active, requirement_level) "
                    "VALUES ($1, 1, $2::jsonb, $3, $4::jsonb, $5, true, 'recommended')",
                    COURSE_ID,
                    json.dumps(_task_content(spec["stem"], "SA_COM")),
                    spec["difficulty_id"],
                    rules.model_dump_json(),
                    order_position,
                )
                order_position += 1

            print("\nВерификация в транзакции:")
            tasks_cnt = await conn.fetchval("SELECT count(*) FROM tasks WHERE course_id = $1", COURSE_ID)
            order_dupes = await conn.fetchval(
                "SELECT COALESCE(sum(c), 0) FROM ("
                "  SELECT count(*) - 1 AS c FROM tasks WHERE course_id = $1 "
                "  GROUP BY order_position HAVING count(*) > 1"
                ") x",
                COURSE_ID,
            )
            required_cnt = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1 AND requirement_level = 'required'",
                COURSE_ID,
            )
            recommended_cnt = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1 AND requirement_level = 'recommended'",
                COURSE_ID,
            )
            null_rules = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = $1 AND solution_rules IS NULL",
                COURSE_ID,
            )
            print(f"  заданий в курсе: {tasks_cnt} (ожидание {total})")
            print(f"  коллизий order_position: {order_dupes} (ожидание 0)")
            print(f"  required: {required_cnt} (ожидание {len(_TURTLE_SIM_TASKS)}), "
                  f"recommended: {recommended_cnt} (ожидание {len(_MANUAL_SA_COM_TASKS)})")
            print(f"  solution_rules IS NULL: {null_rules} (ожидание 0)")

            ok = (
                tasks_cnt == total
                and order_dupes == 0
                and required_cnt == len(_TURTLE_SIM_TASKS)
                and recommended_cnt == len(_MANUAL_SA_COM_TASKS)
                and null_rules == 0
            )
            if not ok:
                raise RuntimeError("Верификация не сошлась — ROLLBACK.")

            if not apply:
                raise RuntimeError("DRY-RUN (внутренний): откатываю.")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
