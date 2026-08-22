#!/usr/bin/env bash
# Ручной деплой LMS на VPS. Запускать на самом сервере из /opt/lms.
set -euo pipefail

# Ожидание готовности сервера после рестарта (tsk-640).
# Единичный curl сразу после `systemctl restart` попадает в момент, когда
# приложение ещё поднимается, и валит скрипт ложной ошибкой «деплой не удался»,
# хотя выкат прошёл (у SPW это ловилось как Connection refused / HTTP 000).
# Здесь socket activation (lms.socket, tsk-403) держит порт через рестарт и
# обычно прячет эту гонку — но лишь пока drop-in на месте: его откат прямо
# предусмотрен в socket.conf, и тогда возвращается тот же refused. Плюс без
# --max-time curl мог висеть неограниченно, если uvicorn не стартовал.
#
# /health отдаёт 200 всегда (app/api/main.py), поэтому здесь ждём именно 2xx,
# а не «любой ответ»: 5xx на /health — настоящий провал, а не старт.
# Использование: wait_for_http_ok <url> [число_попыток]
wait_for_http_ok() {
  local url=$1 attempts=${2:-30} i out code body
  for ((i = 1; i <= attempts; i++)); do
    out=$(curl -sS --max-time 5 -w $'\n%{http_code}' "$url" 2>/dev/null || true)
    code=${out##*$'\n'}
    body=${out%$'\n'*}
    if [[ "$code" == 2* ]]; then
      echo "HTTP $code (готов с попытки $i из $attempts): $body"
      return 0
    fi
    sleep 1
  done
  echo "ОШИБКА: $url не ответил 2xx за $attempts попыток (последний код: ${code:-нет ответа})." >&2
  echo "Смотреть: systemctl status lms; journalctl -u lms -n 50; /var/log/lms/app.log" >&2
  return 1
}

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

  echo "== smoke: /health (ждём готовности сервера, tsk-640) =="
  wait_for_http_ok http://127.0.0.1:8000/health 30

  echo "Deployed: $(git rev-parse --short HEAD)"
}

main "$@"
