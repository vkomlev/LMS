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

  echo "== git fetch =="
  # Версия протокола задана ЯВНО (02.09.2026). Без неё git 2.34 на этом сервере
  # сбивается на согласовании протокола с GitHub и трактует ответ как требование
  # логина: «could not read Username for https://github.com» + «expected flush
  # after ref listing». Репозиторий при этом публичный — тот же адрес анонимный
  # curl отдаёт 200, а `git -c protocol.version=2 ls-remote` работает. Без pty
  # выкат падает сразу, с pty (`ssh -tt`) — ВИСИТ на приглашении ввести имя, и
  # выглядит это как зависший деплой, а не как ошибка.
  #
  # Обход держится здесь, а не в конфиге сервера, намеренно: конфиг переживёт
  # только этот сервер и потеряется при пересоздании машины.
  git -c protocol.version=2 fetch origin

  # Выкат делает reset --hard на origin/main, то есть на прод уезжает ВСЁ
  # закоммиченное в ветке, а не только работа выкатывающего. Чипы работают в
  # одну ветку, поэтому «я свою правку не выкатывал» не значит «её нет на
  # проде»: 25.08 неодобренная работа уехала прицепом с чужим деплоем (tsk-672,
  # увезло tsk-665, увезло работу tsk-658).
  #
  # Кто сознательно придерживает работу до решения оператора — ставит в тело
  # СВОЕГО коммита строку:
  #   Hold-For-Operator: <что должен решить оператор>
  # Гейт ниже не даёт соседу увезти такой коммит молча.
  # Осознанно выкатить всё равно:
  #   sudo -u app env DEPLOY_HOLD_OK=1 bash /opt/lms/deploy/vps/deploy.sh
  local range="HEAD..origin/main" ahead held
  echo "== что уедет на прод сверх текущей версии =="
  ahead=$(git rev-list --count "$range")
  if [[ "$ahead" -eq 0 ]]; then
    echo "Новых коммитов нет: прод уже на origin/main."
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
    echo "ОСТАНОВЛЕНО: в ветке есть работа, помеченная как ждущая решения оператора." >&2
    echo "$held" >&2
    echo "" >&2
    echo "Чего ждут:" >&2
    git log "$range" --grep='Hold-For-Operator:' --format='%B' \
      | grep -i '^Hold-For-Operator:' >&2 || true
    echo "" >&2
    echo "Ничего не тронуто: прод остался на прежней версии." >&2
    echo "Выкат идёт целиком из ветки — выкатить «только своё» нельзя, ветка одна." >&2
    echo "Что делать:" >&2
    echo "  1) дождаться решения оператора по пункту выше;" >&2
    echo "  2) решение получено — повторить деплой с DEPLOY_HOLD_OK=1 и записать это в задачу." >&2
    exit 2
  fi

  echo "== reset to origin/main =="
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

  # Сверка по факту, а не по намерению (tsk-672/tsk-676): печатаем то, что
  # реально приехало в рабочее дерево, а не то, что выкатывающий собирался
  # выкатить. Список до reset — прогноз, этот — факт.
  echo "== что фактически уехало на прод =="
  git --no-pager log --oneline "$(cat .last-deploy-sha)..HEAD" || true

  echo "Deployed: $(git rev-parse --short HEAD)"
}

main "$@"
