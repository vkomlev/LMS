#!/usr/bin/env bash
# Откат LMS-полигона к версии перед последним deploy-lms-poligon.sh.
# Запускать на сервере: sudo -u app bash deploy/poligon/rollback-lms-poligon.sh
#
# ВНИМАНИЕ: откатывает только код+зависимости+рестарт. Alembic-миграции НЕ
# откатываются автоматически (тот же принцип, что в проде) — учебные данные
# полигона тоже стоит беречь от случайного слома схемы при откате.
set -euo pipefail

TIERS=(dev test stage)

main() {
  cd /opt/lms-poligon

  echo "== проверка владельца рабочего дерева =="
  local foreign
  foreign=$(find /opt/lms-poligon -mindepth 1 \
      \( -path /opt/lms-poligon/.git -o -path /opt/lms-poligon/venv \) -prune -o \
      \! -user app -printf '%u:%g %p\n' 2>/dev/null)
  if [[ -n "$foreign" ]]; then
    echo "ОШИБКА: не-app объекты в /opt/lms-poligon — chown -R app:app /opt/lms-poligon" >&2
    exit 1
  fi

  if [[ ! -f .last-deploy-sha ]]; then
    echo "ОШИБКА: .last-deploy-sha не найден — нечего откатывать." >&2
    exit 1
  fi

  local target_sha current_sha
  target_sha=$(cat .last-deploy-sha)
  current_sha=$(git rev-parse HEAD)

  if [[ "$target_sha" == "$current_sha" ]]; then
    echo "Откатывать некуда: версия совпадает с сохранённой ($current_sha)."
    exit 0
  fi

  echo "== откат: $current_sha -> $target_sha =="
  git fetch origin
  git reset --hard "$target_sha"

  source venv/bin/activate
  pip install --upgrade -r requirements.txt
  deactivate

  for tier in "${TIERS[@]}"; do
    sudo systemctl restart "lms-poligon-${tier}"
  done
  sleep 2
  for tier in "${TIERS[@]}"; do
    systemctl is-active "lms-poligon-${tier}"
  done

  curl -fsS http://127.0.0.1:8010/health && echo " (dev)"
  curl -fsS http://127.0.0.1:8011/health && echo " (test)"
  curl -fsS http://127.0.0.1:8012/health && echo " (stage)"

  echo "Откат выполнен: $(git rev-parse --short HEAD)"
}

main "$@"
