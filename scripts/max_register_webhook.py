"""Подписать бота MAX на вебхук LMS (tsk-1253, этап Б).

Запуск на сервере с загруженным .env (MAX_BOT_TOKEN, MAX_WEBHOOK_SECRET,
PUBLIC_API_BASE_URL или аргумент --url):

    python scripts/max_register_webhook.py --url https://<api>/api/v1/integrations/max/webhook
    python scripts/max_register_webhook.py --list

Токен и секрет в вывод не попадают.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.messenger_channel_service import max_ssl_context  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("max_register_webhook")

API = "https://platform-api2.max.ru"
UPDATE_TYPES = ["bot_started", "bot_stopped", "message_created"]


def main() -> int:
    """Создать подписку или показать текущие."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="HTTPS-адрес вебхука LMS")
    parser.add_argument("--list", action="store_true", help="только показать подписки")
    args = parser.parse_args()
    token = os.getenv("MAX_BOT_TOKEN", "")
    secret = os.getenv("MAX_WEBHOOK_SECRET", "")
    if not token:
        logger.error("MAX_BOT_TOKEN не задан")
        return 2
    headers = {"Authorization": token}
    with httpx.Client(timeout=20, verify=max_ssl_context()) as client:
        if args.list or not args.url:
            r = client.get(f"{API}/subscriptions", headers=headers)
            logger.info("%s %s", r.status_code, r.text.replace(token, "<T>"))
            return 0 if r.is_success else 1
        if not secret:
            logger.error("MAX_WEBHOOK_SECRET не задан")
            return 2
        r = client.post(f"{API}/subscriptions", headers=headers,
                        json={"url": args.url, "update_types": UPDATE_TYPES, "secret": secret})
        logger.info("%s %s", r.status_code, r.text.replace(token, "<T>").replace(secret, "<S>"))
        return 0 if r.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
