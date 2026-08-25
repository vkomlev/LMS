# -*- coding: utf-8 -*-
"""tsk-661: мера успеха ИИ-наставника.

Защищается здесь ровно одно, зато самое хрупкое — признак «зачёт списан из чата».
Наставник намеренно не получает эталон (решение №1 в tsk-572), поэтому верная
сдача сразу после разговора может значить и понимание, и переписанный из чата
ответ. Мера, которая этого не различает, хуже отсутствия меры, а сам признак
держится на двух легко ломающихся мелочах:

* сравнение по границам слова — без него ответ «5» находится в любом тексте, где
  есть пятёрка, и «под вопросом» оказывается каждый второй разговор;
* быстрый зачёт считается подозрительным ТОЛЬКО когда ученик не написал ни слова:
  иначе честный разговор, где ученик всё понял и сразу пересдал, попадёт в
  подозрительные.

БД не трогают.
"""
from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

from check_tutor_outcomes import looks_copied  # noqa: E402


class TestОтветВРепликахНаставника:
    """Первый признак: верный ответ дословно есть в том, что сказал наставник."""

    def test_наставник_назвал_ответ(self):
        assert looks_copied(
            answer="243",
            tutor_text="Посчитай ещё раз: получится 243, проверь на своём коде.",
            seconds_after=600,
            student_msgs=3,
        )

    def test_короткий_ответ_не_ловится_как_подстрока(self):
        """«5» внутри «15 минут» — не тот же ответ.

        Это главная ловушка признака: без границ слова любой разговор, где
        мелькнула цифра, объявлялся бы списыванием.
        """
        assert not looks_copied(
            answer="5",
            tutor_text="Попробуй прикинуть за 15 минут, что делает этот цикл.",
            seconds_after=600,
            student_msgs=3,
        )

    def test_ответ_словом_внутри_другого_слова_не_ловится(self):
        assert not looks_copied(
            answer="цикл",
            tutor_text="Посмотри, что происходит на каждом цикле работы программы.",
            seconds_after=600,
            student_msgs=2,
        )

    def test_регистр_и_лишние_пробелы_не_прячут_ответ(self):
        assert looks_copied(
            answer="  Создать   прочитать ",
            tutor_text="Действия обычно называют так: создать прочитать изменить удалить.",
            seconds_after=600,
            student_msgs=2,
        )

    def test_наставник_ответа_не_называл(self):
        assert not looks_copied(
            answer="243",
            tutor_text="Что именно ты увидел при запуске — строку или пустой вывод?",
            seconds_after=219,
            student_msgs=2,
        )


class TestБыстрыйЗачёт:
    """Второй признак: сдача через секунды после реплики и ни слова от ученика."""

    def test_мгновенная_сдача_без_единого_слова_ученика(self):
        assert looks_copied(
            answer="что-то своё",
            tutor_text="Смотри, с чего начинается перебор.",
            seconds_after=4,
            student_msgs=0,
        )

    def test_быстрая_сдача_после_разговора_подозрительной_не_считается(self):
        """Ученик говорил — значит работа была, а быстрота ей не помеха."""
        assert not looks_copied(
            answer="что-то своё",
            tutor_text="Смотри, с чего начинается перебор.",
            seconds_after=4,
            student_msgs=2,
        )

    def test_молчаливый_но_не_мгновенный_зачёт_чист(self):
        assert not looks_copied(
            answer="что-то своё",
            tutor_text="Смотри, с чего начинается перебор.",
            seconds_after=600,
            student_msgs=0,
        )


class TestГраничныеСлучаи:
    def test_пустой_ответ_ничего_не_ловит(self):
        """Пустая строка встречается в любом тексте — признак обязан молчать."""
        assert not looks_copied(
            answer="", tutor_text="любой текст", seconds_after=600, student_msgs=1
        )

    def test_разговор_без_реплик_наставника(self):
        assert not looks_copied(
            answer="243", tutor_text="", seconds_after=None, student_msgs=0
        )
