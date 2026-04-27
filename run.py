# run.py (корень проекта)

import argparse
import os
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

    uvicorn.run(
        "app.api.main:app",
        host="0.0.0.0",
        port=8000,
        log_config=None,
        reload=args.dev,
    )
