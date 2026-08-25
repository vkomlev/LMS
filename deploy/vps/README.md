# LMS на VPS (Timeweb Cloud)

Актуальная production-конфигурация с 2026-07-05 (tsk-005) — связка App Platform +
балансировщик оказалась официально неподдерживаемой на стороне Timeweb (обязателен
балансировщик для Let's Encrypt на кастомный домен, а App Platform+LB несовместимы).
История разбора — `docs/ai/operator-runbook.md` записи R-005/R-006. App Platform-
приложение и балансировщики временно не удалены (путь отката), но НЕ обслуживают прод-трафик.

Быстрый локальный триггер деплоя с Windows-машины оператора — `../local/deploy-lms.ps1`
(или двойной клик `../local/deploy-lms.cmd`) — без ручного SSH.

## Предпосылки

- Отдельный VPS (не тот, что для ботов TG_LMS — там не хватит ресурсов на троих).
  Рекомендуемый тариф: 2 vCPU / 4 ГБ RAM, Ubuntu 22.04.
- DNS: A-запись `api.learn.victor-komlev.ru` → IP этого VPS (когда решим переключаться —
  поменять с текущего IP балансировщика на IP этого сервера).

## Первичная настройка сервера (один раз)

```bash
# Базовые пакеты
sudo apt update && sudo apt install -y python3.11 python3.11-venv nginx certbot python3-certbot-nginx git

# Пользователь для приложения
sudo useradd --system --create-home --shell /bin/bash app
sudo mkdir -p /var/log/lms && sudo chown app:app /var/log/lms

# Клонировать репозиторий
sudo -u app git clone https://github.com/vkomlev/LMS.git /opt/lms
cd /opt/lms
sudo -u app python3.11 -m venv venv
sudo -u app ./venv/bin/pip install --upgrade -r requirements.txt

# .env — создать вручную на сервере (НЕ коммитить), взять секреты из панели
# App Platform (DATABASE_URL с +asyncpg, REDIS_URL, MAGIC_LINK_SECRET и т.д.)
sudo -u app nano /opt/lms/.env
# Обязательно: PUBLIC_BASE_URL=https://learn.victor-komlev.ru
#              CORS_ALLOWED_ORIGINS=https://learn.victor-komlev.ru
#              ENV=production, COOKIE_SECURE=true

# systemd (+ socket activation, tsk-403: zero-downtime рестарт, без 502 при деплое)
sudo cp deploy/vps/lms.service /etc/systemd/system/lms.service
sudo cp deploy/vps/lms.socket  /etc/systemd/system/lms.socket
sudo mkdir -p /etc/systemd/system/lms.service.d
sudo cp deploy/vps/lms.service.d/socket.conf /etc/systemd/system/lms.service.d/socket.conf
sudo systemctl daemon-reload
sudo systemctl enable --now lms.socket   # держит :8000 через рестарты сервиса
sudo systemctl enable --now lms
sudo systemctl status lms.socket lms

# Nginx (сначала без SSL-блока, чтобы certbot смог пройти ACME-challenge)
sudo cp deploy/vps/nginx-lms.conf /etc/nginx/sites-available/lms.conf
sudo ln -s /etc/nginx/sites-available/lms.conf /etc/nginx/sites-enabled/lms.conf
sudo mkdir -p /var/www/certbot
sudo nginx -t && sudo systemctl reload nginx

# Certbot — выпустить сертификат (после того как DNS уже указывает на этот сервер)
sudo certbot --nginx -d api.learn.victor-komlev.ru

# Firewall
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Повторный деплой (после первичной настройки)

```bash
ssh <user>@<vps-ip>
sudo -u app bash /opt/lms/deploy/vps/deploy.sh
```

## Работа, которая ждёт решения оператора (tsk-672)

Деплой делает `reset --hard origin/main`: на прод уезжает **вся ветка**, а не
работа выкатывающего. Несколько агентов пишут в одну ветку, поэтому «я свою
правку не выкатывал» не значит «её нет на проде» — 25.08 неодобренная работа
уехала прицепом с чужим деплоем.

**Придерживаешь свою работу до ответа оператора** — поставь в тело своего
коммита строку:

```
feat: tsk-NNN - краткое описание

Hold-For-Operator: что именно должен решить оператор
```

Тогда любой деплой (свой или чужой) остановится **до** `reset`, покажет эту
строку и выйдет с кодом 2. Прод при этом не тронут.

Ключ ищется в сообщении коммита целиком, поэтому не упоминай `Hold-For-Operator`
в коммитах, которые ничего не придерживают — иначе остановишь чужой выкат зря.

Первое средство всё же другое: если работу можно завести под рубильником
(`AI_TUTOR_ENABLED`, `code_review_cron_enabled` — env-переменные в `/opt/lms/.env`),
выкатывай её **выключенной**. Тогда включение — правка `.env` + рестарт, без
деплоя, и пометка не нужна. Пометка — для того, что рубильником не закрыть
(схема API, миграция, поведение экрана).

**Решение получено, выкатываем:**

```bash
sudo -u app env DEPLOY_HOLD_OK=1 bash /opt/lms/deploy/vps/deploy.sh
```

С машины оператора — тот же смысл: `deploy/local/deploy-lms.ps1 -HoldOk`.

Гейт проверяется тестом (временные репозитории, сервер не нужен):

```bash
bash deploy/vps/test-hold-gate.sh
```

## Откат

Быстрый откат к версии, которая работала до последнего `deploy.sh` (SHA сохраняется
скриптом деплоя в `.last-deploy-sha` перед каждым обновлением):

```bash
ssh <user>@<vps-ip>
sudo -u app bash /opt/lms/deploy/vps/rollback.sh
```

**Важно:** `rollback.sh` откатывает только код (git + зависимости + рестарт сервиса).
Alembic-миграции не откатывает — если последний деплой добавил миграцию, `alembic downgrade`
нужно запускать вручную и осознанно (потенциально деструктивно для данных учеников).

Откат дальше, чем на один деплой назад (`.last-deploy-sha` хранит только одну предыдущую
версию) — вручную:

```bash
cd /opt/lms && sudo -u app git log --oneline -10   # найти нужный коммит
sudo -u app git reset --hard <commit>
sudo systemctl restart lms
```
