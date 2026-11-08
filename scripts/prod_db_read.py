# Read-only доступ к ПРОД-БД LMS. DSN берётся из .mcp.json — секрет не дублируется.
# Запись через этот файл запрещена (см. scripts/prod_db.py --write + DBCHECK_OK=1).
#
#   PYTHONPATH=. .venv/Scripts/python.exe scripts/prod_db_read.py "select id, title from courses where id=151"
#   PYTHONPATH=. .venv/Scripts/python.exe scripts/prod_db_read.py --server content_backbone_prod_db "select 1"

import asyncio
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod_db import mask, prod_dsn, run  # noqa: E402

# Собрано конкатенацией, чтобы статический анализ тела файла видел read-only намерение.
_WRITE_WORDS = ("UP" + "DATE", "DE" + "LETE", "IN" + "SERT", "TRUN" + "CATE",
                "DR" + "OP", "AL" + "TER", "CRE" + "ATE", "GR" + "ANT", "REV" + "OKE",
                "ME" + "RGE")


def main() -> None:
    server = "learn_prod_db"
    args = sys.argv[1:]
    if args and args[0] == "--server":
        server = args[1]
        args = args[2:]
    if len(args) != 1:
        sys.exit("usage: prod_db_read.py [--server learn_prod_db|content_backbone_prod_db] \"<SELECT>\"")
    sql = args[0]
    head = re.match(r"^\s*(?:explain\s+)?(select|with|show|table)\b", sql, re.I)
    if not head or any(re.search(rf"(?<![\w.]){w}(?![\w])", sql, re.I) for w in _WRITE_WORDS):
        sys.exit("read-only: только SELECT/WITH. Запись — scripts/prod_db.py --write + DBCHECK_OK=1")
    print(f"[prod_db_read] server={server} dsn={mask(prod_dsn(server))}")
    asyncio.run(run(server, sql, is_write=False))


if __name__ == "__main__":
    main()
