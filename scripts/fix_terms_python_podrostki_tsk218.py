"""tsk-218: ввод терминов ДО первого использования, курс Python-подростки (LMS prod).

Дефекты (naive+expert review, верифицированы по прод-контенту):
  1) «метод»/точечная запись — впервые точка в мат 961 (.count/.find/.replace) без имени понятия
  2) «[::-1] шаг» — мат 923 подаёт «особый срез» без связи со срезами 922 и понятием «шаг»
  3) «аргумент» — задание 5635 требует термин, а материал 1094 учил только «параметр»
  4) «break» — задание 5498 спрашивает, мат 998 (while) упоминает одной строкой без примера
  5) точка у random.randint — мат 966 не связывает точку с методами строк

Пункт «две роли in» СНЯТ: membership-in (x in список) в курсе не используется (проверено).

Каждая правка — якорная замена (old обязан быть в тексте РОВНО один раз).
Правится только LMS-прод (WP оставляем как есть — переезд в LMS, решение оператора).
Запуск: dry-run по умолчанию; --apply для записи (нужен DBCHECK_OK=1 из-за хука).
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

# --- Тексты вставок (голос курса: аналогия раньше термина, простой язык) ---

METOD_INTRO = (
    "<p>У строки есть не только буквы, но и встроенные умения. Представь швейцарский "
    "ножик: сам ножик — это строка, а лезвие, ножницы и штопор внутри — её умения. "
    "Такое умение строки в Python называют <strong>методом</strong>. Чтобы включить "
    "умение, пишут имя строки, <strong>точку</strong> и название метода со скобками — "
    "<code>fraza.count(&quot;а&quot;)</code>. Точку читай как «возьми у этой строки её "
    "умение».</p>\n"
)

BREAK_DEMO = (
    "<p>Иногда из цикла нужно выйти досрочно, не дожидаясь, пока условие станет "
    "неверным. Для этого есть команда <strong><code>break</code></strong> — она сразу "
    "прерывает цикл, и программа идёт дальше.</p>\n"
    "<pre><code class=\"language-python\" data-line=\"\">x = 0\n"
    "while x &lt; 100:\n"
    "    print(x)\n"
    "    x = x + 1\n"
    "    if x == 3:\n"
    "        break\n"
    "print(&quot;вышли по break&quot;)</code></pre>"
    "<pre class=\"cb-output\" style=\"background:#1e1e1e;color:#d4d4d4;padding:12px 16px;"
    "border-radius:8px;margin:6px 0 16px;overflow:auto\"><code style=\"background:"
    "transparent;color:#d4d4d4;font-family:Consolas,Menlo,monospace\">0\n1\n2\n"
    "вышли по break</code></pre>\n"
)

# (material_id, old, new) — old обязан встречаться ровно один раз
EDITS: list[tuple[int, str, str]] = [
    # 1) метод/точка — вставить intro ПЕРЕД абзацем «Индексы и срезы…» (мат 961)
    (
        961,
        "<p>Индексы и срезы — про то, ГДЕ символы.",
        METOD_INTRO + "<p>Индексы и срезы — про то, ГДЕ символы.",
    ),
    # 2) [::-1] шаг — заменить первый абзац мостиком к срезам (мат 923)
    (
        923,
        "<p>Особый срез <code>[::-1]</code> переворачивает строку задом наперёд. "
        "Получается простейшая шифровка: прочитать перевёрнутое слово сходу не так-то "
        "просто.</p>",
        "<p>Вспомни срезы из прошлого урока: <code>slovo[начало:конец]</code>. У среза "
        "есть и третье число — <strong>шаг</strong>: <code>slovo[начало:конец:шаг]</code>. "
        "Шаг <code>-1</code> означает «идти справа налево», поэтому <code>[::-1]</code> "
        "(начало и конец пустые — берём всю строку) читает её задом наперёд. Получается "
        "простейшая шифровка: прочитать перевёрнутое слово сходу не так-то просто.</p>",
    ),
    # 3) аргумент vs параметр — расширить первый абзац (мат 1094)
    (
        1094,
        "<p>Функции можно передавать данные — через параметры в скобках. А чтобы функция "
        "вернула результат (а не просто напечатала), используют <code>return</code>: он "
        "отдаёт значение обратно, и его можно сохранить в переменную или использовать "
        "дальше.</p>",
        "<p>Функции можно передавать данные — через <strong>параметры</strong> в скобках. "
        "Параметр — это имя-заготовка внутри функции (в примере ниже это <code>x</code>). "
        "А конкретное значение, которое подставляешь при вызове (например <code>3</code> в "
        "<code>kvadrat(3)</code>), называют <strong>аргументом</strong>. Как рецепт «положи "
        "X ложек сахара»: X — это параметр (пустое место с именем), а когда готовишь и "
        "кладёшь 3 ложки, тройка — это аргумент. А чтобы функция вернула результат (а не "
        "просто напечатала), используют <code>return</code>: он отдаёт значение обратно, и "
        "его можно сохранить в переменную или использовать дальше.</p>",
    ),
    # 4) break — демонстрация ПЕРЕД финальным callout (мат 998)
    (
        998,
        "<div class=\"cb-callout cb-callout-important\" style=\"margin:16px 0;padding:10px "
        "14px;border-radius:6px;border-left:4px solid #e0a800;background:#fff8e6;color:"
        "#16222e\">Внутри <code>while</code>",
        BREAK_DEMO
        + "<div class=\"cb-callout cb-callout-important\" style=\"margin:16px 0;padding:10px "
        "14px;border-radius:6px;border-left:4px solid #e0a800;background:#fff8e6;color:"
        "#16222e\">Внутри <code>while</code>",
    ),
    # 5) точка у random.randint — мостик к методам строк (мат 966)
    (
        966,
        "возвращает случайное целое число от <code>a</code> до <code>b</code> — причём оба "
        "края включаются.</p>",
        "возвращает случайное целое число от <code>a</code> до <code>b</code> — причём оба "
        "края включаются. Точка в <code>random.randint</code> работает как точка у "
        "строковых методов (вспомни <code>slovo.upper()</code> из раздела про строки): "
        "«возьми из модуля random его инструмент randint».</p>",
    ),
]


def _dsn() -> str:
    load_dotenv(".env", encoding="utf-8-sig", override=False)
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            for mat_id, old, new in EDITS:
                text = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", mat_id
                )
                if text is None:
                    raise RuntimeError(f"мат {mat_id}: не найден или пустой content.text")
                cnt = text.count(old)
                if cnt != 1:
                    raise RuntimeError(
                        f"мат {mat_id}: якорь встречается {cnt} раз (нужно 1) — "
                        f"правка небезопасна. Якорь: {old[:60]!r}"
                    )
                new_text = text.replace(old, new)
                await conn.execute(
                    "UPDATE materials "
                    "SET content = jsonb_set(content, '{text}', to_jsonb($2::text)) "
                    "WHERE id=$1",
                    mat_id, new_text,
                )
                # verify внутри транзакции
                check = await conn.fetchval(
                    "SELECT content->>'text' FROM materials WHERE id=$1", mat_id
                )
                anchor = new.split(old)[0] or new[:40]
                assert anchor in check, f"мат {mat_id}: вставка не подтвердилась"
                print(f"OK мат {mat_id}: +{len(new) - len(old)} симв., вставка на месте")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply для записи)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО (5 материалов).")
    finally:
        await conn.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    try:
        asyncio.run(main(apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
