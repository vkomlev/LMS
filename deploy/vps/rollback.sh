#!/usr/bin/env bash
# Откат LMS на VPS к версии, зафиксированной перед последним запуском deploy.sh.
# Запускать на самом сервере из /opt/lms: sudo -u app bash deploy/vps/rollback.sh
#
# ВНИМАНИЕ: откатывает только код (git + зависимости + перезапуск сервиса).
# Alembic-миграции НЕ откатываются автоматически — если последний деплой включал
# новую миграцию, оценить и запустить `alembic downgrade` нужно вручную и осознанно
# (потенциально деструктивная операция над реальными данными учеников).
set -euo pipefail

cd /opt/lms

if [[ ! -f .last-deploy-sha ]]; then
  echo "ОШИБКА: .last-deploy-sha не найден — нечего откатывать" \
       "(ни одного деплоя через deploy.sh ещё не было на этом сервере)." >&2
  exit 1
fi

TARGET_SHA=$(cat .last-deploy-sha)
CURRENT_SHA=$(git rev-parse HEAD)

if [[ "$TARGET_SHA" == "$CURRENT_SHA" ]]; then
  echo "Откатывать некуда: текущая версия ($CURRENT_SHA) совпадает с сохранённой для отката."
  exit 0
fi

echo "== откат: $CURRENT_SHA -> $TARGET_SHA =="
git fetch origin
git reset --hard "$TARGET_SHA"

echo "== pip install (версия до отката может требовать другие зависимости) =="
source venv/bin/activate
pip install --upgrade -r requirements.txt
deactivate

echo "== restart service =="
sudo systemctl restart lms
sleep 2
sudo systemctl is-active lms

echo "== smoke: /health =="
curl -fsS http://127.0.0.1:8000/health && echo

echo "Откат выполнен: $(git rev-parse --short HEAD)"
echo "Если последний деплой включал alembic-миграцию — оценить вручную, нужен ли" \
     "'alembic downgrade' (не выполняется этим скриптом автоматически)."
