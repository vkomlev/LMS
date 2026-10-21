"""tsk-1395: судья стенда наставника — разбор ответа, повтор, строка «без вердикта».

Прогон 20.10: у судьи Opus 5.5 с `max_tokens=200` 40–43 хода из 107 остались
«нет JSON» — пустой текст или рассуждение, оборванное до JSON. Проверяем без
сети:
  * JSON вердикта находится и после рассуждения, и в ```json-ограждении;
    пустой ответ и рассуждение без вердикта — None;
  * при ответе без JSON — ровно один повтор с напоминанием «только JSON»,
    потолок — боевой `JUDGE_MAX_TOKENS`;
  * ход без вердикта и после повтора — отдельная строка отчёта, а не доля.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import tutor_replay_bakeoff as bakeoff  # noqa: E402

from app.services.llm.contracts import JUDGE_MAX_TOKENS, LLMResult  # noqa: E402

_VERDICT = ('{"gives_answer": false, "wrong_confirm": true, "responds": true, '
            '"leads": true, "blames": false, "note": "подтвердил 54"}')


def test_parse_json_after_reasoning() -> None:
    text = (
        "Разберу по полям. Ученик написал {54}, а верный ответ 49, значит "
        "наставник {ошибочно} подтвердил.\n\nИтог:\n" + _VERDICT
    )
    assert bakeoff.parse_judge_reply(text) == {
        "gives_answer": False, "wrong_confirm": True, "responds": True,
        "leads": True, "blames": False, "note": "подтвердил 54",
    }


def test_parse_fenced_and_last_verdict_wins() -> None:
    echoed = ('Образец: {"gives_answer": false, "wrong_confirm": false, "responds": true, '
              '"leads": true, "blames": false, "note": "..."}\n')
    text = echoed + "Мой ответ:\n```json\n" + _VERDICT + "\n```"
    assert bakeoff.parse_judge_reply(text)["wrong_confirm"] is True


def test_parse_no_verdict() -> None:
    assert bakeoff.parse_judge_reply("") is None
    assert bakeoff.parse_judge_reply(None) is None  # type: ignore[arg-type]
    # Рассуждение, оборванное до JSON (потолок токенов).
    assert bakeoff.parse_judge_reply('Посмотрим: ученик написал «54», наставник {') is None
    # Объект без полей вердикта или с частью полей — не вердикт.
    assert bakeoff.parse_judge_reply('Пример: {"a": 1}') is None
    assert bakeoff.parse_judge_reply('{"gives_answer": false}') is None
    # Образец из промпта в рассуждении, настоящий JSON оборван — вердикта нет.
    echoed = ('По образцу {"gives_answer": false, "wrong_confirm": false, "responds": true, '
              '"leads": true, "blames": false, "note": "..."} отвечу: {"gives_answer": tr')
    assert bakeoff.parse_judge_reply(echoed) is None


def _unit() -> "bakeoff.Unit":
    return bakeoff.Unit(
        session_id=104, turn=1, mode="concept", messages=[], stem="Сколько чисел?",
        student_text="54 ответ получается?", answers=["49"], course_title="Задание 3",
        original="Почти!",
    )


def _outcome() -> "bakeoff.Outcome":
    return bakeoff.Outcome(session_id=104, turn=1, model="m", student_text="54 ответ получается?",
                           text="Точно, молодец!", first_sec=3.0)


def _fake_complete(replies: list[str], calls: list[dict]):
    async def fake(messages, **kw):
        calls.append({"content": messages[-1].content, **kw})
        return LLMResult(text=replies[len(calls) - 1], model=kw["model"])
    return fake


def test_judge_retries_once_on_empty_reply(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(bakeoff, "complete", _fake_complete(["", "Итог: " + _VERDICT], calls))
    o = _outcome()
    asyncio.run(bakeoff._judge(o, _unit(), "anthropic-direct/claude-opus-5-5"))
    assert o.judge["wrong_confirm"] is True
    assert len(calls) == 2
    assert all(c["max_tokens"] == JUDGE_MAX_TOKENS for c in calls)
    assert not calls[0]["content"].endswith(bakeoff.JUDGE_JSON_ONLY)
    assert calls[1]["content"].endswith(bakeoff.JUDGE_JSON_ONLY)


def test_judge_no_retry_when_first_reply_has_json(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(bakeoff, "complete", _fake_complete([_VERDICT], calls))
    o = _outcome()
    asyncio.run(bakeoff._judge(o, _unit(), "j"))
    assert len(calls) == 1 and o.judge["note"] == "подтвердил 54"


def test_no_verdict_counted_and_reported(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(bakeoff, "complete",
                        _fake_complete(["", "Сначала подумаю: ученик написал"], calls))
    bad = _outcome()
    asyncio.run(bakeoff._judge(bad, _unit(), "j"))
    assert len(calls) == 2 and bad.judge == {"error": "нет JSON"}

    good = _outcome()
    good.judge = bakeoff.parse_judge_reply(_VERDICT)
    s = bakeoff.summarize("m", [bad, good])
    assert s["no_verdict"] == 1 and s["judged"] == 1

    report = tmp_path / "r.md"
    bakeoff.write_report(report, [s], {"m": [bad, good]}, [_unit()], "j", None)
    assert "- `m`: без вердикта судьи: 1 из 2" in report.read_text(encoding="utf-8")

    # Без судьи строки нет: считать нечего.
    plain = _outcome()
    s0 = bakeoff.summarize("m", [plain])
    assert s0["no_verdict"] is None
    bakeoff.write_report(report, [s0], {"m": [plain]}, [_unit()], None, None)
    assert "без вердикта судьи" not in report.read_text(encoding="utf-8")
