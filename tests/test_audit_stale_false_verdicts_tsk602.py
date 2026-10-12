# -*- coding: utf-8 -*-
"""tsk-602: сверка старых незачётов с нынешними правилами — чистые функции и критерий.

Главное, что проверяется: критерием расхождения служит НАСТОЯЩИЙ код проверки
(`CheckingService`), а не SQL-модель нормализации. На разборе tsk-602 SQL-модель
дала 4 ложных совпадения из 10 — расхождение с Python на неразрывном пробеле
и на шагах нормализации, которых у задания нет. Тесты фиксируют оба случая,
чтобы критерий не подменили обратно. БД не трогают.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import json  # noqa: E402

import pytest  # noqa: E402

from audit_stale_false_verdicts_tsk602 import (  # noqa: E402
    REVIEWED_PATH,
    _expected_of,
    _load_reviewed,
    _should_stay_silent,
    _split_reviewed,
)

from app.services.checking_service import CheckingService  # noqa: E402


class TestExpectedOf:
    """Короткая запись нынешнего эталона — то, что видит человек в отчёте."""

    def test_short_answer_lists_all_variants(self) -> None:
        rules = {
            "short_answer": {
                "accepted_answers": [
                    {"score": 1, "value": "1204502"},
                    {"score": 1, "value": "1204 502"},
                ]
            }
        }
        assert _expected_of(rules) == "1204502 | 1204 502"

    def test_falls_back_to_correct_options(self) -> None:
        rules = {"short_answer": None, "correct_options": ["a", "c"]}
        assert _expected_of(rules) == "варианты: a, c"

    def test_no_reference_answer(self) -> None:
        assert _expected_of({}) == "—"
        assert _expected_of(None) == "—"


class TestCriterionIsRealCheckingCode:
    """Критерий расхождения — сам сервис проверки, со всеми его тонкостями."""

    def test_nbsp_separators_inside_one_number_match_since_tsk829(self) -> None:
        """`2 102 556 498` (с NBSP) = `2102556498` — с tsk-829 это ЗАЧЁТ.

        Поведение движка сменилось намеренно, и тест сменился вместе с ним. Раньше
        он закреплял обратное: неразрывный пробел остаётся разделителем, а
        SQL-модель (`[[:punct:]]`) удаляла NBSP и давала «ложное» совпадение —
        именно так в разбор tsk-602 попал result 4202.

        Разбор tsk-828 показал, что совпадение было не ложным: ученица записала то
        же число с разрядными пробелами (их вставляет копирование из калькулятора),
        то есть решила верно и потеряла балл на записи. Расхождение SQL-модели с
        Python оказалось не ошибкой модели, а дефектом движка — tsk-829 его закрыл.

        Критерий аудита не изменился: им остаётся НАСТОЯЩИЙ код проверки, а не
        SQL-модель, — ради этого файл и заведён.
        """
        steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
        answer = "2 102 556 498"
        assert CheckingService._matches_short_answer(answer, "2102556498", steps)

    def test_spaces_between_different_values_still_matter(self) -> None:
        """Граница послабления tsk-829: `3 7` — два значения, а не число 37.

        Эталонов-наборов такого вида на проде 311, и снятие пробелов между ними
        было бы массовым ложным зачётом. Послабление одностороннее: слитный ответ
        за набор значений не засчитывается.
        """
        steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
        assert not CheckingService._matches_short_answer("37", "3 7", steps)
        assert not CheckingService._matches_short_answer(
            "123456789", "1 2 3 4 5 6 7 8 9", steps
        )

    def test_step_absent_means_punctuation_is_significant(self) -> None:
        """Без шага strip_punctuation двоеточие значимо — задание его и требует."""
        steps = ["trim", "collapse_spaces"]
        assert not CheckingService._matches_short_answer(
            "def privet()", "def privet():", steps
        )
        assert CheckingService._matches_short_answer(
            "def privet():", "def privet():", steps
        )

    def test_space_after_comma_needs_its_own_variant(self) -> None:
        """Ловушка курса 157: `10, 0` зачитывается только отдельным вариантом эталона."""
        steps = ["trim", "lower"]
        assert not CheckingService._matches_short_answer("10, 0", "10,0", steps)
        assert CheckingService._matches_short_answer("10, 0", "10, 0", steps)


class TestQuietMode:
    """tsk-636: чек стал еженедельным — молчать он обязан только по-настоящему пусто."""

    def test_молчит_когда_смотреть_не_на_что(self) -> None:
        assert _should_stay_silent(quiet=True, stale=[], type_changed=[], failed=[])

    def test_без_тихого_режима_печатает_всегда(self) -> None:
        """Ручной прогон обязан отвечать «проверено столько-то, чисто»."""
        assert not _should_stay_silent(quiet=False, stale=[], type_changed=[], failed=[])

    def test_находка_ломает_молчание(self) -> None:
        assert not _should_stay_silent(
            quiet=True, stale=[{"result_id": 1}], type_changed=[], failed=[]
        )

    def test_смежные_сигналы_тоже_ломают_молчание(self) -> None:
        """Сменённый тип и непроверяемая работа — другой повод посмотреть, но повод.

        Если считать их молчанием, чек тихо сузит охват, а в журнале будет «чисто».
        """
        assert not _should_stay_silent(
            quiet=True, stale=[], type_changed=[(1, 2, "…")], failed=[]
        )
        assert not _should_stay_silent(
            quiet=True, stale=[], type_changed=[], failed=[(1, "ошибка")]
        )


class TestReviewed:
    """tsk-1300: разобранные работы не повторяются в каждом еженедельном отчёте."""

    def test_разобранные_отброшены_новые_остались_в_порядке(self) -> None:
        fresh, dropped = _split_reviewed([5, 1, 9, 3], {1, 3, 42})
        assert fresh == [5, 9]
        assert dropped == 2

    def test_только_разобранные_дают_молчание(self) -> None:
        """Неделя без новых находок — тихий режим молчит, а не повторяет старое."""
        fresh, _ = _split_reviewed([1, 3], {1, 3})
        assert _should_stay_silent(quiet=True, stale=fresh, type_changed=[], failed=[])

    def test_нет_файла_нет_разобранных(self, tmp_path) -> None:
        assert _load_reviewed(tmp_path / "нет.json") == set()

    def test_битый_файл_падает_а_не_возвращает_всё(self, tmp_path) -> None:
        path = tmp_path / "r.json"
        path.write_text("{битый", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            _load_reviewed(path)

    def test_боевой_файл_читается(self) -> None:
        """Файл в репозитории валиден и ключи — номера работ."""
        reviewed = _load_reviewed(REVIEWED_PATH)
        assert reviewed and all(isinstance(r, int) for r in reviewed)
