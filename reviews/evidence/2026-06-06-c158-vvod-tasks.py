# -*- coding: utf-8 -*-
"""Курс 158 «Задание 7 ЕГЭ — Кодирование и передача информации» — вводная серия.

30 авто-проверяемых заданий (SC/SA_COM) + гашение материала 437 «Контрольные вопросы».
Существующие 86 заданий сдвигаются +30. Итого: 116.

Серия: tsk-109 итерация 6 / external_uid prefix: lms:c158:vvod
Разделы: единицы (1-6), текст (7-11), изображения (12-16),
         звук (17-20), сжатие (21-23), передача (24-27), хранение (28-30)
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 158
MATERIAL_ID = 437
COURSE_UID  = "wp:zadanie-7-ege-kodirovanie-razlichnyh-vidov-informatsii-peredacha-informatsii"
N           = 30

DIFF_THEORY = 1
DIFF_EASY   = 2
DIFF_NORMAL = 3


# ── helpers ───────────────────────────────────────────────────────────────────

def load_dsn() -> str:
    dsn = os.environ.get("LMS_DB_DSN")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    url = None
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL"):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not url:
        raise RuntimeError("DATABASE_URL не найден в .env")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


def opt(oid: str, text: str) -> dict:
    return {"id": oid, "text": text, "is_active": True, "explanation": ""}


def sc(stem: str, options: list, correct: list):
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})


def sa(stem: str, value: str, extras: list = None):
    accepted = [{"score": 1, "value": value}]
    for v in (extras or []):
        accepted.append({"score": 1, "value": v})
    short = {
        "regex": None,
        "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": accepted,
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── серия заданий ─────────────────────────────────────────────────────────────

SERIES = [

    # ── Раздел 1: Единицы измерения (1–6) ────────────────────────────────────

    # 1 — 1 Кбайт в битах (EASY): 1024 × 8 = 8192
    (DIFF_EASY, sa(
        "<p>Сколько бит содержится в 1&nbsp;Кбайте?</p>"
        "<p>Введи число.</p>",
        "8192",
    )),

    # 2 — 2 МБ в битах (EASY): 2 × 1024 × 1024 × 8 = 16 777 216
    (DIFF_EASY, sa(
        "<p>Переведите 2&nbsp;Мбайта в биты.</p>"
        "<p>Введи число.</p>",
        "16777216",
    )),

    # 3 — ГБ / МБ (EASY): 1024
    (DIFF_EASY, sa(
        "<p>Во сколько раз 1&nbsp;Гбайт больше 1&nbsp;Мбайта?</p>"
        "<p>Введи число.</p>",
        "1024",
    )),

    # 4 — 256 КБ в Кбит (EASY): 256 × 8 = 2048
    (DIFF_EASY, sa(
        "<p>Выразите 256&nbsp;Кбайт в килобитах.</p>"
        "<p>Введи число.</p>",
        "2048",
    )),

    # 5 — 4096 байт в КБ (EASY): 4096 / 1024 = 4
    (DIFF_EASY, sa(
        "<p>Текст занимает 4096&nbsp;байт. Сколько это Кбайт?</p>"
        "<p>Введи число.</p>",
        "4",
    )),

    # 6 — Зачем согласовывать единицы? (THEORY)
    (DIFF_THEORY, sc(
        "<p>Почему при вычислениях важно проверять согласованность единиц измерения "
        "(например, переводить всё в Мбиты или байты)?</p>",
        [opt("A", "Только чтобы запись выглядела аккуратнее"),
         opt("B", "Чтобы не ошибиться в расчётах — смешение единиц даёт неверный результат"),
         opt("C", "Потому что компьютер не умеет работать со смешанными единицами"),
         opt("D", "Чтобы ускорить передачу данных по каналу")],
        ["B"],
    )),

    # ── Раздел 2: Кодирование текста (7–11) ──────────────────────────────────

    # 7 — количество символов при 7 бит (EASY): 2^7 = 128
    (DIFF_EASY, sa(
        "<p>Какое максимальное количество различных символов можно закодировать "
        "при разрядности 7&nbsp;бит?</p>"
        "<p>Введи число.</p>",
        "128",
    )),

    # 8 — 1200 символов × 8 бит = 1200 байт (EASY)
    (DIFF_EASY, sa(
        "<p>Текст содержит 1200&nbsp;символов, разрядность кодирования — 8&nbsp;бит. "
        "Сколько <strong>байт</strong> памяти потребуется?</p>"
        "<p>Введи число.</p>",
        "1200",
    )),

    # 9 — 40 000 символов × 16 бит = 80 000 байт (EASY)
    (DIFF_EASY, sa(
        "<p>Для кодирования текста из 40&nbsp;000 символов использовалась разрядность "
        "16&nbsp;бит. Сколько <strong>байт</strong> памяти потребуется?</p>"
        "<p>Введи число.</p>",
        "80000",
    )),

    # 10 — 8→16 бит: объём ×2 (EASY)
    (DIFF_EASY, sc(
        "<p>Текст из 10&nbsp;000 символов кодировался с разрядностью 8&nbsp;бит. "
        "Разрядность увеличили до 16&nbsp;бит. Как изменился объём памяти?</p>",
        [opt("A", "Уменьшился в 2 раза"),
         opt("B", "Увеличился в 2 раза"),
         opt("C", "Увеличился в 4 раза"),
         opt("D", "Не изменился — символов столько же")],
        ["B"],
    )),

    # 11 — i в формуле I=N·i (THEORY)
    (DIFF_THEORY, sc(
        "<p>В формуле <strong>I&nbsp;=&nbsp;N&nbsp;·&nbsp;i</strong> "
        "переменная <em>i</em> называется «разрядностью кодирования». "
        "Что она означает?</p>",
        [opt("A", "Количество символов в тексте"),
         opt("B", "Количество бит, отводимых для кодирования одного символа"),
         opt("C", "Порядковый номер символа в алфавите"),
         opt("D", "Суммарный объём файла в байтах")],
        ["B"],
    )),

    # ── Раздел 3: Кодирование изображений (12–16) ────────────────────────────

    # 12 — 640×480, 8 бит = 307 200 байт (EASY)
    (DIFF_EASY, sa(
        "<p>Изображение имеет размер 640&times;480&nbsp;пикселей и глубину цвета "
        "8&nbsp;бит. Сколько <strong>байт</strong> памяти требуется?</p>"
        "<p>Введи число.</p>",
        "307200",
    )),

    # 13 — глубина 8→24: объём ×3 (EASY)
    (DIFF_EASY, sa(
        "<p>Глубина цвета изображения увеличилась с 8 до 24&nbsp;бит "
        "(размеры в пикселях не изменились). Во сколько раз увеличился объём файла?</p>"
        "<p>Введи число.</p>",
        "3",
    )),

    # 14 — 300×200, 16 бит = 120 000 байт (EASY)
    (DIFF_EASY, sa(
        "<p>Изображение 300&times;200&nbsp;пикселей хранится с глубиной цвета "
        "16&nbsp;бит. Вычислите объём в <strong>байтах</strong>.</p>"
        "<p>Введи число.</p>",
        "120000",
    )),

    # 15 — проверка расчёта: 1024×768×24÷8 = 2 359 296 байт (NORMAL)
    (DIFF_NORMAL, sc(
        "<p>Изображение 1024&times;768&nbsp;пикселей с глубиной цвета 24&nbsp;бит "
        "занимает 2&nbsp;359&nbsp;296&nbsp;байт. Соответствует ли это расчётам "
        "по формуле <code>I = ширина × высота × глубина / 8</code>?</p>",
        [opt("A", "Да — 1024 × 768 × 24 ÷ 8 = 2 359 296 байт"),
         opt("B", "Нет, должно быть больше — глубина не делится на 8"),
         opt("C", "Нет, должно быть меньше — нужно делить на 1024"),
         opt("D", "Нет, формула применяется только для глубины 8 бит")],
        ["A"],
    )),

    # 16 — глубина цвета (THEORY)
    (DIFF_THEORY, sc(
        "<p>Что означает термин <strong>«глубина цвета»</strong> "
        "для растрового изображения?</p>",
        [opt("A", "Физический размер экрана в дюймах"),
         opt("B", "Количество бит, выделяемых для кодирования цвета одного пикселя"),
         opt("C", "Количество пикселей по горизонтали"),
         opt("D", "Число цветов, различимых человеческим глазом")],
        ["B"],
    )),

    # ── Раздел 4: Кодирование звука (17–20) ──────────────────────────────────

    # 17 — частота дискретизации (THEORY)
    (DIFF_THEORY, sc(
        "<p>Выберите верное определение: <strong>«частота дискретизации»</strong> "
        "при кодировании звука — это…</p>",
        [opt("A", "Количество бит, выделяемых на один замер звука"),
         opt("B", "Количество замеров (отсчётов) звукового сигнала в секунду"),
         opt("C", "Длительность одного замера в миллисекундах"),
         opt("D", "Скорость передачи звукового файла по каналу связи")],
        ["B"],
    )),

    # 18 — 44 100 × 16 × 2 / 8 = 176 400 байт/с (EASY)
    (DIFF_EASY, sa(
        "<p>Звук записывается с частотой дискретизации 44&nbsp;100&nbsp;Гц, "
        "глубиной 16&nbsp;бит, в стерео (2&nbsp;канала). "
        "Вычислите объём данных за 1&nbsp;секунду в <strong>байтах</strong>.</p>"
        "<p>Введи число.</p>",
        "176400",
    )),

    # 19 — частота ×2 → объём ×2 (EASY)
    (DIFF_EASY, sc(
        "<p>Звук записывается с частотой дискретизации 22&nbsp;кГц. "
        "Все остальные параметры (глубина кодирования, число каналов, длительность) "
        "не изменились. Как изменится объём файла, если частоту увеличить до 44&nbsp;кГц?</p>",
        [opt("A", "Не изменится — объём зависит только от глубины"),
         opt("B", "Увеличится в 2 раза"),
         opt("C", "Уменьшится в 2 раза"),
         opt("D", "Увеличится в 4 раза")],
        ["B"],
    )),

    # 20 — 22 000 × 8 × 1 × 120 / 8 = 2 640 000 байт (NORMAL)
    (DIFF_NORMAL, sa(
        "<p>Звук длительностью 2&nbsp;минуты записан с параметрами: "
        "частота 22&nbsp;кГц, глубина 8&nbsp;бит, моно (1&nbsp;канал). "
        "Определите объём памяти в <strong>байтах</strong>.</p>"
        "<p>Введи число.</p>",
        "2640000",
    )),

    # ── Раздел 5: Сжатие данных (21–23) ──────────────────────────────────────

    # 21 — зачем сжатие (THEORY)
    (DIFF_THEORY, sc(
        "<p>Для чего применяются методы сжатия данных?</p>",
        [opt("A", "Чтобы ускорить работу процессора"),
         opt("B", "Чтобы уменьшить объём файлов — экономия места и времени передачи"),
         opt("C", "Чтобы преобразовать файл в другой формат (например, JPG → PNG)"),
         opt("D", "Чтобы защитить файл паролем")],
        ["B"],
    )),

    # 22 — 20 МБ → 5 МБ: в 4 раза (EASY)
    (DIFF_EASY, sa(
        "<p>При сжатии файл уменьшился с 20&nbsp;Мбайт до 5&nbsp;Мбайт. "
        "Во сколько раз уменьшился объём?</p>"
        "<p>Введи число.</p>",
        "4",
    )),

    # 23 — почему распаковка = исходный объём (THEORY)
    (DIFF_THEORY, sc(
        "<p>Почему после распаковки сжатого файла его объём снова равен исходному?</p>",
        [opt("A", "При сжатии часть данных дублируется, при распаковке — дубликаты удаляются"),
         opt("B", "Алгоритм сжатия сохраняет все исходные данные, только в более компактном виде"),
         opt("C", "Программа распаковки генерирует недостающие данные"),
         opt("D", "Файл возвращается к первоначальному физическому формату на диске")],
        ["B"],
    )),

    # ── Раздел 6: Передача данных (24–27) ────────────────────────────────────

    # 24 — 16 МБ = 128 Мбит / 2 Мбит/с = 64 с (NORMAL)
    (DIFF_NORMAL, sa(
        "<p>Канал связи имеет пропускную способность 2&nbsp;Мбит/с. "
        "Сколько секунд потребуется для передачи файла размером 16&nbsp;Мбайт?</p>"
        "<p>Введи число.</p>",
        "64",
    )),

    # 25 — 100 МБ / 1 МБ/с = 100 с (EASY)
    (DIFF_EASY, sa(
        "<p>По каналу со скоростью 1&nbsp;Мбайт/с передают файл размером "
        "100&nbsp;Мбайт. Сколько секунд займёт передача?</p>"
        "<p>Введи число.</p>",
        "100",
    )),

    # 26 — 30×0,6=18 МБ; 18/1=18 с; 10+18+2=30 с (NORMAL)
    (DIFF_NORMAL, sa(
        "<p>Файл размером 30&nbsp;Мбайт был сжат на 40%. "
        "Время упаковки — 10&nbsp;с, распаковки — 2&nbsp;с. "
        "Пропускная способность канала — 1&nbsp;Мбайт/с.</p>"
        "<p>Сколько секунд потребуется суммарно: "
        "упаковка&nbsp;+ передача&nbsp;+ распаковка?</p>"
        "<p>Введи число.</p>",
        "30",
    )),

    # 27 — аналогия пропускной способности (THEORY)
    (DIFF_THEORY, sc(
        "<p>Какой пример из жизни лучше всего иллюстрирует понятие "
        "<strong>«пропускная способность канала»</strong>?</p>",
        [opt("A", "Труба с водой: диаметр трубы — пропускная способность, объём воды — объём данных"),
         opt("B", "Лампочка: мощность лампочки — это скорость передачи одного бита"),
         opt("C", "Весы: масса груза — это объём файла, а точность — пропускная способность"),
         opt("D", "Термометр: температура — это скорость канала, шкала — размер файла")],
        ["A"],
    )),

    # ── Раздел 7: Хранение данных (28–30) ────────────────────────────────────

    # 28 — 5000 × 16 / 8 = 10 000 байт (EASY)
    (DIFF_EASY, sa(
        "<p>Документ содержит 5000&nbsp;символов, разрядность кодирования — 16&nbsp;бит. "
        "Сколько <strong>байт</strong> нужно для хранения?</p>"
        "<p>Введи число.</p>",
        "10000",
    )),

    # 29 — разрешение ×2 по обоим изм. → пикселей ×4 → объём ×4 (NORMAL)
    (DIFF_NORMAL, sc(
        "<p>Документ сканируется с разрешением 300&nbsp;dpi и глубиной цвета 24&nbsp;бит. "
        "Разрешение увеличивают вдвое: до 600&nbsp;dpi (по обоим измерениям). "
        "Во сколько раз увеличится объём файла?</p>",
        [opt("A", "В 2 раза — разрешение стало вдвое выше"),
         opt("B", "В 4 раза — по двум измерениям: 2 × 2 = в 4 раза больше пикселей"),
         opt("C", "В 8 раз — учитывается и глубина цвета"),
         opt("D", "Не изменится — глубина цвета та же")],
        ["B"],
    )),

    # 30 — почему разные формулы (THEORY)
    (DIFF_THEORY, sc(
        "<p>Почему для расчёта объёма текста, изображения и звука используются "
        "разные формулы?</p>",
        [opt("A", "Разные типы файлов хранятся на разных носителях"),
         opt("B", "У каждого типа информации своя единица кодирования: символ, пиксель, отсчёт"),
         opt("C", "Разные файловые форматы имеют разный размер заголовка"),
         opt("D", "Так требует стандарт кодирования UTF-8")],
        ["B"],
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, а в SERIES = {len(SERIES)}"


# ── сборка строк ──────────────────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, (ttype, stem, options, extra)) in enumerate(SERIES, start=1):
        ext_uid = f"lms:c158:vvod:{i:02d}"
        task_content = {
            "code": None, "stem": stem, "tags": None,
            "type": ttype, "media": None, "title": None, "prompt": None,
            "options": options, "has_hints": False,
            "course_uid": COURSE_UID,
            "hints_text": [], "hints_video": [], "difficulty_code": None,
        }
        solution_rules = {
            "max_score": 1,
            "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
            "auto_check": True, "text_answer": None,
            "scoring_mode": "all_or_nothing",
            "short_answer": extra["short_answer"],
            "partial_rules": [], "correct_options": extra["correct_options"],
            "custom_scoring_config": None, "manual_review_required": False,
        }
        rows.append((ext_uid, diff_id, i, task_content, solution_rules))
    return rows


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    apply = "--apply" in sys.argv

    conn = psycopg2.connect(load_dsn())
    conn.autocommit = False
    cur = conn.cursor()

    try:
        cur.execute("SET LOCAL app.skip_task_order_trigger = 'true'")

        # ── снимок до ────────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        before_cnt = cur.fetchone()[0]
        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_before = cur.fetchone()[0]
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")
        print(f"материал {MATERIAL_ID} is_active до: {mat_before}")

        # ── сдвиг +N ─────────────────────────────────────────────────────────
        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s WHERE course_id=%s",
            (N, COURSE_ID),
        )
        shifted = cur.rowcount
        print(f"сдвинуто заданий на +{N}: {shifted}")

        # ── вставка 30 заданий ────────────────────────────────────────────────
        rows = build_rows()
        for ext_uid, diff_id, pos, tc, sr in rows:
            cur.execute(
                "INSERT INTO tasks "
                "(external_uid, max_score, task_content, course_id, difficulty_id, "
                "solution_rules, max_attempts, time_limit_sec, order_position) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ext_uid, 1, Json(tc), COURSE_ID, diff_id, Json(sr),
                 None, None, pos),
            )

        # ── гашение материала ────────────────────────────────────────────────
        cur.execute("UPDATE materials SET is_active=false WHERE id=%s", (MATERIAL_ID,))
        mat_upd = cur.rowcount

        # ── самопроверка ──────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        after_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT min(order_position), max(order_position), count(*), "
            "count(DISTINCT order_position) FROM tasks WHERE course_id=%s",
            (COURSE_ID,),
        )
        pmin, pmax, pcnt, pdistinct = cur.fetchone()

        cur.execute(
            "SELECT count(*) FROM tasks "
            "WHERE course_id=%s AND external_uid LIKE 'lms:c158:vvod:%%'",
            (COURSE_ID,),
        )
        series_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT order_position, task_content->>'type', "
            "left(task_content->>'stem', 60) "
            "FROM tasks WHERE external_uid LIKE 'lms:c158:vvod:%%' "
            "ORDER BY order_position",
        )
        preview = cur.fetchall()

        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_after = cur.fetchone()[0]

        print(f"\nзаданий после: {after_cnt}  (было {before_cnt})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after} (строк {mat_upd})")
        print("--- серия (pos / тип / начало stem) ---")
        for pos, typ, stem_head in preview:
            print(f"  {pos:>2}  {typ:<7}  {stem_head!r}")

        checks = {
            f"было {before_cnt} заданий":           before_cnt > 0,
            f"вставлено ровно {N}":                  series_cnt == N,
            f"итог = было + {N}":                    after_cnt == before_cnt + N,
            "позиции непрерывны 1..count":           (pmin == 1 and pmax == pcnt
                                                      and pdistinct == pcnt),
            f"серия на позициях 1..{N}":             [p[0] for p in preview] == list(range(1, N + 1)),
            f"сдвинуты все старые ({before_cnt})":  shifted == before_cnt,
            "материал погашен":                      mat_after is False and mat_upd == 1,
        }

        print("\n--- проверки ---")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")

        if all(checks.values()) and apply:
            conn.commit()
            print("\nРЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
        elif all(checks.values()):
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: DRY-RUN пройден, ROLLBACK. Запусти с --apply для записи.")
        else:
            conn.rollback()
            print("\nРЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
            sys.exit(1)

    except Exception as exc:
        conn.rollback()
        print(f"ОШИБКА: {exc!r}. ROLLBACK.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
