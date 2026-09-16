"""Стенд наставника на ИСТОРИЧЕСКИХ диалогах учеников (tsk-957).

**Чем отличается от `llm_model_bakeoff.py`.** Тот стенд гоняет один выдуманный
злейший сценарий на промпте из файла методики. Здесь кандидату подают то, что
реально получала боевая модель: системную инструкцию, собранную боевым кодом
(`build_system_prompt`), настоящее задание и настоящие реплики ученика из
`ai_tutor_session` / `ai_tutor_message` — с опечатками, «не знаю» и просьбами
«напиши за меня». Кандидат отвечает на КАЖДЫЙ ход каждого разговора, а
предыдущие ходы берутся из истории как есть (ответы прежней боевой модели).
Это единственный способ проверить модель на разнообразии входа, которого стенд
с одним сценарием не даёт (правило 1 `model-routing.md`).

**Что меряется автоматически** (по каждому ответу):
  * дожил ли ответ: ошибка провайдера, пустой поток, таймаут;
  * время до первого куска — и доля ответов, которые бой срезал бы пределом 12 c;
  * срез стражем вывода (`TutorStreamGuard`) — модель полезла в готовое решение;
  * слив эталона: верный ответ из `solution_rules` встретился в тексте, хотя в
    условии его нет;
  * срыв роли: обещает написать решение / просит прислать условие, которое есть;
  * не по-русски: доля кириллицы среди букв вне кода ниже порога;
  * (по флагу `--judge-model`) оценка второй моделью: не выдал ли ответ, ответил
    ли на реплику ученика, ведёт ли вопросом или шагом, не упрекает ли.

**Чего стенд НЕ меряет.** Педагогику по-настоящему: судья-модель — фильтр для
глаз, а не вердикт. Отчёт печатает худшие ответы каждого кандидата — их надо
читать (п. 5 регламента tsk-573). Скорость — по одному проходу; решение о
голове цепочки требует второго прохода в другой час (правило 2).

Данные: выгрузка `ai_tutor_session`/`ai_tutor_message`/`tasks` в JSON (только
SELECT, тестовые учётки исключены). Запуск:

    .venv/Scripts/python.exe scripts/tutor_replay_bakeoff.py --dataset dialogs.json \
        --models a,b,c --judge-model z-ai/glm-5.3 --dump replay-out.json
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import pathlib
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")

from app.services.ai_tutor.answer_guard import TutorStreamGuard  # noqa: E402
from app.services.ai_tutor.prompt import (  # noqa: E402
    STUDENT_DATA_CLOSE,
    STUDENT_DATA_OPEN,
    TutorTaskView,
    build_opening_user_message,
    build_system_prompt,
)
from app.services.llm import Budget, LLMError, LLMMessage, complete, stream  # noqa: E402
from app.services.llm import providers  # noqa: E402

FIRST_TOKEN_LIMIT = Budget.INTERACTIVE.first_token_timeout or 12.0

# Учёт расхода пишет в БД приложения; стенду он не нужен, а без поднятой базы
# каждый вызов оставлял бы в выводе трассировку.
logging.getLogger("app.services.llm.usage").setLevel(logging.CRITICAL)

_ASKS_LANGUAGE = re.compile(r"(на каком|какой)\s+(языке|язык)\s*(программирования)?", re.I)
_PROMISES_SOLUTION = re.compile(
    r"(напишу|напишем|дам|покажу)\s+(тебе\s+)?(готов\w+\s+)?(решени\w+|программ\w+|код)"
    r"|скопируй\s+(текст\s+)?задани|пришли\s+(мне\s+)?(текст\s+)?(условие|задание)",
    re.I,
)
_CODE_FENCE = re.compile(r"```.*?```", re.S)
_LETTERS = re.compile(r"[A-Za-zА-Яа-яЁё一-鿿]")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_PROVIDER_ERROR_TEXT = re.compile(r"^\s*\[req_\w+\]|from AI provider|Model not found", re.I)

JUDGE_PROMPT = """Ты проверяешь реплику ИИ-наставника школьнику. Наставник НЕ должен выдавать
ответ или готовое решение — он ведёт ученика к ответу вопросами и маленькими шагами.
Подтвердить верный ответ, который ученик УЖЕ назвал сам, — это не слив, это нормально.

Условие задания:
{stem}

Верный ответ (ученику НЕ показывается; тебе — чтобы заметить слив и ложное подтверждение): {answers}

Последняя реплика ученика: {student}

Реплика наставника, которую оцениваешь:
{tutor}

Ответь ТОЛЬКО JSON без пояснений, поля:
gives_answer — наставник сам назвал верный ответ или дал готовое решение, которого ученик ещё не называл;
wrong_confirm — наставник подтвердил как верный ответ или рассуждение ученика, которое на самом деле неверно;
responds — наставник отреагировал именно на то, что написал ученик;
leads — ведёт дальше: вопрос или подсказка-шаг, а не лекция и не повтор прежнего;
blames — упрекает или оценивает ученика («ты невнимателен», «неверно, подумай ещё»);
note — одна короткая фраза по-русски.
{{"gives_answer": false, "wrong_confirm": false, "responds": true, "leads": true, "blames": false, "note": "..."}}"""


@dataclass
class Unit:
    """Один ход разговора, на который отвечает кандидат."""

    session_id: int
    turn: int                     # 0 — вступление наставника, k — ответ на k-ю реплику ученика
    mode: str
    messages: list[LLMMessage]    # system + история + текущая реплика
    stem: str
    student_text: str             # что написал ученик (пусто у вступления)
    answers: list[str]
    course_title: str
    original: str                 # что ответила боевая модель тогда
    student_said: str = ""        # всё, что ученик писал до этого хода (и снимок ответа)


@dataclass
class Outcome:
    session_id: int
    turn: int
    model: str
    student_text: str
    text: str = ""
    shown: str = ""
    error: Optional[str] = None
    first_sec: Optional[float] = None
    total_sec: float = 0.0
    guard_reason: Optional[str] = None
    leak: bool = False
    role_fail: list[str] = field(default_factory=list)
    non_russian: bool = False
    judge: Optional[dict] = None

    @property
    def alive(self) -> bool:
        return self.error is None and bool(self.text.strip())


# ────────────────────────────── Данные ──────────────────────────────────────

def _view(sess: dict) -> TutorTaskView:
    task = sess["task"]

    class _Shim:
        id = sess["task_id"]
        task_content = task["task_content"]

    return TutorTaskView.from_task(_Shim, course_title=task.get("course_title"))


def build_units(sess: dict, *, openings: bool) -> list[Unit]:
    """Разложить разговор на ходы. История до хода — как в бою (`build_llm_messages`)."""
    view = _view(sess)
    system = build_system_prompt(view, sess["mode"], student_answer=sess.get("student_answer_snapshot"))
    base = [LLMMessage(role="system", content=system)]
    history: list[LLMMessage] = []
    units: list[Unit] = []
    student_no = 0
    msgs = sess["messages"]
    said = [sess.get("student_answer_snapshot") or ""]
    if openings and msgs and msgs[0]["role"] == "tutor":
        units.append(Unit(
            session_id=sess["id"], turn=0, mode=sess["mode"],
            messages=base + [LLMMessage(role="user", content=build_opening_user_message(
                view, sess.get("student_answer_snapshot")))],
            stem=view.stem, student_text="", answers=sess["task"]["answers"],
            course_title=view.course_title or "", original=msgs[0]["content"],
            student_said="\n".join(said),
        ))
    for i, m in enumerate(msgs):
        if m["role"] == "student":
            student_no += 1
            said.append(m["content"])
            reply = next((x["content"] for x in msgs[i + 1:] if x["role"] == "tutor"), "")
            current = LLMMessage(
                role="user",
                content=f"{STUDENT_DATA_OPEN}\n{m['content'].strip()}\n{STUDENT_DATA_CLOSE}",
            )
            units.append(Unit(
                session_id=sess["id"], turn=student_no, mode=sess["mode"],
                messages=base + history + [current], stem=view.stem,
                student_text=m["content"], answers=sess["task"]["answers"],
                course_title=view.course_title or "", original=reply,
                student_said="\n".join(said),
            ))
        history.append(LLMMessage(
            role="user" if m["role"] == "student" else "assistant", content=m["content"],
        ))
    return units


# ────────────────────────────── Проверки ────────────────────────────────────

def _leaks(text: str, answers: list[str], stem: str, student_said: str = "") -> bool:
    """Верный ответ в тексте наставника, которого нет ни в условии, ни у ученика.

    Подтвердить ответ, который ученик назвал сам, — не слив: на прогоне 15.09
    регулярка без этой оговорки давала 10 ложных срабатываний на одно настоящее
    («49» из реплики ученика, «цикл» из условия, «МОРЕ» набранное латиницей).
    Короткие числа (1-2 знака) считаем сливом только рядом со словами
    «ответ/получится/равно» — иначе любое «шаг 2» ложно срабатывает.
    """
    known = (stem + "\n" + student_said).lower()
    for a in answers:
        a = a.strip()
        if not a or a.lower() in known:
            continue
        pat = re.escape(a)
        if len(a) <= 2 and a.isdigit():
            if re.search(rf"(ответ|получ\w+|равн\w+|итог\w*|будет)[^\n\d]{{0,25}}\b{pat}\b", text, re.I):
                return True
            continue
        if re.search(rf"(?<![\w.]){pat}(?![\w.])", text, re.I):
            return True
    return False


def _role_fails(text: str, course_title: str) -> list[str]:
    found: list[str] = []
    if "python" in course_title.lower() and _ASKS_LANGUAGE.search(text):
        found.append("спрашивает язык (он в названии курса)")
    if _PROMISES_SOLUTION.search(text):
        found.append("обещает решение / просит условие")
    return found


def _non_russian(text: str) -> bool:
    plain = _CODE_FENCE.sub("", text)
    letters = _LETTERS.findall(plain)
    if len(letters) < 40:
        return False
    return len(_CYRILLIC.findall(plain)) / len(letters) < 0.6


async def _judge(o: Outcome, unit: Unit, judge_model: str) -> None:
    prompt = JUDGE_PROMPT.format(
        stem=unit.stem[:1500], answers=", ".join(unit.answers) or "—",
        student=unit.student_text or "(вступление, ученик ещё ничего не писал)",
        tutor=o.text[:2500],
    )
    try:
        res = await complete(
            [LLMMessage(role="user", content=prompt)], model=judge_model,
            purpose="tutor_replay_judge", max_tokens=200, temperature=0.0,
        )
        m = re.search(r"\{.*\}", res.text, re.S)
        o.judge = json.loads(m.group(0)) if m else {"error": "нет JSON"}
    except (LLMError, json.JSONDecodeError) as exc:
        o.judge = {"error": f"{type(exc).__name__}: {exc}"[:120]}


# ────────────────────────────── Прогон ──────────────────────────────────────

async def run_unit(model: str, unit: Unit) -> Outcome:
    o = Outcome(session_id=unit.session_id, turn=unit.turn, model=model,
                student_text=unit.student_text)
    guard = TutorStreamGuard(mode=unit.mode, stem=unit.stem)
    raw: list[str] = []
    shown = ""
    t0 = time.monotonic()
    try:
        # Бюджет батча: предел первого куска — наша ось измерения, а не отсечка.
        async for chunk in stream(unit.messages, model=model, purpose="tutor_replay",
                                  budget=Budget.BATCH, max_tokens=900):
            if chunk.done:
                if chunk.truncated:
                    o.error = "обрыв после первого куска"
                break
            if o.first_sec is None and chunk.delta:
                o.first_sec = time.monotonic() - t0
            raw.append(chunk.delta)
            shown += guard.feed(chunk.delta)
        shown += guard.finish()
    except LLMError as exc:
        o.error = f"{type(exc).__name__}: {exc}"[:160]
    except Exception as exc:  # noqa: BLE001 — отчёт важнее падения
        o.error = f"{type(exc).__name__}: {exc}"[:160]
    o.total_sec = time.monotonic() - t0
    o.text = "".join(raw)
    o.shown = shown
    if o.error is None and _PROVIDER_ERROR_TEXT.search(o.text):
        # Маршрутизатор умеет отдавать отказ ТЕКСТОМ при HTTP 200 (и биллить его
        # по минимуму в 1000 токенов) — без этой проверки он сошёл бы за ответ.
        o.error = "upstream_in_200: " + " ".join(o.text.split())[:120]
        o.text = ""
    if o.text:
        o.guard_reason = guard.hit.reason if guard.hit else None
        o.leak = _leaks(o.text, unit.answers, unit.stem, unit.student_said)
        o.role_fail = _role_fails(o.text, unit.course_title)
        o.non_russian = _non_russian(o.text)
    return o


async def run_model(model: str, units: list[Unit], concurrency: int,
                    judge_model: Optional[str]) -> list[Outcome]:
    sem = asyncio.Semaphore(concurrency)

    async def one(u: Unit) -> Outcome:
        async with sem:
            o = await run_unit(model, u)
            if judge_model and o.alive:
                await _judge(o, u, judge_model)
            return o

    return await asyncio.gather(*(one(u) for u in units))


# ────────────────────────────── Сводка ──────────────────────────────────────

def summarize(model: str, outs: list[Outcome]) -> dict:
    alive = [o for o in outs if o.alive]
    firsts = sorted(o.first_sec for o in alive if o.first_sec is not None)
    judged = [o for o in alive if o.judge and "error" not in o.judge]

    def share(pred) -> Optional[float]:
        return (sum(1 for o in alive if pred(o)) / len(alive)) if alive else None

    def jshare(key: str, want: bool) -> Optional[float]:
        return (sum(1 for o in judged if bool(o.judge.get(key)) is want) / len(judged)) if judged else None

    p90 = firsts[min(len(firsts) - 1, int(len(firsts) * 0.9))] if firsts else None
    return {
        "model": model, "units": len(outs), "alive": len(alive),
        "errors": sorted({o.error for o in outs if o.error}),
        "first_med": statistics.median(firsts) if firsts else None,
        "first_p90": p90,
        "over_limit": share(lambda o: (o.first_sec or 0) > FIRST_TOKEN_LIMIT),
        "guard": share(lambda o: bool(o.guard_reason)),
        "leak": share(lambda o: o.leak),
        "role": share(lambda o: bool(o.role_fail)),
        "non_ru": share(lambda o: o.non_russian),
        "chars_med": statistics.median(len(o.text) for o in alive) if alive else None,
        "judged": len(judged),
        "j_gives_answer": jshare("gives_answer", True),
        "j_wrong_confirm": jshare("wrong_confirm", True),
        "j_responds": jshare("responds", True),
        "j_leads": jshare("leads", True),
        "j_blames": jshare("blames", True),
    }


def verdict(s: dict) -> str:
    if s["alive"] == 0:
        return "МЕРТВА"
    if s["alive"] / s["units"] < 0.9:
        return "НЕНАДЁЖНА"
    if (s["over_limit"] or 0) > 0.2 or (s["first_med"] or 0) > FIRST_TOKEN_LIMIT / 2:
        return "МЕДЛЕННО"
    if (s["leak"] or 0) > 0 or (s["guard"] or 0) > 0.05 or (s["j_gives_answer"] or 0) > 0.05:
        return "СЛИВАЕТ"
    if (s["j_wrong_confirm"] or 0) > 0.05:
        return "ОШИБАЕТСЯ"
    if (s["non_ru"] or 0) > 0 or (s["role"] or 0) > 0.05:
        return "СРЫВ РОЛИ"
    return "ГОДНА"


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _sec(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.1f} c"


def worst_samples(outs: list[Outcome], n: int = 3) -> list[Outcome]:
    """Худшие ответы — первыми: их и надо читать."""
    def badness(o: Outcome) -> int:
        j = o.judge or {}
        return (
            (4 if o.leak else 0) + (4 if o.guard_reason else 0) + (3 if o.role_fail else 0)
            + (3 if o.non_russian else 0) + (3 if j.get("gives_answer") else 0)
            + (3 if j.get("wrong_confirm") else 0)
            + (2 if j.get("blames") else 0) + (1 if j.get("responds") is False else 0)
            + (1 if j.get("leads") is False else 0)
        )
    alive = [o for o in outs if o.alive]
    return sorted(alive, key=badness, reverse=True)[:n]


def write_report(path: pathlib.Path, summaries: list[dict], results: dict[str, list[Outcome]],
                 units: list[Unit], judge_model: Optional[str], balance: Optional[dict]) -> None:
    sessions = sorted({u.session_id for u in units})
    lines = [
        f"# Стенд наставника на исторических диалогах — {dt.date.today().isoformat()}",
        "",
        f"Прогон: `scripts/tutor_replay_bakeoff.py`. Ходов на модель: **{len(units)}** "
        f"из {len(sessions)} разговоров боевой базы (тестовые учётки исключены). "
        f"Судья-модель: `{judge_model or 'нет'}`. Предел первого куска в бою: {FIRST_TOKEN_LIMIT:.0f} c.",
    ]
    if balance:
        lines.append(f"Баланс ключа: `credits={balance.get('credits')}`, `usage={balance.get('usage')}`.")
    lines += [
        "",
        "Каждый кандидат отвечает на каждый ход каждого разговора: системная инструкция "
        "собрана боевым кодом, история до хода — настоящая (ответы прежней модели), реплика "
        "ученика — дословно. «Слив» — верный ответ из эталона в тексте; «страж» — срез "
        "`TutorStreamGuard`; «>12 c» — доля ответов, которые бой срезал бы пределом первого куска.",
        "",
        "| Модель | Вердикт | Ответов | 1й кусок мед./p90 | >12 c | Страж | Слив | Роль | Не рус. | "
        "Судья: выдал ответ | Судья: подтвердил неверное | Судья: ответил ученику | Судья: ведёт | Судья: упрекает |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| `{s['model']}` | **{verdict(s)}** | {s['alive']}/{s['units']} | "
            f"{_sec(s['first_med'])} / {_sec(s['first_p90'])} | {_pct(s['over_limit'])} | "
            f"{_pct(s['guard'])} | {_pct(s['leak'])} | {_pct(s['role'])} | {_pct(s['non_ru'])} | "
            f"{_pct(s['j_gives_answer'])} | {_pct(s['j_wrong_confirm'])} | {_pct(s['j_responds'])} | {_pct(s['j_leads'])} | "
            f"{_pct(s['j_blames'])} |"
        )
    lines += ["", "## Ошибки провайдера", ""]
    for s in summaries:
        if s["errors"]:
            lines.append(f"- `{s['model']}`: " + "; ".join(s["errors"][:4]))
    if not any(s["errors"] for s in summaries):
        lines.append("нет")
    lines += ["", "## Худшие ответы каждого кандидата (читать глазами)", ""]
    umap = {(u.session_id, u.turn): u for u in units}
    for s in summaries:
        lines.append(f"### `{s['model']}`")
        for o in worst_samples(results[s["model"]]):
            u = umap[(o.session_id, o.turn)]
            flags = []
            if o.leak:
                flags.append("СЛИВ")
            if o.guard_reason:
                flags.append(f"страж: {o.guard_reason}")
            flags += o.role_fail
            if o.non_russian:
                flags.append("не по-русски")
            if o.judge and "error" not in o.judge:
                flags.append("судья: " + ", ".join(k for k in ("gives_answer", "wrong_confirm", "blames") if o.judge.get(k))
                             + (f" — {o.judge.get('note', '')}" if o.judge.get("note") else ""))
            lines += [
                "",
                f"**Сессия {o.session_id}, ход {o.turn}** (режим `{u.mode}`, первый кусок {_sec(o.first_sec)})"
                + (f" — {'; '.join(flags)}" if flags else ""),
                f"- Ученик: «{(u.student_text or '—')[:200]}»",
                "- Кандидат:", "", "```", o.text.strip()[:1200], "```",
            ]
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _balance() -> Optional[dict]:
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from llm_model_bakeoff import _env, credits  # type: ignore
        key, base = _env()
        return credits(base, key)
    except Exception:  # noqa: BLE001 — баланс не обязателен
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Стенд наставника на исторических диалогах (tsk-957)")
    ap.add_argument("--dataset", required=True, help="JSON-выгрузка сессий и реплик")
    ap.add_argument("--models", help="кандидаты через запятую; по умолчанию боевая цепочка")
    ap.add_argument("--judge-model", help="модель-судья ответов (без неё — только механические проверки)")
    ap.add_argument("--no-openings", action="store_true", help="не гонять вступительные реплики")
    ap.add_argument("--sessions", help="ограничить список сессий (через запятую)")
    ap.add_argument("--limit", type=int, help="взять первые N ходов (для дешёвой пробы)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="docs/qa")
    ap.add_argument("--dump", help="куда сложить все ответы (JSON)")
    ap.add_argument("--rejudge", help="не генерировать заново: взять ответы из этого дампа и только пересудить")
    args = ap.parse_args()

    if not os.environ.get("CLOSEROUTER_API_KEY"):
        print("Нет ключа провайдера (CLOSEROUTER_API_KEY) — прогон невозможен.")
        return 2
    data = json.loads(pathlib.Path(args.dataset).read_text(encoding="utf-8"))
    sessions = data["sessions"]
    if args.sessions:
        wanted = {int(x) for x in args.sessions.split(",")}
        sessions = [s for s in sessions if s["id"] in wanted]
    units: list[Unit] = []
    for s in sessions:
        units += build_units(s, openings=not args.no_openings)
    if args.limit:
        units = units[: args.limit]
    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              if args.models else providers.tutor_models())
    balance = _balance()
    print(f"Ходов: {len(units)} из {len(sessions)} сессий; моделей: {len(models)}; "
          f"баланс: {balance}")

    results: dict[str, list[Outcome]] = {}
    summaries: list[dict] = []

    prior: dict[str, list[Outcome]] = {}
    if args.rejudge:
        # Пересуживание: ответы уже сгенерированы, меняется только оценка. Так
        # правка промпта судьи не требует платить за генерацию заново и не
        # рассинхронизирует модели, прогнанные до и после правки.
        raw = json.loads(pathlib.Path(args.rejudge).read_text(encoding="utf-8"))
        prior = {m: [Outcome(**o) for o in outs_] for m, outs_ in raw.items()}
        models = [m for m in models if m in prior] if args.models else list(prior)

    async def run_all() -> None:
        # Один цикл событий на весь прогон: движок БД учёта расхода привязывается
        # к первому циклу, и `asyncio.run` на каждую модель ронял его закрытием.
        umap = {(u.session_id, u.turn): u for u in units}
        for model in models:
            t0 = time.monotonic()
            if args.rejudge:
                outs = prior[model]
                sem = asyncio.Semaphore(args.concurrency)

                async def rejudge_one(o: Outcome) -> None:
                    async with sem:
                        u = umap.get((o.session_id, o.turn))
                        if u is None or not o.alive:
                            return
                        o.leak = _leaks(o.text, u.answers, u.stem, u.student_said)
                        o.role_fail = _role_fails(o.text, u.course_title)
                        if args.judge_model:
                            o.judge = None
                            await _judge(o, u, args.judge_model)

                await asyncio.gather(*(rejudge_one(o) for o in outs))
            else:
                outs = await run_model(model, units, args.concurrency, args.judge_model)
            results[model] = outs
            s = summarize(model, outs)
            summaries.append(s)
            print(f"[{verdict(s):10}] {model}: ответов {s['alive']}/{s['units']}, "
                  f"1й кусок мед. {_sec(s['first_med'])}, >12 c {_pct(s['over_limit'])}, "
                  f"страж {_pct(s['guard'])}, слив {_pct(s['leak'])}, "
                  f"судья-выдал {_pct(s['j_gives_answer'])} ({time.monotonic() - t0:.0f} c)",
                  flush=True)
            if s["errors"]:
                print("    ошибки: " + "; ".join(s["errors"][:3]), flush=True)
            if args.dump:
                pathlib.Path(args.dump).write_text(json.dumps(
                    {m: [asdict(o) for o in outs_] for m, outs_ in results.items()},
                    ensure_ascii=False, indent=1), encoding="utf-8")

    asyncio.run(run_all())

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    report = outdir / f"{stamp}-tutor-replay-bakeoff.md"
    if report.exists():
        report = outdir / f"{dt.datetime.now().strftime('%Y-%m-%d-%H%M%S')}-tutor-replay-bakeoff.md"
    judge_label = args.judge_model or ("сохранён из дампа" if args.rejudge else None)
    write_report(report, summaries, results, units, judge_label, balance)
    print(f"Отчёт: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
