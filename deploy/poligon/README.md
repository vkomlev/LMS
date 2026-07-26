# Учебный полигон QA (tsk-182) — LMS часть

**Пересмотр 2026-07-25:** полигон разворачивается на уже существующей
инфраструктуре Timeweb, БЕЗ нового VPS — приложения на `lms-spw-vds`
(5.42.102.20, тот же сервер, что уже крутит прод LMS+SPW), БД на уже
существующем прод-Postgres-инстансе (5.42.107.253, где уже живут `learn` и
`content_backbone`) — новые базы `poligon_dev`/`poligon_test`/`poligon_stage`,
не новый сервер. Обоснование, диаграмма и честный разбор trade-off изоляции —
`docs/briefs/2026-07-25-tsk182-poligon-timeweb.md`, раздел 4 «Изоляция — что
изменилось».

Код — отдельная ветка `poligon` (никогда не мержится в `main`). Один git-чекаут
на `lms-spw-vds` (`/opt/lms-poligon`, РЯДОМ с прод-чекаутом `/opt/lms`), три
`.env.<tier>` + три systemd unit'а поверх него — разница между dev/test/stage
только в конфигурации, не в версии кода.

## Предпосылки

- Доступ по SSH на `lms-spw-vds` (уже есть — тот же сервер, что для прод LMS/SPW).
- Доступ, достаточный для `CREATE DATABASE`/`CREATE ROLE` на Postgres-инстансе
  5.42.107.253 (суперпользователь кластера или уже выданные права — уточнить
  у оператора, если не под рукой).
- DNS: 6 A-записей (см. бриф, раздел «Домены») на IP `lms-spw-vds` (5.42.102.20).
- Ветка `poligon` в репозитории LMS существует и содержит патчи из
  `deploy/poligon/new-code/` (применяются один раз при создании ветки — см.
  `new-code/README.md`).

## Первичная настройка (один раз, на `lms-spw-vds`)

```bash
ssh lms-spw-vds

# Пользователь app и Postgres client УЖЕ есть на этом сервере (используются
# прод LMS/SPW) — новых системных пакетов для LMS-части не требуется, кроме
# python3.11-venv (если ещё не стоит отдельно от прод-venv).
sudo mkdir -p /var/log/lms-poligon && sudo chown app:app /var/log/lms-poligon

# 3 базы данных, 3 роли на СУЩЕСТВУЮЩЕМ прод-Postgres-инстансе (5.42.107.253) —
# выполняется С ДОСТУПОМ К ЭТОМУ ИНСТАНСУ (не обязательно с lms-spw-vds — psql
# может подключаться удалённо, если оператор дал доступ; если процедура
# создания БД на проде задокументирована иначе — см. docs/ai/operator-runbook.md).
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "CREATE ROLE poligon_dev_app   LOGIN PASSWORD '<сгенерировать>';"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "CREATE ROLE poligon_test_app  LOGIN PASSWORD '<сгенерировать>';"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "CREATE ROLE poligon_stage_app LOGIN PASSWORD '<сгенерировать>';"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "CREATE DATABASE poligon_dev   OWNER poligon_dev_app;"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "CREATE DATABASE poligon_test  OWNER poligon_test_app;"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "CREATE DATABASE poligon_stage OWNER poligon_stage_app;"
# Пароли — как прод-секреты: python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Ограничить blast radius учебных SQL-упражнений (Г9: DELETE/DDL) на ОБЩЕМ
# Postgres-процессе — обязательно на общем инстансе, необязательно было бы
# на отдельном сервере:
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "ALTER ROLE poligon_dev_app   CONNECTION LIMIT 20;"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "ALTER ROLE poligon_test_app  CONNECTION LIMIT 20;"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "ALTER ROLE poligon_stage_app CONNECTION LIMIT 20;"
# statement_timeout — на уровне БД, не роли (ALTER ROLE ... IN DATABASE ...):
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "ALTER ROLE poligon_dev_app   IN DATABASE poligon_dev   SET statement_timeout = '5s';"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "ALTER ROLE poligon_test_app  IN DATABASE poligon_test  SET statement_timeout = '5s';"
psql "postgresql://<admin>@5.42.107.253:5432/postgres" -c \
  "ALTER ROLE poligon_stage_app IN DATABASE poligon_stage SET statement_timeout = '5s';"

# Redis — переиспользуем УЖЕ работающий на lms-spw-vds Redis (прод LMS db=2),
# полигон занимает db=3/4/5 (dev/test/stage) — НЕ ставим новый Redis-процесс.

# Клонировать репозиторий НА ВЕТКУ poligon (не main!), рядом с прод-чекаутом /opt/lms
sudo -u app git clone --branch poligon https://github.com/vkomlev/LMS.git /opt/lms-poligon
cd /opt/lms-poligon
sudo -u app python3.11 -m venv venv
sudo -u app ./venv/bin/pip install --upgrade -r requirements.txt

# 3 .env-файла — на основе deploy/poligon/.env.lms.<tier>.example
sudo -u app cp deploy/poligon/.env.lms.dev.example   /opt/lms-poligon/.env.dev
sudo -u app cp deploy/poligon/.env.lms.test.example  /opt/lms-poligon/.env.test
sudo -u app cp deploy/poligon/.env.lms.stage.example /opt/lms-poligon/.env.stage
# Заполнить DATABASE_URL (хост 5.42.107.253 + пароли ролей выше) и секреты —
# см. комментарии в файлах
sudo -u app nano /opt/lms-poligon/.env.dev
sudo -u app nano /opt/lms-poligon/.env.test
sudo -u app nano /opt/lms-poligon/.env.stage

# Применить схему на ВСЕ 3 БД (одна и та же Alembic-история)
source venv/bin/activate
DATABASE_URL=$(grep ^DATABASE_URL .env.dev   | cut -d= -f2-) alembic upgrade head
DATABASE_URL=$(grep ^DATABASE_URL .env.test  | cut -d= -f2-) alembic upgrade head
DATABASE_URL=$(grep ^DATABASE_URL .env.stage | cut -d= -f2-) alembic upgrade head
deactivate

# Посев тестовых данных на все 3 БД (идемпотентно). db_write_gate.py совпадёт
# host-сигнатурой с прод (общий Postgres-инстанс) — ожидаемое трение, см. бриф
# раздел 8; safety guard ВНУТРИ poligon_seed.py (allowlist точного имени БД)
# работает независимо от хука.
DBCHECK_OK=1 sudo -u app venv/bin/python scripts/poligon_seed.py --tier dev   --reset
DBCHECK_OK=1 sudo -u app venv/bin/python scripts/poligon_seed.py --tier test  --reset
DBCHECK_OK=1 sudo -u app venv/bin/python scripts/poligon_seed.py --tier stage --reset

# systemd — 3 unit'а, РЯДОМ с прод lms.service (не заменяют его)
for tier in dev test stage; do
  sudo cp deploy/poligon/lms-poligon-$tier.service /etc/systemd/system/
done
sudo systemctl daemon-reload
sudo systemctl enable --now lms-poligon-dev lms-poligon-test lms-poligon-stage
sudo systemctl status lms-poligon-dev lms-poligon-test lms-poligon-stage lms
# ^ последним в списке — прод lms, чтобы явно увидеть, что он не затронут.

# Nginx — новый site-файл РЯДОМ с уже включённым prod lms.conf/spw.conf
sudo cp deploy/poligon/nginx-poligon.conf /etc/nginx/sites-available/poligon.conf
sudo ln -s /etc/nginx/sites-available/poligon.conf /etc/nginx/sites-enabled/poligon.conf
sudo nginx -t && sudo systemctl reload nginx
# nginx -t обязателен ДО reload — общий nginx-процесс обслуживает и прод.

# Certbot — ОТДЕЛЬНЫЙ SAN-сертификат на 6 доменов полигона (НЕ трогаем прод-
# сертификат api.learn.*/learn.* — отдельный `certbot --nginx` вызов, отдельные
# домены в -d, existing прод-cert не переиздаётся)
sudo certbot --nginx \
  -d api-dev-poligon.victor-komlev.ru -d api-test-poligon.victor-komlev.ru \
  -d api-stage-poligon.victor-komlev.ru \
  -d dev-poligon.victor-komlev.ru -d test-poligon.victor-komlev.ru \
  -d stage-poligon.victor-komlev.ru

# Firewall — уже настроен для прода (22/80/443), новых портов наружу не
# открываем (8010-8012/3010-3012 — только127.0.0.1, за nginx).
```

## Повторный деплой (после первичной настройки)

```bash
ssh lms-spw-vds
sudo -u app bash /opt/lms-poligon/deploy/poligon/deploy-lms-poligon.sh
```

Один прогон обновляет код (ветка `poligon`), прогоняет `alembic upgrade head`
на ВСЕХ 3 БД полигона (5.42.107.253, `poligon_*`) и перезапускает все 3
systemd-сервиса полигона — **прод `lms.service` этот скрипт не трогает** (он
живёт в `/opt/lms`, отдельный чекаут, отдельный unit).

**Сброс данных отдельно от деплоя кода** (не делает деплой автоматически):

```bash
DBCHECK_OK=1 sudo -u app venv/bin/python /opt/lms-poligon/scripts/poligon_seed.py --tier test --reset
```

## Откат

```bash
ssh lms-spw-vds
sudo -u app bash /opt/lms-poligon/deploy/poligon/rollback-lms-poligon.sh
```

## Безопасность/изоляция — что проверить после первого деплоя

- `sudo -u postgres psql -h 5.42.107.253 -c "\du"` — 3 новые роли
  (`poligon_dev_app`/`poligon_test_app`/`poligon_stage_app`), КАЖДАЯ видит
  только свою БД (`\l` + `\c learn` под ролью полигона → ожидаем permission denied).
- `systemctl status lms lms-poligon-dev lms-poligon-test lms-poligon-stage` —
  прод `lms` в статусе `active` и НЕ перезапускался (`systemctl show lms
  -p ActiveEnterTimestamp` не изменился после деплоя полигона).
- `curl https://api-stage-poligon.victor-komlev.ru/auth/test/issue-session -X POST`
  → 404 (двойной gate реально работает на stage — `ENV=production` там).
- Ресурсы: `free -h` и `sudo -u postgres psql -h 5.42.107.253 -c
  "SELECT count(*) FROM pg_stat_activity;"` — сверить до/после первого деплоя,
  если растёт нагрузка — см. TODO в брифе (перенос на отдельный VPS).
