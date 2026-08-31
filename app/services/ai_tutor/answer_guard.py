"""Страж ответа наставника: не пускать готовое решение ученику (tsk-748).

**Почему страж, а не ещё один абзац в инструкции.** 31.08 наставник в режиме
`concept` спросил ученика, на каком языке тот пишет (при слове Python и полном
условии в инструкции), сам предложил «скопируй задание, и я напишу решение», а
затем выдал программу целиком с разбором. Отвечала `anthropic/claude-sonnet-4.6`
— ГОЛОВА цепочки, не запасная модель, и системная инструкция доехала до неё
полностью (по учёту расхода вход рос от хода к ходу: 3228 → 4351 токена).

То есть защита работала ровно до тех пор, пока модель соглашалась её соблюдать.
Текст инструкции — не механизм: он просит, а не запрещает. Здесь запрет
механический — он не зависит от того, что модель решила прочитать.

**Почему фильтр потоковый.** Ответ отдаётся ученику по кускам, и стереть с
экрана уже показанное нельзя. Поэтому код не отдаётся сразу: как только в потоке
встречается ограждение блока, выдача этого куска придерживается до закрытия
блока, и блок уходит ученику только если прошёл проверку. Задержка стоит ровно
столько, сколько модель печатает один блок, — а разрешённый по методике
микро-пример короткий (3-5 строк).

**Что считается решением.** Не «любой код»: методика прямо разрешает микро-пример
на ПОСТОРОННЕЙ задаче (режим `concept`, шаг 3), и запрет всего подряд сломал бы
объяснение. Запрещается то, что переносится в поле ответа без понимания:
законченная программа (есть и ввод, и вывод), длинная простыня, пример на данных
самого задания, второй блок в одном ответе и любой собранный код в режиме `thin`.
Правила проверены на настоящих репликах сессии 57 — см. `tests/test_tsk748_*`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

FENCE = "```"

# Методика разрешает микро-пример «3-5 строк». Шестая строка — уже не иллюстрация
# приёма, а кусок программы: столько ученик переносит к себе целиком.
MAX_EXAMPLE_LINES = 5

# Ввод и вывод по языкам. Вместе в одном блоке они означают законченную программу:
# у неё есть чем питаться и что показать, её достаточно вставить и запустить.
_INPUT_MARKERS = (
    "input(", "readline", "cin >>", "cin>>", "scanf", "gets(",
    "prompt(", "read_line", "readLine", "stdin",
)
_OUTPUT_MARKERS = (
    "print(", "cout <<", "cout<<", "printf", "console.log",
    "console.write", "puts(", "echo ", "System.out",
)

# Строка, похожая на код, вне ограждения: присваивание с вызовом, вызов вывода,
# заголовок конструкции. Две такие подряд — программа, которую забыли огородить.
_CODE_LINE = re.compile(
    r"^\s*(?:[A-Za-zА-Яа-я_][\wЀ-ӿ]*\s*=\s*.+\(|"
    r"(?:print|cout|printf|console\.log|System\.out)\s*[\(<]|"
    r"(?:def|for|while|if|else|elif|return|import|from|#include)\b)"
)


@dataclass(frozen=True)
class GuardHit:
    """Факт срабатывания: что вырезано и почему. Уходит в журнал и в `meta`."""

    reason: str
    cut_chars: int
    sample: str


# Что видит ученик вместо решения. Не «ошибка» и не отказ в помощи: разговор
# продолжается, но с того места, где думать снова должен он.
BLOCKED_NOTICE = (
    "\n\nГотовую программу я не покажу — иначе задание решу я, а не ты, и на "
    "контрольной это не поможет. Давай по шагам: скажи своими словами, что "
    "программа должна сделать ПЕРВЫМ действием?"
)


def _has_input(code: str) -> bool:
    low = code.lower()
    return any(m.lower() in low for m in _INPUT_MARKERS)


def _has_output(code: str) -> bool:
    low = code.lower()
    return any(m.lower() in low for m in _OUTPUT_MARKERS)


def _stem_literals(stem: str) -> set[str]:
    """Заметные литералы условия: длинные числа и слова.

    Короткие числа (`2`, `10`) не берём — они встречаются в любом объяснении и
    дали бы ложные срабатывания на посторонних примерах.
    """
    if not stem:
        return set()
    return {n for n in re.findall(r"\d{3,}", stem)}


def judge_block(code: str, *, mode: str, stem: str, index: int) -> Optional[str]:
    """Причина запретить блок, либо `None`, если он допустим.

    Порядок проверок — от самого дешёвого и жёсткого правила к самому узкому.
    """
    if mode == "thin":
        # Одноконструкционное задание: любой собранный код равен ответу — ученик
        # просто перенесёт числа. Это уже записано в инструкции режима, теперь
        # ещё и вынуждено.
        return "режим thin: собранный код равен ответу"
    if index > 0:
        # Методика разрешает ОДИН микро-пример. Ответ из четырёх блоков подряд —
        # это справочник по языку, а не разбор его затыка: ровно так выглядели
        # реплики 157 и 159 в сессии 57.
        return "второй блок кода в одном ответе"
    if _has_input(code) and _has_output(code):
        return "законченная программа: есть и ввод, и вывод"
    lines = [ln for ln in code.splitlines() if ln.strip()]
    if len(lines) > MAX_EXAMPLE_LINES:
        return f"пример длиннее {MAX_EXAMPLE_LINES} строк ({len(lines)})"
    shared = _stem_literals(stem) & set(re.findall(r"\d{3,}", code))
    if shared:
        return f"пример собран на данных задания: {', '.join(sorted(shared))}"
    return None


class TutorStreamGuard:
    """Фильтр потока: придерживает код до проверки, обрывает ответ при сливе.

    Работает посимвольно по мере прихода кусков. Наружу отдаёт только то, что
    уже разрешено показывать; после блокировки не отдаёт ничего.
    """

    def __init__(self, *, mode: str, stem: str) -> None:
        self._mode = mode
        self._stem = stem or ""
        self._buf = ""            # необработанный хвост куска
        self._in_block = False
        self._block = ""          # тело блока, пока он не закрылся
        self._blocks_passed = 0
        self._tail = ""                    # неотданный остаток текущей строки
        self._line = ""                    # ВСЯ текущая строка, включая отданное
        self._held: Optional[str] = None   # неотданный остаток строки кода
        self._held_full: Optional[str] = None   # она же целиком — для разбора
        self.blocked = False
        self.hit: Optional[GuardHit] = None

    # ─────────────────────────── внешний контракт ───────────────────────────

    def feed(self, delta: str) -> str:
        """Принять кусок потока, вернуть то, что можно показать ученику."""
        if self.blocked:
            return ""
        self._buf += delta
        return self._drain()

    def finish(self) -> str:
        """Завершить: отдать хвост.

        Незакрытый блок (ответ обрезан пределом токенов) судим как есть: половина
        программы переносится в поле ответа так же, как целая.
        """
        if self.blocked:
            return ""
        out = ""
        if self._in_block and self._block.strip():
            out += self._close_block()
            if self.blocked:
                return out
        rest, self._buf = self._buf, ""
        out += self._release_plain(rest, final=True)
        return out

    # ─────────────────────────── внутренняя кухня ───────────────────────────

    def _drain(self) -> str:
        out: list[str] = []
        while self._buf:
            if self._in_block:
                idx = self._buf.find(FENCE)
                if idx == -1:
                    self._block += self._buf
                    self._buf = ""
                    break
                self._block += self._buf[:idx]
                self._buf = self._buf[idx + len(FENCE):]
                self._in_block = False
                out.append(self._close_block())
                if self.blocked:
                    return "".join(out)
                continue

            idx = self._buf.find(FENCE)
            if idx == -1:
                # Хвост может оказаться началом ограждения («``»), разорванным
                # между двумя кусками сети. Придерживаем его, иначе ограждение
                # проскочит фильтр по частям.
                keep = _partial_fence_tail(self._buf)
                head = self._buf[: len(self._buf) - keep] if keep else self._buf
                self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                out.append(self._release_plain(head, final=False))
                break
            out.append(self._release_plain(self._buf[:idx], final=False))
            if self.blocked:
                return "".join(out)
            # Придержанное отдаём ДО блока: иначе строка, ждавшая соседа, уехала
            # бы ученику уже после кода — то есть текст переставился бы местами.
            out.append(self._flush_pending())
            self._buf = self._buf[idx + len(FENCE):]
            self._in_block = True
            self._block = ""
        return "".join(out)

    def _close_block(self) -> str:
        """Блок закрылся: судить и либо отдать целиком, либо оборвать ответ."""
        body = self._block
        self._block = ""
        # Первая строка ограждения — язык (```python), в разбор её не берём.
        code = body.split("\n", 1)[1] if "\n" in body else body
        reason = judge_block(
            code, mode=self._mode, stem=self._stem, index=self._blocks_passed
        )
        if reason is None:
            self._blocks_passed += 1
            return f"{FENCE}{body}{FENCE}"
        self._block_now(reason, cut=body)
        return BLOCKED_NOTICE

    def _release_plain(self, chunk: str, *, final: bool) -> str:
        """Обычный текст: отдаём построчно, ловя код без ограждения.

        Две подряд строки, похожие на код, — это программа, у которой модель
        просто не поставила ограждение; фильтр по блокам такую пропустил бы.
        Придерживаем ОДНУ строку: одиночная строка кода в объяснении законна
        («введи `print(23**45)`»), пара подряд — уже нет.

        Незавершённая строка тоже придерживается: судить по половине строки
        нельзя, а поток режется сетью где угодно.
        """
        if not chunk and not final:
            return ""
        out: list[str] = []      # завершённые строки, каждая со своим переводом
        rest = chunk
        while "\n" in rest:
            head, rest = rest.split("\n", 1)
            line = self._line + head          # ВСЯ строка, включая уже отданное
            unsent = self._tail + head        # то, чего ученик ещё не видел
            self._line = ""
            self._tail = ""
            if _CODE_LINE.match(line):
                if self._held is not None:
                    self._block_now(
                        "код без ограждения: две строки программы подряд",
                        cut=f"{self._held_full}\n{line}",
                    )
                    return _join(out) + BLOCKED_NOTICE
                self._held, self._held_full = unsent, line
                continue
            if self._held is not None:
                out.append(self._held)
                self._held = self._held_full = None
            out.append(unsent)

        # Незавершённый остаток. Придерживаем его, ТОЛЬКО когда в строке уже
        # видно зачаток кода: обычная реплика («Ошибки — это нормально…») может
        # не содержать перевода строки вовсе, и придержать её до конца ответа
        # значило бы вернуть ту самую задержку, ради устранения которой сделан
        # поток. Решение принимается по ВСЕЙ строке, а не по остатку: при выдаче
        # по несколько символов целой строки в руках не оказывается никогда, и
        # проверка остатка пропускала голый код всегда.
        self._line += rest
        self._tail += rest
        trailing = ""
        if final or not (self._held is not None or _may_become_code(self._line)):
            trailing, self._tail = self._tail, ""

        if final and self._held is not None:
            # Последняя строка ответа тоже может оказаться второй строкой кода:
            # обрыв генерации не повод пропустить программу целиком.
            if trailing and _CODE_LINE.match(self._line):
                self._block_now(
                    "код без ограждения: две строки программы подряд",
                    cut=f"{self._held_full}\n{self._line}",
                )
                return _join(out) + BLOCKED_NOTICE
            out.append(self._held)
            self._held = self._held_full = None
        return _join(out) + trailing

    def _flush_pending(self) -> str:
        """Отдать всё придержанное и начать строку заново."""
        out = ""
        if self._held is not None:
            out += f"{self._held}\n"
            self._held = self._held_full = None
        out += self._tail
        self._tail = ""
        self._line = ""
        return out

    def _block_now(self, reason: str, *, cut: str) -> None:
        self.blocked = True
        self.hit = GuardHit(reason=reason, cut_chars=len(cut), sample=cut[:500])
        logger.warning(
            "ai_tutor: страж вырезал решение (%s), режим=%s, символов=%d; начало: %.200s",
            reason, self._mode, len(cut), cut.replace("\n", " "),
        )


# Незавершённая строка ещё не опознаётся как код: `a = int` станет кодом только
# со скобкой в конце. Поэтому хвост придерживается по более широкому признаку —
# «здесь может вырасти строка программы». Иначе код без ограждения проезжал бы
# мимо детектора всегда: поток приходит кусками по несколько символов, и целой
# строки в руках не оказывается никогда.
_CODE_SEED = re.compile(
    r"[=(]|^\s*(?:def|for|while|if|else|elif|return|import|from|#include|print|cout)\b"
)


def _may_become_code(pending: str) -> bool:
    """Может ли незавершённая строка оказаться строкой программы."""
    return bool(pending.strip()) and bool(_CODE_SEED.search(pending))


def _join(lines: list[str]) -> str:
    """Собрать выданные строки обратно, вернув каждой её перевод строки."""
    return "".join(f"{line}\n" for line in lines)


def _partial_fence_tail(buf: str) -> int:
    """Сколько символов хвоста могут оказаться началом ограждения."""
    for n in (2, 1):
        if buf.endswith("`" * n):
            return n
    return 0
