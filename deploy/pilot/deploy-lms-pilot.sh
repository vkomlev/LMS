#!/usr/bin/env bash
# Повторный выкат LMS экземпляра «pilot» (tsk-764).
# Запускать на сервере: sudo -u app bash /opt/lms-pilot/deploy/pilot/deploy-lms-pilot.sh
#
# Экземпляр живёт на той же ветке `main`, что и боевой — в этом весь смысл
# развилки tsk-764: один код, разные базы. Форк отклонён.
# Боевой `lms.service` (/opt/lms) этот скрипт не трогает: другой чекаут,
# другой unit, другая база.
set -euo pipefail

ROOT=/opt/lms-pilot
SERVICE=lms-pilot
PORT=8020

# Тело — внутри функции: `git reset --hard` переписывает и сам этот файл на
# диске, без обёртки команды после reset читались бы рассинхронизированно
# (тот же приём, что в deploy/vps/deploy.sh боевого LMS).
main() {
  cd "$ROOT"

  echo "== проверка владельца рабочего дерева (R-009) =="
  local foreign
  foreign=$(find "$ROOT" -mindepth 1 \
      \( -path "$ROOT/.git" -o -path "$ROOT/venv" -o -path "$ROOT/data" -o -path "$ROOT/uploads" \) -prune -o \
      \! -user app -printf '%u:%g %p\n' 2>/dev/null)
  if [[ -n "$foreign" ]]; then
    echo "ОШИБКА: в $ROOT есть объекты не под владельцем app — git reset --hard упадёт." >&2
    echo "$foreign" | head -20 >&2
    echo "Лечение (под root): chown -R app:app $ROOT" >&2
    exit 1
  fi

  echo "== сохранение текущей версии для возможного отката =="
  git rev-parse HEAD > .last-deploy-sha
  echo "Версия перед выкатом (цель отката): $(cat .last-deploy-sha)"

  echo "== git fetch =="
  git fetch origin

  echo "== что уедет сверх текущей версии =="
  local range="HEAD..origin/main" ahead held
  ahead=$(git rev-list --count "$range")
  if [[ "$ahead" -eq 0 ]]; then
    echo "Новых коммитов нет."
  else
    echo "Коммитов к выкату: $ahead"
    git --no-pager log --oneline "$range"
  fi

  echo "== проверка пометок «ждёт решения оператора» (tsk-672) =="
  held=$(git log "$range" --grep='Hold-For-Operator:' --format='%h %s' || true)
  if [[ -z "$held" ]]; then
    echo "Помеченных коммитов нет."
  elif [[ "${DEPLOY_HOLD_OK:-}" == "1" ]]; then
    echo "ВНИМАНИЕ: помеченные коммиты есть, выкат продолжен по DEPLOY_HOLD_OK=1:"
    echo "$held"
  else
    echo "ОСТАНОВЛЕНО: в ветке есть работа, ждущая решения оператора." >&2
    echo "$held" >&2
    git log "$range" --grep='Hold-For-Operator:' --format='%B' \
      | grep -i '^Hold-For-Operator:' >&2 || true
    echo "Ничего не тронуто. Решение получено → повторить с DEPLOY_HOLD_OK=1." >&2
    exit 2
  fi

  echo "== reset to origin/main =="
  git reset --hard origin/main

  echo "== pip install =="
  source venv/bin/activate
  pip install --quiet --upgrade -r requirements.txt

  echo "== alembic upgrade head — ТОЛЬКО база pilot =="
  if [[ ! -f .env ]]; then
    echo "ОШИБКА: $ROOT/.env не найден — первичная настройка не завершена (см. README.md)." >&2
    exit 1
  fi
  local db_url
  db_url=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
  case "$db_url" in
    *"/pilot"*) ;;
    *) echo "ОШИБКА: DATABASE_URL в .env ведёт не в базу pilot. Выкат остановлен." >&2; exit 1 ;;
  esac
  DATABASE_URL="$db_url" alembic upgrade head
  deactivate

  echo "== restart службы =="
  sudo systemctl restart "$SERVICE"
  sleep 2
  systemctl is-active "$SERVICE"

  echo "== smoke: /health =="
  curl -fsS "http://127.0.0.1:${PORT}/health" && echo

  echo "== боевой экземпляр не тронут =="
  systemctl is-active lms
  systemctl show lms -p ActiveEnterTimestamp

  echo "== что фактически уехало =="
  git --no-pager log --oneline "$(cat .last-deploy-sha)..HEAD" || true
  echo "Deployed: $(git rev-parse --short HEAD)"
}

main "$@"
