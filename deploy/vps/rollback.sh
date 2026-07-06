#!/usr/bin/env bash
# Откат LMS на VPS к версии, зафиксированной перед последним запуском deploy.sh.
# Запускать на самом сервере из /opt/lms: sudo -u app bash deploy/vps/rollback.sh
#
# ВНИМАНИЕ: откатывает только код (git + зависимости + перезапуск сервиса).
# Alembic-миграции НЕ откатываются автоматически — если последний деплой включал
# новую миграцию, оценить и запустить `alembic downgrade` нужно вручную и осознанно
# (потенциально деструктивная операция над реальными данными учеников).
set -euo pipefail

# Всё тело — внутри функции: `git reset --hard` ниже переписывает и сам этот
# файл (bash читает исполняемый скрипт с диска по мере выполнения), поэтому
# без обёртки в функцию рассинхронизация чтения даёт случайные ошибки на
# командах после reset. Тело функции целиком разбирается в память ДО первого
# вызова — reset её больше не задевает.
main() {
  cd /opt/lms

  if [[ ! -f .last-deploy-sha ]]; then
    echo "ОШИБКА: .last-deploy-sha не найден — нечего откатывать" \
         "(ни одного деплоя через deploy.sh ещё не было на этом сервере)." >&2
    exit 1
  fi

  local target_sha current_sha
  target_sha=$(cat .last-deploy-sha)
  current_sha=$(git rev-parse HEAD)

  if [[ "$target_sha" == "$current_sha" ]]; then
    echo "Откатывать некуда: текущая версия ($current_sha) совпадает с сохранённой для отката."
    exit 0
  fi

  echo "== откат: $current_sha -> $target_sha =="
  git fetch origin
  git reset --hard "$target_sha"

  echo "== pip install (версия до отката может требовать другие зависимости) =="
  source venv/bin/activate
  pip install --upgrade -r requirements.txt
  deactivate

  echo "== restart service =="
  sudo systemctl restart lms
  sleep 2
  systemctl is-active lms

  echo "== smoke: /health =="
  curl -fsS http://127.0.0.1:8000/health && echo

  echo "Откат выполнен: $(git rev-parse --short HEAD)"
  echo "Если последний деплой включал alembic-миграцию — оценить вручную, нужен ли" \
       "'alembic downgrade' (не выполняется этим скриптом автоматически)."
}

main "$@"
