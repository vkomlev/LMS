# run.py (корень проекта)

import argparse
import os
import socket
import sys
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"), encoding="utf-8-sig")

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="LMS Core API")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Режим разработки: hot-reload включён (не использовать в проде)",
    )
    args = parser.parse_args()

    port = 8000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        if _s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"Ошибка: порт {port} уже занят. Остановите текущий сервер перед запуском.")
            sys.exit(1)

    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=port,
        log_config=None,
        reload=args.dev,
    )
