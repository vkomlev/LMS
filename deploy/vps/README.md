# Резервный план: LMS на голом VPS (без App Platform)

Готовится про запас на случай, если App Platform Timeweb Cloud не удастся починить
(tsk-005, см. `docs/ai/operator-runbook.md` R-006). Не применять без явного решения
перейти на этот план — пока актуальный контур LMS работает через App Platform +
балансировщик.

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

# systemd
sudo cp deploy/vps/lms.service /etc/systemd/system/lms.service
sudo systemctl daemon-reload
sudo systemctl enable --now lms
sudo systemctl status lms

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
