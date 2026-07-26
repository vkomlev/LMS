#!/usr/bin/env bash
# Деплой LMS-полигона (tsk-182) на изолированный VPS. Запускать на сервере
# из /opt/lms-poligon: sudo -u app bash deploy/poligon/deploy-lms-poligon.sh
#
# Один код (ветка `poligon`) обслуживает все 3 tier'а (dev/test/stage) —
# различаются только .env.<tier> и systemd unit. Деплой поэтому обновляет
# код один раз, прогоняет alembic на всех 3 БД и рестартует все 3 сервиса.
# Сид/сброс данных — ОТДЕЛЬНО, см. scripts/poligon_seed.py (не часть деплоя,
# сброс — деструктивная операция, не должна триггериться каждым code-push).
set -euo pipefail

TIERS=(dev test stage)

# Тело — внутри функции: `git reset --hard` переписывает и сам этот файл на
# диске, без обёртки команды после reset читались бы рассинхронизированно
# (тот же приём, что в deploy/vps/deploy.sh прод-LMS).
main() {
  cd /opt/lms-poligon

  echo "== проверка владельца рабочего дерева (тот же guard, что на проде, R-009) =="
  local foreign
  foreign=$(find /opt/lms-poligon -mindepth 1 \
      \( -path /opt/lms-poligon/.git -o -path /opt/lms-poligon/venv \) -prune -o \
      \! -user app -printf '%u:%g %p\n' 2>/dev/null)
  if [[ -n "$foreign" ]]; then
    echo "ОШИБКА: в /opt/lms-poligon есть объекты не под владельцем app — git reset --hard упадёт." >&2
    echo "Первые 20:" >&2
    echo "$foreign" | head -20 >&2
    echo "Лечение (под root): chown -R app:app /opt/lms-poligon" >&2
    exit 1
  fi

  echo "== сохранение текущей версии для возможного отката =="
  git rev-parse HEAD > .last-deploy-sha
  echo "Версия перед деплоем (цель отката): $(cat .last-deploy-sha)"

  echo "== git fetch + reset to origin/poligon (НЕ main!) =="
  git fetch origin
  git reset --hard origin/poligon

  echo "== pip install =="
  source venv/bin/activate
  pip install --upgrade -r requirements.txt

  echo "== alembic upgrade head — на все 3 БД полигона =="
  for tier in "${TIERS[@]}"; do
    if [[ ! -f ".env.${tier}" ]]; then
      echo "ОШИБКА: .env.${tier} не найден — первичная настройка не завершена (см. README.md)." >&2
      exit 1
    fi
    local db_url
    db_url=$(grep '^DATABASE_URL=' ".env.${tier}" | cut -d= -f2-)
    echo "-- tier=${tier} --"
    DATABASE_URL="$db_url" alembic upgrade head
  done
  deactivate

  echo "== restart всех 3 сервисов =="
  for tier in "${TIERS[@]}"; do
    sudo systemctl restart "lms-poligon-${tier}"
  done
  sleep 2
  for tier in "${TIERS[@]}"; do
    systemctl is-active "lms-poligon-${tier}"
  done

  echo "== smoke: /health на всех 3 портах =="
  curl -fsS http://127.0.0.1:8010/health && echo " (dev)"
  curl -fsS http://127.0.0.1:8011/health && echo " (test)"
  curl -fsS http://127.0.0.1:8012/health && echo " (stage)"

  echo "Deployed: $(git rev-parse --short HEAD)"
  echo "Напоминание: данные НЕ сброшены этим деплоем. Для сброса —"
  echo "  sudo -u app venv/bin/python scripts/poligon_seed.py --tier <dev|test|stage> --reset"
}

main "$@"
