# -*- coding: utf-8 -*-
"""tsk-317/tsk-319 доп.: прикрепить файлы-приложения к 30 file-gated заданиям Крылова.

ЧТО ДЕЛАЕТ
tsk-317 (F3) и tsk-319 (F6) закрыли ответы для 92 Крылов-заданий (варианты
1/5/11/16), но 30 из них (типы 3, 9, 10, 17, 18, 22, 24, 26, 27) остались
без файла-приложения — по книге он распространяется через сайт ege.plus под
кодом доступа с голограммы бумажного издания, недоступным нам (см. отчёты
tsk-317/tsk-319).

Оператор обнаружил и указал на локальный архив
`D:\\Work\\CyberGuru\\EGE\\docs\\Варианты\\Файлы для выполнения заданий-...zip`
— он оказался подлинным набором файлов-приложений именно к этой книге
(организован по тем же 9 номерам заданий × 20 вариантам, что и наш пробел).

ВЕРИФИКАЦИЯ (5/5 точных совпадений, до записи)
- Задание 9, вариант 1: пересчитал условие («строка, где одно число повторяется
  трижды, сумма неповторяющихся больше...») по файлу — 11597, совпадает с уже
  записанным ответом.
- Задание 3, все 4 варианта: имена листов и заголовки столбцов файла дословно
  совпадают с текстом условия (Аренда/Электросамокаты/Клиенты — вариант 1;
  Наличие/Продукты/Производственные_базы — вариант 5; Движение_лекарственных_
  средств/Препараты/Аптека — вариант 11; Готовый_товар/Продукция/Ткани —
  вариант 16).
- Задание 10, вариант 1: пересчитал вхождения «был» в составе слов (не отдельно)
  в главах X-XII романа «Рудин» (главы найдены по маркерам в тексте, XII —
  кириллической «Х», не латинской) — 35, совпадает.
- Задание 17, вариант 1: пересчитал тройки с условием — «8 99191», совпадает
  ЦЕЛИКОМ (оба числа).
- Задание 24, вариант 1: пересчитал максимальную подпоследовательность с S и
  35 нечётными цифрами — 272, совпадает.

Задания 18, 22, 26, 27 (grid-робот/DAG-планирование/самолёты-команды/
кластеризация звёзд) НЕ переалгоритмизированы (сложность реализации несоразмерна
объёму), но файлы верифицированы структурно: формат и заголовки совпадают с
описанием в условии для каждого варианта, файлы из ТОГО ЖЕ архива, что дал 5/5
точных числовых совпадений на других типах — уверенность высокая, не 100%.

Файлы залиты в CAS/S3 (`store_bytes_to_cas`, тот же прод-бакет), публичная
доступность подтверждена (выборочно, 5 файлов разных форматов — 200 OK).

Ссылка вставляется отдельным `<p>` в НАЧАЛО stem (не требует поиска якоря,
устойчиво к разнице форматирования между 30 заданиями).

Запуск: dry-run по умолчанию; --apply при DBCHECK_OK=1.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

MEDIA_BASE = "/api/v1/media"

# external_uid -> [(sha_ext, label)]
ATTACHMENTS: dict[str, list[tuple[str, str]]] = {
    "crylov:v1t3": [("27d74af63aac444f62a4931377d75a8f69bf8f4ca3665a0d881c4974c616a01a.ods", "Файл к заданию")],
    "crylov:v1t9": [("b4dce32d5ae47cfe88c272b35d02b8312723af22f56a6d7b6b5454e8a33f95ae.ods", "Файл к заданию")],
    "crylov:v1t10": [("486a739bfcb9857e50550e0adec189244a6958beb643b64efce721f2e9b7d085.odt", "Файл к заданию")],
    "crylov:v1t17": [("29ac57ea222afbbd447defa454f46dcc50666a0c4827d01901975423eb1809d2.txt", "Файл к заданию")],
    "crylov:v1t18": [("81851b446443c3cd9b34b35e6aeb64185e27889075d0696e9b295757bd8aed54.ods", "Файл к заданию")],
    "crylov:v1t22": [("944ef021825066753723eef46b0da572c92e907accabbc8f687d019040fbfb2b.ods", "Файл к заданию")],
    "crylov:v1t24": [("576286b648ff8dc8f4b301a1caf5c7cbbf21581966bb79bff6f249a44ee8d1ec.txt", "Файл к заданию")],
    "crylov:v1t26": [("c1a6e6ab6751dde50802691561532fb46b83fe45f06e132b5b1e114e7405b3cb.txt", "Файл к заданию")],
    "crylov:v1t27": [
        ("d253f1b5c9d3aecac99e959e221c6e6aa744689dce2cf44e488fad45c13ef570.txt", "Файл А"),
        ("ebe349ac19cd10334a0a99ec9a0628d98b5b10d51b71ebfadcd541a6dcf5bfd6.txt", "Файл Б"),
    ],
    "crylov:v5t3": [("2d5d4e6368b0d77b86ba71f4e8dd8840a2e04c6f6d83485043620e8ec73c6d88.ods", "Файл к заданию")],
    "crylov:v5t10": [("2bba7d13b84293147027a71cc79c637124de70cbc2930d38741d1eeb7e2356b2.odt", "Файл к заданию")],
    "crylov:v5t17": [("27eba7eee1e85d9553cdb8004f032e736257153cbe92c09baf6e18ce47bcf1a6.txt", "Файл к заданию")],
    "crylov:v5t18": [("b0d0780af0146f75f28f3d6aff9c495ed22ab4ad0d8511bb5965e922de573852.ods", "Файл к заданию")],
    "crylov:v5t22": [("00b256dfa280f9937beb22db3203b7aaeb8e3e8507d33c4320ddb9161abd0bec.ods", "Файл к заданию")],
    "crylov:v5t24": [("dfba9a6b8c2642639e24fa7ede53c57c303bb8a1364db1f224718b36c949f8b5.txt", "Файл к заданию")],
    "crylov:v5t27": [
        ("9b29cb0f3b19689c81f9a80f053e906d0a9ece8c21b7e741e27141dc54e10872.txt", "Файл А"),
        ("69f4454b524fb254452ae3cc0b3f208f382ea4f7bf71be9e4ecc5159841b9555.txt", "Файл Б"),
    ],
    "crylov:v11t3": [("0a29670e929f0bd9b1b67f61073417cda25141aacb302af65eccac86baa29727.ods", "Файл к заданию")],
    "crylov:v11t9": [("e697fae45309a3d1a8637136fb8e1853b2da45cbc98c4845dfe1cf59cacd6723.ods", "Файл к заданию")],
    "crylov:v11t10": [("2bba7d13b84293147027a71cc79c637124de70cbc2930d38741d1eeb7e2356b2.odt", "Файл к заданию")],
    "crylov:v11t17": [("a6516b90ba98132c71dcc60e819063f5f3c869338d9a125f1d0ccb103a083e5f.txt", "Файл к заданию")],
    "crylov:v11t18": [("650b0f3cb45f1390acf1c68fa1da18889fdb199067946b30b4a41eb834fff24b.ods", "Файл к заданию")],
    "crylov:v11t22": [("915da92e09112ef24b0ad875751672566e347ba5fe6ea2993fe370aac5d9831f.ods", "Файл к заданию")],
    "crylov:v11t24": [("dc009d113c7d0bb386a6a1499e732dc93c3c9b88a01d381333410e6005213ad2.txt", "Файл к заданию")],
    "crylov:v11t26": [("b28003205fdfecb3cd7ad2e2e623b95e5f6ed0d981ae63ef866400d22117f0f9.txt", "Файл к заданию")],
    "crylov:v11t27": [
        ("4ffa21288251eaf25827bdaf52d9d4a6fc0838a043dabd60b4dd7371c7d3b249.txt", "Файл А"),
        ("b6cb942cae7f73e7338c6fb4a63cf3246d91f3f914dba035ee53e37cc44f8314.txt", "Файл Б"),
    ],
    "crylov:v16t3": [("7ba4843c79586cd7b277eb2cedde51fe59ded3a99e047fda8518d7106fd4bd38.ods", "Файл к заданию")],
    "crylov:v16t17": [("8e63720ba2d54a5b03799b4a32705f4e92a51c8ec407022aab4f39714cc91366.txt", "Файл к заданию")],
    "crylov:v16t22": [("243ffc87c25ce8e925cd3a1737d018c913465c02727ae4ce431ed53b4d627dc4.ods", "Файл к заданию")],
    "crylov:v16t24": [("4cb7e9d3589bee7700fdbfeb3a1084fa2985d8d219f393487fa9abac3b92e517.txt", "Файл к заданию")],
    "crylov:v16t27": [
        ("ab5cc15ef05b95fd061aa3437d54ed62c621fa46adde7ef8035e2a3e8b40a5ca.txt", "Файл А"),
        ("b6cb942cae7f73e7338c6fb4a63cf3246d91f3f914dba035ee53e37cc44f8314.txt", "Файл Б"),
    ],
}


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


def build_link_block(items: list[tuple[str, str]]) -> str:
    links = " &middot; ".join(
        f'<a href="{MEDIA_BASE}/{sha}" rel="noopener noreferrer" target="_blank">{label}</a>'
        for sha, label in items
    )
    return f"<p>{links}</p>\n"


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            uids = list(ATTACHMENTS.keys())
            rows = await conn.fetch(
                "SELECT id, external_uid, task_content->>'stem' AS stem "
                "FROM tasks WHERE external_uid = ANY($1::text[])",
                uids,
            )
            by_uid = {r["external_uid"]: r for r in rows}
            missing = set(uids) - set(by_uid)
            if missing:
                raise RuntimeError(f"не найдены external_uid: {missing}")

            print(f"Целевых заданий: {len(uids)}")
            already_has_file_link = [
                uid for uid in uids
                if "/api/v1/media/" in (by_uid[uid]["stem"] or "")
                and any(by_uid[uid]["stem"].count(sha) for sha, _ in ATTACHMENTS[uid])
            ]
            if already_has_file_link:
                raise RuntimeError(
                    f"{len(already_has_file_link)} заданий уже содержат эту ссылку "
                    f"(повторный запуск?): {already_has_file_link}"
                )

            updated = 0
            for uid, items in ATTACHMENTS.items():
                row = by_uid[uid]
                block = build_link_block(items)
                new_stem = block + row["stem"]
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set(task_content, '{stem}', to_jsonb($2::text)) "
                    "WHERE id = $1",
                    row["id"], new_stem,
                )
                updated += 1

            if updated != len(uids):
                raise AssertionError(f"ожидали обновить {len(uids)}, обновлено {updated}")

            # ---- Верификация ----
            check_rows = await conn.fetch(
                "SELECT external_uid, task_content->>'stem' AS stem "
                "FROM tasks WHERE external_uid = ANY($1::text[])",
                uids,
            )
            bad = []
            for r in check_rows:
                stem = r["stem"] or ""
                for sha, _ in ATTACHMENTS[r["external_uid"]]:
                    if sha not in stem:
                        bad.append((r["external_uid"], sha))
            if bad:
                raise AssertionError(f"ссылки не подтвердились: {bad}")

            print(f"\nOK: {updated}/{len(uids)} заданий получили ссылку(и) на файл-приложение.")

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
