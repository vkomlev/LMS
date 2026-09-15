# -*- coding: utf-8 -*-
"""tsk-945: «приземление» абстрактных заданий курса «Чат-боты TG/VK/Max».

Пять заданий курса ссылались на код «из урока» или на файл в zip, а сам код в
условии отсутствовал — проверяющему не на что опереться, ученик сдаёт одну строку.
Правило self-contained (ADR-0042, `assignment-rules.md` §9 п.2) публикатор
ContentBackbone поддерживает полем `context` блока `task`, но в исходнике курса
оно не было заполнено.

Скрипт делает две согласованные вещи (иначе LMS и исходник разъедутся):

1. `--source` — правит исходник CreateCourses: `exports/wp-blocks/<урок>.json` и
   `exports/lms-graph-2026-07-06.json` (обе копии, как в tsk-856): заполняет
   `context`, переписывает `stem`/`hint`, в уроке 1.4 добавляет второе задание
   («прокачка» Бот-Эхо вынесена из #5832 решением оператора 2026-09-15).
2. `--apply` — пишет в боевой LMS через API (сервисный ключ в `LMS_API_KEY`):
   `PATCH /tasks/{id}` с новым `task_content` (условие с вшитым кодом в том же
   виде, что даёт публикатор) и `solution_rules` с подтверждёнными критериями
   (`status=approved`, `reviewed_by=2`, `origin=manual`, как 4820/4821). Новое
   задание `#q3` сначала создаётся `POST /tasks/bulk-upsert`, затем PATCH-ится
   критериями — так у него появляется пометка `manual_web`, и переиздание курса
   (в исходнике критериев нет) их не сотрёт.

Без флагов — только план: показывает будущие условия и критерии.

Запуск (после протокола /db-check, план и выборка — в reviews/2026-09-15-tsk945-*.md):
    python scripts/tsk945_ground_tasks.py --source
    LMS_API_KEY=... python scripts/tsk945_ground_tasks.py --apply
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tsk945")

COURSE_DIR = Path(r"D:\Work\CreateCourses\courses\chat-boty-tg-vk-max")
WP_BLOCKS = COURSE_DIR / "exports" / "wp-blocks"
LMS_GRAPH = COURSE_DIR / "exports" / "lms-graph-2026-07-06.json"
API_BASE = os.environ.get("LMS_API_BASE", "https://api.learn.victor-komlev.ru/api/v1")

#: Оператор, подтвердивший критерии (victor.komlev@mail.ru).
REVIEWED_BY = 2
CRITERIA_NOTE = (
    "Критерии согласованы с оператором в чате 2026-09-15 (tsk-945); "
    "код задания вшит в условие, ответ проверяется по нему."
)

# ---------------------------------------------------------------------------
# Код, который вшивается в условия (без подсказок преподавателю).
# ---------------------------------------------------------------------------

CODE_BROKEN_BOT = """import telebot
from config import TOKEN

bot = telebot.TeleBot(TOKEN)


@bot.message_handler(commands=['start'])
def say_hi(message):
    bot.send_message(message.chat.id, "Привет! Я починенный бот.")"""

CODE_ECHO_BOT = """import telebot
from config import TOKEN

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def say_hi(message):
    bot.send_message(message.chat.id, "Привет! Я повторяю за тобой.")

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)

bot.infinity_polling()"""

CODE_SKELETON = """import telebot

bot = telebot.TeleBot("сюда_вставь_токен")

bot.infinity_polling()"""

CODE_NAIVE_GUESS = """import telebot
import random
from config import TOKEN

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(func=lambda message: message.text.isdigit())
def guess(message):
    zagadannoe = random.randint(1, 100)
    dogadka = int(message.text)
    if dogadka < zagadannoe:
        bot.send_message(message.chat.id, "Моё число больше.")
    elif dogadka > zagadannoe:
        bot.send_message(message.chat.id, "Моё число меньше.")
    else:
        bot.send_message(message.chat.id, "Угадал!")

bot.infinity_polling()"""

CODE_THREE_ECHOES = """# 1. Telegram (библиотека telebot)
@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.send_message(message.chat.id, message.text)

bot.infinity_polling()


# 2. ВКонтакте (библиотека vk_api)
for event in longpoll.listen():
    if event.type == VkEventType.MESSAGE_NEW and event.to_me:
        otpravit(event.user_id, event.text)


# 3. Max (библиотека maxapi)
@dp.message_created()
async def echo(event: MessageCreated):
    await event.message.answer(event.message.text)

async def main():
    await dp.start_polling(bot)"""


def _criteria(must: list[str], accept: list[str], reject: list[str], notes: str) -> dict[str, Any]:
    """Блок `grading_criteria` в подтверждённом виде (образец — задания 4820/4821)."""
    return {
        "must": must,
        "accept": accept,
        "reject": reject,
        "notes": notes,
        "status": "approved",
        "origin": "manual",
        "reviewed_by": REVIEWED_BY,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "generated_by_model": None,
        "generated_at": None,
        "draft_warning": None,
    }


# ---------------------------------------------------------------------------
# Описание правок: одно задание = один словарь.
#   lesson  — файл урока в wp-blocks (без .json)
#   task_id — id в LMS (None для нового задания)
#   block_index — индекс блока task в уроке (None → добавить в конец урока)
# ---------------------------------------------------------------------------

TASKS: list[dict[str, Any]] = [
    {
        "key": "5832",
        "task_id": 5832,
        "lesson": "1.4-bot-molchit-chinim",
        "external_uid": "authored:chat-boty-tg-vk-max:bot-molchit-chinim#q1",
        "block_index": 26,
        "ttype": "SA_COM",  # в LMS задание уже SA_COM (ответ + комментарий), исходник догоняет
        "title": "Поиск причины молчащего бота",
        "context": CODE_BROKEN_BOT,
        # Выноска «Зачёт, если» в теле урока (WP + материал LMS) описывала оба
        # требования разом — теперь у каждого задания своя.
        "callout": (
            "<b>Зачёт, если:</b><br>✅ причина, почему бот молчал, названа словами: программа "
            "доходит до конца и завершается, бот не начинает слушать сообщения;<br>✅ названа "
            "строка bot.infinity_polling() и место — последней строкой файла, после обработчиков."
        ),
        "stem": (
            "Этот бот запускается без единой ошибки, но на /start не отвечает — молчит. "
            "Токен верный, программа не закрывалась. Разберись по коду выше и ответь двумя "
            "пунктами, каждый одним предложением: 1) причина словами — чего в этом коде не "
            "хватает и почему из-за этого бот не слышит сообщения; 2) строка, которую нужно "
            "добавить, и куда именно в файле её поставить."
        ),
        "hint": (
            "Программа выполняется сверху вниз и заканчивается на последней строке. Проверь "
            "конец файла: что там должно стоять после обработчиков, чтобы бот начал слушать "
            "сообщения?"
        ),
        "review_note": (
            "Названа причина (нет запуска приёма сообщений — программа доходит до конца и "
            "завершается) и строка bot.infinity_polling() последней строкой файла."
        ),
        "criteria": _criteria(
            must=[
                "Названа причина: в конце файла нет запуска приёма сообщений (bot.infinity_polling() "
                "или bot.polling()) — программа доходит до конца и завершается, бот не начинает "
                "слушать чат",
                "Названа строка-починка bot.infinity_polling() (принимается bot.polling()) и место: "
                "последней строкой файла, после всех обработчиков",
            ],
            accept=[
                "Любые формулировки причины со смыслом «бот не запущен в режиме ожидания сообщений», "
                "«нет цикла опроса», «программа сразу завершается»",
                "bot.polling(none_stop=True) и другие варианты polling — верная починка",
                "Ответ целиком в поле комментария, а в поле ответа пусто или одно слово — норма",
            ],
            reject=[
                "Причина названа как неверный токен, опечатка в декораторе или отступы — в этом "
                "коде их нет",
                "Только строка без причины или только причина без строки — задание одно на оба "
                "пункта, половину не засчитывать",
                "Строка поставлена выше обработчиков (до @bot.message_handler) — обработчики после "
                "запуска не зарегистрируются",
            ],
            notes=(
                "Ученик 09.2026 сдал одну строку «нет bot.infinity_polling()» — по этим критериям "
                "нужны и причина словами, и место строки. " + CRITERIA_NOTE
            ),
        ),
    },
    {
        "key": "new-q3",
        "task_id": None,
        "lesson": "1.4-bot-molchit-chinim",
        "external_uid": "authored:chat-boty-tg-vk-max:bot-molchit-chinim#q3",
        "block_index": None,
        "course_id": 928,
        "title": "Прокачай Бот-Эхо: особый ответ на «привет»",
        "ttype": "SA_COM",
        "context": CODE_ECHO_BOT,
        "callout": (
            "<b>Зачёт, если:</b><br>✅ на слово «привет» в любом написании бот отвечает "
            "по-особому, остальное по-прежнему повторяет;<br>✅ обработчик «привет» стоит выше эхо, "
            "и ты объяснил, почему;<br>✅ проверено на твоём боте."
        ),
        "stem": (
            "Прокачай этого Бот-Эхо: на слово «привет» в любом написании (привет, Привет, "
            "ПРИВЕТ) он должен отвечать своей особой фразой, а все остальные сообщения — "
            "по-прежнему повторять. Пришли код обработчика, который ты добавил, и напиши одним "
            "предложением, куда в файле ты его поставил и почему именно туда."
        ),
        "hint": (
            "telebot проверяет обработчики по порядку сверху вниз, а обработчик с "
            "func=lambda message: True ловит вообще всё. Чтобы регистр не мешал, сравнивай "
            "message.text.lower()."
        ),
        "review_note": (
            "Отдельный обработчик на «привет» без учёта регистра стоит выше эхо; эхо остальных "
            "сообщений сохранено; ученик объясняет порядок."
        ),
        "criteria": _criteria(
            must=[
                "Есть проверка на слово «привет», не зависящая от регистра: message.text.lower() == "
                "'привет' или равноценный способ (in, strip, список написаний)",
                "Если сделан отдельный обработчик — он стоит ВЫШЕ эхо-обработчика с "
                "func=lambda message: True, и ученик объясняет, что иначе эхо перехватит сообщение",
                "Эхо-обработчик сохранён: остальные сообщения по-прежнему повторяются",
            ],
            accept=[
                "Любая своя фраза-ответ на «привет»",
                "Решение через if внутри эхо-обработчика (если текст — «привет», особый ответ, "
                "иначе эхо) — засчитывать, требования выполнены; объяснение места тогда про if",
                "Обработчик /start изменён или убран — он не предмет этого задания",
            ],
            reject=[
                "Сравнение только с одним написанием, без lower() — «ПРИВЕТ» не поймает",
                "Отдельный обработчик «привет» стоит ниже эхо — он никогда не сработает",
                "Эхо убрано или сломано: остальные сообщения больше не повторяются",
            ],
            notes=(
                "Задание выделено из #5832 (там «прокачка» была третьим требованием и в текстовом "
                "ответе не проверялась). " + CRITERIA_NOTE
            ),
        ),
    },
    {
        "key": "6195",
        "task_id": 6195,
        "lesson": "3.1-sluchaynoe-chislo",
        "external_uid": "authored:chat-boty-tg-vk-max:sluchaynoe-chislo#q2",
        "block_index": 18,
        "title": None,
        "context": CODE_NAIVE_GUESS,
        "stem": (
            "Собери этого наивного бота (токен — через config.py) и поиграй: убедись, что выиграть "
            "нельзя. Ответь одним предложением: из-за какой строчки бот загадывает число заново на "
            "каждое сообщение? Процитируй строчку целиком и скажи, где она стоит."
        ),
        "hint": None,  # подсказка остаётся прежней
        "review_note": None,
        "criteria": _criteria(
            must=[
                "Названа строка zagadannoe = random.randint(1, 100) (или её смысл — загадывание "
                "числа) и сказано, что она стоит внутри обработчика guess, поэтому выполняется на "
                "каждое сообщение",
            ],
            accept=[
                "«Обработчик» может быть назван функцией guess, «под декоратором» или «внутри def»",
                "Скриншот игры не требуется — проверяется только объяснение",
            ],
            reject=[
                "Названа строка dogadka = int(message.text) или условие if — они число не загадывают",
                "Ответ «из-за random» без указания, что вызов стоит внутри обработчика",
            ],
            notes=CRITERIA_NOTE,
        ),
    },
    {
        "key": "6221",
        "task_id": 6221,
        "lesson": "9.2-eho-bot-v-max",
        "external_uid": "authored:chat-boty-tg-vk-max:max-eho-bot#q2",
        "block_index": 14,
        "title": None,
        "context": CODE_THREE_ECHOES,
        "stem": (
            "Запусти эхо-бота в Max (полный код — в уроке, токен в config.py) и проверь, что он "
            "повторяет сообщения. Выше — сердцевина трёх эхо-ботов курса: Telegram, ВКонтакте и "
            "Max. Напиши 2–3 предложениями, что во всех трёх осталось одинаковым, а что отличается "
            "только обёрткой. Назови хотя бы две общие вещи."
        ),
        "hint": None,
        "review_note": None,
        "criteria": _criteria(
            must=[
                "Названо общее: бот получает событие «пришло сообщение», берёт его текст и отвечает "
                "тем же текстом (событие → разбор → ответ), и во всех трёх есть запуск ожидания "
                "сообщений (polling / listen / start_polling)",
                "Названо хотя бы одно отличие как обёртка: декораторы против цикла for, "
                "async/await в Max, разные библиотеки",
            ],
            accept=[
                "«Токен-секрет лежит в config.py» — засчитывать как вторую общую вещь",
                "Скриншот работы бота в Max не требуется",
            ],
            reject=[
                "Ответ только «везде Python» или «везде токен» без сути «сообщение → ответ»",
                "Перечислены только отличия, общего нет",
            ],
            notes=CRITERIA_NOTE,
        ),
    },
    {
        "key": "6167",
        "task_id": 6167,
        "lesson": "1.1-stavim-telebot",
        "external_uid": "authored:chat-boty-tg-vk-max:stavim-telebot#q2",
        "block_index": 22,
        "title": None,
        "context": CODE_SKELETON,
        "stem": (
            "Поставь библиотеку командой pip install pytelegrambotapi, собери файл bot.py из этого "
            "скелета, вставь свой токен и запусти. Пришли скриншот, где программа работает и "
            "красных ошибок нет, и одной фразой напиши, что ты видишь в терминале после запуска."
        ),
        "hint": None,
        "review_note": None,
        "criteria": _criteria(
            must=[
                "Программа запущена и не падает: нет красной ошибки (Traceback), терминал ждёт — "
                "курсор не вернулся к приглашению командной строки",
            ],
            accept=[
                "Вместо скриншота — словесное описание «запустил, ошибок нет, ждёт» — засчитывать, "
                "это установочное задание",
                "Токен на скриншоте скрыт или обрезан — так и должно быть",
            ],
            reject=[
                "На скриншоте Traceback: ModuleNotFoundError (библиотека не поставлена) или "
                "Unauthorized (токен неверный)",
            ],
            notes=(
                "Если токен виден целиком — зачесть, но напомнить ученику правило модуля 0: токен "
                "не показывать. " + CRITERIA_NOTE
            ),
        ),
    },
    {
        "key": "5831",
        "task_id": 5831,
        "lesson": "1.3-bot-eho",
        "external_uid": "authored:chat-boty-tg-vk-max:bot-eho#q2",
        "block_index": 15,
        "title": None,
        "context": CODE_ECHO_BOT,
        "stem": (
            "Собери Бот-Эхо по этому коду (приветствие на /start оставь своё). Проверь, что /start "
            "здоровается, а остальные сообщения бот повторяет. Пришли короткое видео или два "
            "скриншота, где ты пишешь разные сообщения, а бот повторяет каждое."
        ),
        "hint": None,
        "review_note": None,
        "criteria": _criteria(
            must=[
                "Бот повторяет минимум два разных сообщения — видно на видео или скриншотах",
                "/start по-прежнему здоровается: команду не перехватил эхо-обработчик",
            ],
            accept=[
                "Своё приветствие вместо «Привет! Я повторяю за тобой.»",
                "Два скриншота вместо видео; словесное описание, если приложить негде",
            ],
            reject=[
                "На /start бот отвечает эхом «/start» — обработчик эхо стоит выше обработчика /start",
                "Повторяется только одно сообщение или ответа нет",
            ],
            notes=CRITERIA_NOTE,
        ),
    },
]


# ---------------------------------------------------------------------------
# Исходник CreateCourses
# ---------------------------------------------------------------------------


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _apply_to_block(block: dict[str, Any], spec: dict[str, Any]) -> None:
    block["context"] = spec["context"]
    block["context_lang"] = "python"
    block["stem"] = spec["stem"]
    if spec.get("ttype"):
        block["ttype"] = spec["ttype"]
    if spec.get("hint"):
        block["hint"] = spec["hint"]
    if spec.get("review_note"):
        block["review_note"] = spec["review_note"]


def _new_block(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "task",
        "ttype": spec.get("ttype", "SA"),
        "context": spec["context"],
        "context_lang": "python",
        "stem": spec["stem"],
        "hint": spec["hint"],
        "review_note": spec["review_note"],
    }


def _callout_block(html_text: str) -> dict[str, Any]:
    return {"type": "callout", "style": "tip", "html": html_text}


def _patch_callout_after(blocks: list[dict[str, Any]], idx: int, spec: dict[str, Any], where: str) -> None:
    """Выноска «Зачёт, если» сразу за блоком задания — переписать под новое условие."""
    nxt = blocks[idx + 1] if idx + 1 < len(blocks) else None
    if nxt and nxt.get("type") == "callout" and "Зачёт" in str(nxt.get("html") or ""):
        nxt["html"] = spec["callout"]
        logger.info("%s: выноска «Зачёт, если» за блоком %s переписана", where, idx)
    else:
        raise RuntimeError(f"{where}: за блоком {idx} нет выноски «Зачёт, если»")


def _find_graph_node(node: dict[str, Any], global_uid: str) -> dict[str, Any] | None:
    if node.get("global_uid") == global_uid:
        return node
    for sub in node.get("subcourses") or []:
        found = _find_graph_node(sub, global_uid)
        if found is not None:
            return found
    return None


def _patch_blocks(blocks: list[dict[str, Any]], spec: dict[str, Any], where: str) -> None:
    """Правит блок task в списке блоков урока (wp-blocks или узел графа)."""
    if spec["block_index"] is None:
        if any(b.get("type") == "task" and b.get("stem") == spec["stem"] for b in blocks):
            logger.info("%s: блок task уже есть (%s) — пропуск", where, spec["external_uid"])
            return
        # Новое задание встаёт ПОСЛЕ последнего интерактивного блока (чтобы не сдвинуть
        # нумерацию #qN у существующих) и ПЕРЕД навигацией урока (lms_skip).
        last_interactive = max(
            i for i, b in enumerate(blocks) if b.get("type") in ("task", "control_question", "quiz")
        )
        insert_at = last_interactive + 1
        while insert_at < len(blocks) and not blocks[insert_at].get("lms_skip"):
            insert_at += 1
        blocks.insert(insert_at, _new_block(spec))
        blocks.insert(insert_at + 1, _callout_block(spec["callout"]))
        logger.info("%s: добавлен блок task на позицию %s (%s)", where, insert_at, spec["external_uid"])
        return
    task_blocks = [i for i, b in enumerate(blocks) if b.get("type") == "task"]
    idx = spec["block_index"]
    if not (idx < len(blocks) and blocks[idx].get("type") == "task"):
        if len(task_blocks) != 1:
            raise RuntimeError(f"{where}: не нашёл единственный блок task для {spec['external_uid']}")
        idx = task_blocks[0]
    _apply_to_block(blocks[idx], spec)
    logger.info("%s: блок %s переписан (%s)", where, idx, spec["external_uid"])
    if spec.get("callout"):
        _patch_callout_after(blocks, idx, spec, where)


def apply_source() -> None:
    graph = _load(LMS_GRAPH)
    for spec in TASKS:
        lesson_path = WP_BLOCKS / f"{spec['lesson']}.json"
        lesson = _load(lesson_path)
        _patch_blocks(lesson["blocks"], spec, lesson_path.name)
        _dump(lesson_path, lesson)

        node = _find_graph_node(graph, lesson["global_uid"])
        if node is None:
            raise RuntimeError(f"в графе нет узла {lesson['global_uid']}")
        _patch_blocks(node["blocks"], spec, f"{LMS_GRAPH.name}:{lesson['global_uid']}")
    _dump(LMS_GRAPH, graph)


# ---------------------------------------------------------------------------
# LMS
# ---------------------------------------------------------------------------


def context_html(code: str, lang: str = "python") -> str:
    """Та же разметка, что у публикатора (`blocks_to_lms._code_context_html`)."""
    return (
        "<p>Исходный код для этого задания:</p>"
        f'<pre><code class="language-{html.escape(lang, quote=True)}">'
        f"{html.escape(code, quote=True)}</code></pre>"
    )


def _api(method: str, path: str, body: Any | None = None) -> Any:
    import urllib.error
    import urllib.request

    key = os.environ.get("LMS_API_KEY")
    if not key:
        raise RuntimeError("LMS_API_KEY не задан")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method=method,
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} → {e.code}: {detail[:2000]}") from e


def build_lms_payload(spec: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """PATCH-тело: условие с вшитым кодом + правило с критериями поверх текущего."""
    content = dict(current["task_content"])
    content["stem"] = context_html(spec["context"]) + spec["stem"]
    if spec.get("hint"):
        content["hints_text"] = [spec["hint"]]
        content["has_hints"] = True
    if spec.get("title"):
        content["title"] = spec["title"]
    if spec["key"] == "5832":
        # Ссылка на zip больше не нужна: код в условии. Файл остаётся в хранилище.
        content["has_attached_file"] = False
        content["attached_file_paths"] = []
    rules = dict(current["solution_rules"])
    rules["grading_criteria"] = spec["criteria"]
    return {"task_content": content, "solution_rules": rules}


def plan() -> None:
    for spec in TASKS:
        print(f"\n=== {spec['key']} {spec['external_uid']} ===")
        print(context_html(spec["context"])[:120], "…")
        print("STEM:", spec["stem"])
        if spec.get("hint"):
            print("HINT:", spec["hint"])
        print("MUST:", *spec["criteria"]["must"], sep="\n  - ")


def apply_lms() -> None:
    for spec in TASKS:
        task_id = spec["task_id"]
        if task_id is None:
            found = _api("POST", "/tasks/find-by-external", {"uids": [spec["external_uid"]]})
            existing = [t for t in (found.get("items") or found.get("tasks") or []) if t]
            if existing:
                task_id = existing[0]["id"]
                logger.info("%s уже есть: id=%s", spec["external_uid"], task_id)
            else:
                # Правило и содержимое — по образцу соседнего задания того же типа.
                template = _api("GET", "/tasks/5832")
                content = {
                    k: template["task_content"].get(k)
                    for k in ("code", "tags", "media", "prompt", "scales", "options", "course_uid",
                              "difficulty_code")
                }
                content.update({
                    "type": spec["ttype"], "title": spec["title"],
                    "stem": context_html(spec["context"]) + spec["stem"],
                    "hints_text": [spec["hint"]], "hints_video": [], "has_hints": True,
                    "has_attached_file": False, "attached_file_paths": [],
                })
                rules = dict(template["solution_rules"])
                rules.pop("grading_criteria", None)
                created = _api("POST", "/tasks/bulk-upsert", {"items": [{
                    "external_uid": spec["external_uid"], "course_id": spec["course_id"],
                    "difficulty_id": 3, "task_content": content, "solution_rules": rules,
                    "max_score": 1,
                }]})
                task_id = created["results"][0]["id"]
                logger.info("создано %s → id=%s", spec["external_uid"], task_id)
            spec["task_id"] = task_id
        current = _api("GET", f"/tasks/{task_id}")
        if current.get("external_uid") != spec["external_uid"]:
            raise RuntimeError(f"id={task_id}: external_uid {current.get('external_uid')!r} ≠ ожидаемого")
        body = build_lms_payload(spec, current)
        updated = _api("PATCH", f"/tasks/{task_id}", body)
        gc = (updated.get("solution_rules") or {}).get("grading_criteria") or {}
        prov = updated.get("content_provenance") or {}
        logger.info(
            "id=%s: stem %s симв., критерии status=%s must=%s, provenance=%s",
            task_id, len(updated["task_content"]["stem"]), gc.get("status"), len(gc.get("must") or []),
            prov.get("source"),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="store_true", help="править исходник CreateCourses")
    parser.add_argument("--apply", action="store_true", help="записать в боевой LMS через API")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if not args.source and not args.apply:
        plan()
        return 0
    if args.source:
        apply_source()
    if args.apply:
        apply_lms()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
