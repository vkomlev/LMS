# run.py (корень проекта)

import os
from dotenv import load_dotenv

# Явно читаем .env
load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"), encoding="utf-8-sig")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.api.main:app",    # <-- здесь строка "модуль:приложение"
        host="0.0.0.0",
        port=8000,
        log_config=None,       # не переопределяем наш logger
        reload=True,           # теперь работает корректно
    )
