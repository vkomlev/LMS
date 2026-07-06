#!/usr/bin/env bash
# Ручной деплой LMS на VPS. Запускать на самом сервере из /opt/lms.
set -euo pipefail

cd /opt/lms

echo "== сохранение текущей версии для возможного отката =="
git rev-parse HEAD > .last-deploy-sha
echo "Версия перед деплоем (цель отката): $(cat .last-deploy-sha)"

echo "== git fetch + reset to origin/main =="
git fetch origin
git reset --hard origin/main

echo "== pip install =="
source venv/bin/activate
pip install --upgrade -r requirements.txt

echo "== alembic upgrade head =="
alembic upgrade head
deactivate

echo "== restart service =="
sudo systemctl restart lms
sleep 2
sudo systemctl is-active lms

echo "== smoke: /health =="
curl -fsS http://127.0.0.1:8000/health && echo

echo "Deployed: $(git rev-parse --short HEAD)"
