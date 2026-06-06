# -*- coding: utf-8 -*-
"""Курс 139 «Задание 13 ЕГЭ. IP-адресация» — серия заданий.

27 авто-проверяемых заданий (SC/MC/SA_COM) + гашение материала 356 «Контрольные вопросы».
Существующие 74 задания сдвигаются +27. Итого: 101.

Серия: tsk-109 итерация 9 / external_uid prefix: lms:c139:vvod
Блок 1: Теория (01-08)   — SC/MC/SA_COM, THEORY/EASY
Блок 2: На бумаге (09-18) — SA_COM/SC, EASY/NORMAL
Блок 3: Python (19-27)    — SA_COM/SC, EASY/NORMAL
"""
import io, os, re, sys
import psycopg2
from psycopg2.extras import Json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

COURSE_ID   = 139
COURSE_UID  = "wp:reshenie-zadanij-13-ege-organizatsiya-kompyuternyh-setej-i-adresatsiya"
MATERIAL_ID = 356
N           = 27

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
    """Single-choice задание."""
    return ("SC", stem, options, {"correct_options": correct, "short_answer": None})


def mc(stem: str, options: list, correct: list):
    """Multiple-choice задание."""
    return ("MC", stem, options, {"correct_options": correct, "short_answer": None})


def sa(stem: str, value: str, extras: list = None):
    """SA_COM с авто-чеком по списку ответов."""
    accepted = [{"score": 1, "value": value}]
    for v in (extras or []):
        accepted.append({"score": 1, "value": v})
    short = {
        "regex": None, "use_regex": False,
        "normalization": ["trim", "lower"],
        "accepted_answers": accepted,
    }
    return ("SA_COM", stem, None, {"correct_options": [], "short_answer": short})


# ── содержание серии ──────────────────────────────────────────────────────────
# Структура: (difficulty_id, hints_video, task_tuple)

SERIES = [

    # ── Блок 1: Теория (01–08) ────────────────────────────────────────────────

    # 01 — IPv4 = 32 бита (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>IPv4-адрес состоит из:</p>",
        [opt("A", "8 бит (1 байт)"),
         opt("B", "16 бит (2 байта)"),
         opt("C", "32 бита (4 байта)"),
         opt("D", "64 бита (8 байт)")],
        ["C"],
    )),

    # 02 — назначение маски сети (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>Маска сети применяется для:</p>",
        [opt("A", "Шифрования передаваемых данных"),
         opt("B", "Определения адреса шлюза по умолчанию"),
         opt("C", "Разделения IP-адреса на сетевую часть и часть узла"),
         opt("D", "Автоматического назначения IP-адресов устройствам")],
        ["C"],
    )),

    # 03 — что означает /24 (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>Запись <code>192.168.1.0/24</code> означает, что:</p>",
        [opt("A", "Сеть рассчитана ровно на 24 устройства"),
         opt("B", "Первые 24 бита IP-адреса — сетевая часть"),
         opt("C", "Последние 24 бита IP-адреса — сетевая часть"),
         opt("D", "Адрес принадлежит диапазону класса A")],
        ["B"],
    )),

    # 04 — 255.255.255.0 = /24 = 24 бита (EASY)
    (DIFF_EASY, [], sa(
        "<p>Маска <code>255.255.255.0</code> в двоичной записи содержит ровно N единиц подряд, "
        "и это число совпадает с длиной префикса <code>/N</code>.</p>"
        "<p>Сколько бит занимает сетевая часть в маске <code>255.255.255.0</code>?</p>"
        "<p>Введите число.</p>",
        "24",
    )),

    # 05 — адрес сети и broadcast (THEORY, MC)
    (DIFF_THEORY, [], mc(
        "<p>Выберите все верные утверждения об адресе сети и broadcast-адресе:</p>",
        [opt("A", "Адрес сети — первый адрес диапазона, все биты хост-части равны 0"),
         opt("B", "Broadcast-адрес — последний адрес диапазона, все биты хост-части равны 1"),
         opt("C", "Оба адреса можно назначать узлам как обычные IP-адреса"),
         opt("D", "Адрес сети вычисляется побитовой конъюнкцией IP-адреса узла и маски")],
        ["A", "B", "D"],
    )),

    # 06 — что такое хост-адреса (THEORY)
    (DIFF_THEORY, [], sc(
        "<p>Хост-адреса в сети — это:</p>",
        [opt("A", "Все IP-адреса диапазона, включая адрес сети и broadcast"),
         opt("B", "Только адрес шлюза по умолчанию"),
         opt("C", "Адреса, доступные для назначения узлам: весь диапазон кроме адреса сети и broadcast"),
         opt("D", "Адреса маршрутизаторов внутри сети")],
        ["C"],
    )),

    # 07 — хостов в /30: 2^2 - 2 = 2 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Сколько хост-адресов (доступных для назначения узлам) в сети "
        "с префиксом <code>/30</code>?</p>"
        "<p>Введите число.</p>",
        "2",
    )),

    # 08 — strict=False зачем: хост-биты ≠ 0 (EASY)
    (DIFF_EASY, [], sc(
        "<p>При выполнении <code>IPv4Network('192.168.1.45/24')</code> без "
        "<code>strict=False</code> Python выбросит <code>ValueError</code>. Почему?</p>",
        [opt("A", "192.168.1.45 — недопустимый IP-адрес"),
         opt("B", "192.168.1.45 — не сетевой адрес: биты хост-части не равны нулю"),
         opt("C", "Маска /24 недопустима для данного диапазона адресов"),
         opt("D", "Модуль ipaddress не принимает адреса в формате x.x.x.x/prefix")],
        ["B"],
    )),

    # ── Блок 2: На бумаге (09–18) ────────────────────────────────────────────

    # 09 — 10.0.0.0/8: 2^24 = 16 777 216 (EASY, разбор 480)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239480"], sa(
        "<p>Дана сеть <code>10.0.0.0/8</code>. Сколько всего IP-адресов в ней?</p>"
        "<p>Введите число.</p>",
        "16777216",
    )),

    # 10 — 192.168.10.45/24: broadcast = 192.168.10.255 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Адрес <code>192.168.10.45/24</code>. Найдите broadcast-адрес сети.</p>"
        "<p>Введите адрес.</p>",
        "192.168.10.255",
    )),

    # 11 — 172.16.5.200/255.255.252.0 (/22): 2^10 = 1024 (NORMAL, разбор 481)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239481"], sa(
        "<p>Адрес <code>172.16.5.200/255.255.252.0</code>. "
        "Сколько всего IP-адресов содержится в этой сети?</p>"
        "<p>Введите число.</p>",
        "1024",
    )),

    # 12 — 192.168.1.130/25: сеть = 192.168.1.128 (EASY)
    # 130 = 10000010, AND с /25-маской 10000000 → 10000000 = 128
    (DIFF_EASY, [], sa(
        "<p>Каков сетевой адрес для <code>192.168.1.130/25</code>?</p>"
        "<p>Введите адрес.</p>",
        "192.168.1.128",
    )),

    # 13 — хостов в /29: 2^3 - 2 = 6 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Сколько хост-адресов (доступных для узлов) в сети "
        "с префиксом <code>/29</code>?</p>"
        "<p>Введите число.</p>",
        "6",
    )),

    # 14 — 192.168.2.100 in 192.168.2.0/26? /26 → .0-.63, 100>63 → НЕТ (EASY)
    (DIFF_EASY, [], sc(
        "<p>Входит ли адрес <code>192.168.2.100</code> "
        "в сеть <code>192.168.2.0/26</code>?</p>",
        [opt("A", "Да"),
         opt("B", "Нет")],
        ["B"],
    )),

    # 15 — 10.1.5.1 in 10.0.0.0/15? /15 → 10.0.0.0-10.1.255.255 → ДА (NORMAL)
    (DIFF_NORMAL, [], sc(
        "<p>Входит ли адрес <code>10.1.5.1</code> "
        "в сеть <code>10.0.0.0/15</code>?</p>",
        [opt("A", "Да"),
         opt("B", "Нет")],
        ["A"],
    )),

    # 16 — broadcast 192.168.1.0/30 = 192.168.1.3 (EASY)
    (DIFF_EASY, [], sa(
        "<p>Дана сеть <code>192.168.1.0/30</code>. Каков её broadcast-адрес?</p>"
        "<p>Введите адрес.</p>",
        "192.168.1.3",
    )),

    # 17 — /24 vs /25: 256/128 = в 2 раза (EASY)
    (DIFF_EASY, [], sc(
        "<p>Сравните сети <code>192.168.1.0/24</code> и <code>192.168.1.0/25</code>. "
        "Во сколько раз /24 больше /25 по числу адресов?</p>",
        [opt("A", "В 4 раза"),
         opt("B", "В 2 раза"),
         opt("C", "В 8 раз"),
         opt("D", "В 16 раз")],
        ["B"],
    )),

    # 18 — broadcast 100.64.0.0/10 = 100.127.255.255 (NORMAL, Carrier-grade NAT)
    (DIFF_NORMAL, [], sa(
        "<p>Запишите broadcast-адрес для сети <code>100.64.0.0/10</code> "
        "(диапазон Carrier-grade NAT).</p>"
        "<p>Введите адрес.</p>",
        "100.127.255.255",
    )),

    # ── Блок 3: Python ipaddress (19–27) ─────────────────────────────────────

    # 19 — netmask 192.168.0.0/24 = 255.255.255.0 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Создайте объект сети: <code>network = IPv4Network('192.168.0.0/24')</code>.</p>"
        "<p>Что выведет <code>print(network.netmask)</code>?</p>"
        "<p>Введите результат.</p>",
        "255.255.255.0",
    )),

    # 20 — strict=False с 192.168.1.45/24 → network_address = 192.168.1.0 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Создайте сеть: "
        "<code>network = IPv4Network('192.168.1.45/24', strict=False)</code>.</p>"
        "<p>Что вернёт <code>network.network_address</code>?</p>"
        "<p>Введите адрес.</p>",
        "192.168.1.0",
    )),

    # 21 — num_addresses 10.0.0.0/16 = 65536 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Создайте объект <code>IPv4Network('10.0.0.0/16')</code>.</p>"
        "<p>Что вернёт <code>network.num_addresses</code>?</p>"
        "<p>Введите число.</p>",
        "65536",
    )),

    # 22 — 2-й хост 192.168.1.0/30 = 192.168.1.2 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Переберите все хосты сети <code>192.168.1.0/30</code> "
        "через <code>network.hosts()</code>.</p>"
        "<p>Введите второй адрес в списке (индекс 1).</p>",
        "192.168.1.2",
    )),

    # 23 — 192.168.1.100 in 192.168.1.0/25 → True (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sc(
        "<p>Выполните в Python:</p>"
        "<pre>ip_address('192.168.1.100') in IPv4Network('192.168.1.0/25')</pre>"
        "<p>Каков результат?</p>",
        [opt("A", "True"),
         opt("B", "False")],
        ["A"],
    )),

    # 24 — 8.8.8.8 in 8.8.8.0/24 → True (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sc(
        "<p>Выполните в Python:</p>"
        "<pre>ip_address('8.8.8.8') in IPv4Network('8.8.8.0/24')</pre>"
        "<p>Каков результат?</p>",
        [opt("A", "True"),
         opt("B", "False")],
        ["A"],
    )),

    # 25 — 5-й хост 172.16.0.0/24 = 172.16.0.5 (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Получите список первых 5 хостов сети <code>172.16.0.0/24</code> "
        "через <code>network.hosts()</code>.</p>"
        "<p>Введите пятый адрес (индекс 4).</p>",
        "172.16.0.5",
    )),

    # 26 — with_netmask 192.168.0.0/24 → "192.168.0.0/255.255.255.0" (EASY)
    (DIFF_EASY, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Создайте объект <code>IPv4Network('192.168.0.0/24')</code>.</p>"
        "<p>Что вернёт <code>network.with_netmask</code>?</p>"
        "<p>Введите строку.</p>",
        "192.168.0.0/255.255.255.0",
    )),

    # 27 — хостов для маски 255.255.254.0 (/23): 2^9 - 2 = 510 (NORMAL)
    (DIFF_NORMAL, ["https://vk.com/video-53400615_456239368"], sa(
        "<p>Напишите скрипт, который принимает IP-адрес и маску подсети от пользователя "
        "и выводит: сетевой адрес, broadcast-адрес и количество хост-адресов.</p>"
        "<p>Сколько хост-адресов выведет ваш скрипт для маски "
        "<code>255.255.254.0</code>?</p>"
        "<p>Введите число.</p>",
        "510",
    )),
]

assert len(SERIES) == N, f"Ожидается {N} заданий, а в SERIES = {len(SERIES)}"


# ── сборка строк для вставки ──────────────────────────────────────────────────

def build_rows() -> list:
    rows = []
    for i, (diff_id, hints_video, (ttype, stem, options, extra)) in enumerate(
        SERIES, start=1
    ):
        ext_uid = f"lms:c139:vvod:{i:02d}"
        has_hints = bool(hints_video)
        task_content = {
            "code": None, "stem": stem, "tags": None,
            "type": ttype, "media": None, "title": None, "prompt": None,
            "options": options, "has_hints": has_hints,
            "course_uid": COURSE_UID,
            "hints_text": [], "hints_video": hints_video,
            "difficulty_code": None,
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
        mat_before = cur.fetchone()
        print(f"заданий в курсе {COURSE_ID} до: {before_cnt}")
        print(f"материал {MATERIAL_ID} is_active до: {mat_before[0] if mat_before else 'НЕТ'}")

        # ── 1. Сдвинуть все задания +N ───────────────────────────────────────
        cur.execute(
            "UPDATE tasks SET order_position = order_position + %s "
            "WHERE course_id=%s",
            (N, COURSE_ID),
        )
        shifted = cur.rowcount
        print(f"сдвинуто заданий на +{N}: {shifted}")

        # ── 2. Вставить 27 заданий на позиции 1..N ───────────────────────────
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
        print(f"вставлено новых заданий: {len(rows)}")

        # ── 3. Погасить материал 356 ─────────────────────────────────────────
        cur.execute(
            "UPDATE materials SET is_active=false WHERE id=%s AND course_id=%s",
            (MATERIAL_ID, COURSE_ID),
        )
        mat_upd = cur.rowcount
        print(f"погашен материал {MATERIAL_ID}: {mat_upd} строк")

        # ── самопроверка ──────────────────────────────────────────────────────
        cur.execute("SELECT count(*) FROM tasks WHERE course_id=%s", (COURSE_ID,))
        after_cnt = cur.fetchone()[0]

        cur.execute(
            "SELECT min(order_position), max(order_position), count(*), "
            "count(DISTINCT order_position) FROM tasks WHERE course_id=%s",
            (COURSE_ID,),
        )
        pmin, pmax, pcnt, pdistinct = cur.fetchone()

        cur.execute("SELECT is_active FROM materials WHERE id=%s", (MATERIAL_ID,))
        mat_after = cur.fetchone()[0]

        cur.execute(
            "SELECT order_position, external_uid, task_content->>'type' AS ttype "
            "FROM tasks WHERE external_uid LIKE 'lms:c139:vvod:%' "
            "ORDER BY order_position",
        )
        new_rows = cur.fetchall()

        expected_after = before_cnt + N   # 74 + 27 = 101

        print(f"\n── состояние после ───────────────────────────────────────")
        print(f"заданий: {after_cnt}  (ожидается {expected_after})")
        print(f"order_position: min={pmin} max={pmax} count={pcnt} distinct={pdistinct}")
        print(f"материал {MATERIAL_ID} is_active после: {mat_after}")
        print("новые задания:")
        for pos, uid, ttype in new_rows:
            print(f"  pos={pos:3d}  {uid}  {ttype}")

        checks = {
            f"до было {before_cnt} заданий":         before_cnt == 74,
            f"итог {expected_after}":                 after_cnt == expected_after,
            f"позиции непрерывны 1..{expected_after}": (
                pmin == 1 and pmax == expected_after
                and pdistinct == expected_after
            ),
            "сдвинуто 74 задания":                   shifted == 74,
            f"вставлено {N} новых":                   len(new_rows) == N,
            f"новые на pos 1..{N}":                   (
                [r[0] for r in new_rows] == list(range(1, N + 1))
            ),
            f"материал {MATERIAL_ID} погашен":        not mat_after,
        }

        print(f"\n── проверки ─────────────────────────────────────────────")
        for name, ok in checks.items():
            print(f"  [{'OK' if ok else 'FAIL'}] {name}")

        if all(checks.values()) and apply:
            conn.commit()
            print("РЕЗУЛЬТАТ: все проверки пройдены, COMMIT.")
        elif all(checks.values()):
            conn.rollback()
            print("РЕЗУЛЬТАТ: DRY-RUN пройден, ROLLBACK. Запусти с --apply.")
        else:
            conn.rollback()
            print("РЕЗУЛЬТАТ: проверки НЕ пройдены, ROLLBACK.")
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
