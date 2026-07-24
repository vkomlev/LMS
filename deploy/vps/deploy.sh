#!/usr/bin/env bash
# Ручной деплой LMS на VPS. Запускать на самом сервере из /opt/lms.
set -euo pipefail

# Всё тело — внутри функции: `git reset --hard` ниже переписывает и сам этот
# файл (bash читает исполняемый скрипт с диска по мере выполнения), поэтому
# без обёртки в функцию рассинхронизация чтения даёт случайные ошибки на
# командах после reset. Тело функции целиком разбирается в память ДО первого
# вызова — reset её больше не задевает.
main() {
  cd /opt/lms

  echo "== проверка владельца рабочего дерева (страховка tsk-394) =="
  # Прод-скрипты, случайно запущенные под root (ssh на этот сервер логинится
  # root'ом), оставляют в /opt/lms файлы root:root. `git reset --hard` ниже
  # переписывает рабочее дерево и падает на них невнятным Permission denied.
  # Ловим это ДО reset и даём понятную ошибку с готовой командой лечения.
  # Правило: прод-скрипты запускать под app (sudo -u app ...), см.
  # docs/ai/operator-runbook.md. .git и venv исключены: reset --hard их не
  # трогает (venv не под git; .git — забота самого git и он всегда под app).
  local foreign
  foreign=$(find /opt/lms -mindepth 1 \
      \( -path /opt/lms/.git -o -path /opt/lms/venv \) -prune -o \
      \! -user app -printf '%u:%g %p\n' 2>/dev/null)
  if [[ -n "$foreign" ]]; then
    echo "ОШИБКА: в /opt/lms есть объекты не под владельцем app — git reset --hard упадёт." >&2
    echo "Первые 20:" >&2
    echo "$foreign" | head -20 >&2
    echo "" >&2
    echo "Причина: прод-скрипт запускали под root, а не под app (tsk-394)." >&2
    echo "Лечение (на сервере под root): chown -R app:app /opt/lms" >&2
    echo "Затем повторить деплой. Впредь прод-скрипты запускать под app:" >&2
    echo "  ssh lms-spw-vds 'sudo -u app bash -lc \"cd /opt/lms && venv/bin/python scripts/X.py\"'" >&2
    exit 1
  fi

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
  systemctl is-active lms

  echo "== smoke: /health =="
  curl -fsS http://127.0.0.1:8000/health && echo

  echo "Deployed: $(git rev-parse --short HEAD)"
}

main "$@"
