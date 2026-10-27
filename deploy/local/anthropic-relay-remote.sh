#!/bin/bash
# tsk-1376: серверная часть пересыльщика. Передаётся в `ssh lms-spw-vds bash -s -- <режим>`,
# на сервере не хранится.
#   free  — снять висящий приёмник 127.0.0.1:18443 (sshd старой сессии тоннеля). Вызывается,
#           когда наш ssh уже завершился: любой оставшийся приёмник — мёртвый, он принимает
#           соединение и никуда его не ведёт (таймаут 12 c у наставника вместо отказа).
#   probe — проверка живости: HTTP-ответ пересыльщика через тоннель. Пересыльщик на GET
#           отвечает 404 сразу; 000 — тоннель или пересыльщик не отвечает.
set -u
PORT=18443
case "${1:-}" in
  free)
    pids=$(ss -ltnpH "sport = :$PORT" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
    if [ -z "$pids" ]; then echo "free: порт свободен"; exit 0; fi
    for p in $pids; do
      if [ "$(ps -o comm= -p "$p")" = "sshd" ]; then
        kill "$p" && echo "free: снят приёмник sshd pid=$p"
      else
        echo "free: порт держит не sshd (pid=$p), не трогаю"; exit 2
      fi
    done
    ;;
  probe)
    curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/" || true
    ;;
  *)
    echo "режим: free | probe"; exit 1
    ;;
esac
