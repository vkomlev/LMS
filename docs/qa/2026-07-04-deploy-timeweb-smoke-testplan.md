Тест-план: no-branch_2026-07-04-deploy-timeweb.md
Ветка: main (инфраструктурная задача, не привязана к одному diff)
Репозитории: D:\Work\LMS, D:\Work\spw, D:\Work\TG_LMS

## Затронутые страницы/маршруты
- `https://api.learn.victor-komlev.ru/health` (или аналог) — LMS API поднялся на App Platform,
  отвечает 200, читает БД `learn` из DBaaS.
- `https://learn.victor-komlev.ru` — SPW отдаёт главную страницу, серверный проксирующий вызов
  к `LMS_UPSTREAM_URL` (prod-адрес LMS на App Platform) отрабатывает без CORS-ошибок.
- `https://learn.victor-komlev.ru/auth/vk/callback` — VK ID redirect URI собран под прод-домен.
- WP-embed iframe на `victor-komlej.ru` (тестовая страница) — X-Frame-Options/CSP пропускают
  прод-домен SPW.
- Telegram: 5 ботов (admin/methodist/teacher/student/marketer) отвечают на `/start` в реальном
  Telegram на VDS.

## Ключевые взаимодействия для проверки
- Ученик логинится через magic-link на `learn.victor-komlev.ru`, видит задание, отправляет ответ —
  end-to-end через prod LMS API + prod БД `learn`.
- TG Mini App открывается из Telegram-клиента, авторизуется через initData на prod-домене.
- Любой из 5 TG-ботов переживает `systemctl restart <bot-unit>` и корректно поднимается заново
  (проверка systemd Restart=always).
- `alembic upgrade head` на пустой prod-БД `learn` (DBaaS) — все 33 миграции проходят без ошибок.
- Подключение LMS к Redis (rate-limit/session, db=2) и TG_LMS к Redis (FSM, db=0) — оба сервиса
  используют разные логические БД Redis без коллизий ключей.

## Граничные случаи
- Обрыв сети между App Platform (LMS/SPW) и VDS/DBaaS — приложение должно вернуть понятную
  5xx-ошибку, а не зависнуть; текущая обработка таймаутов (`HTTP_TIMEOUT=5.0` в TG_LMS) должна
  сработать при недоступности LMS API.
- Один из 5 ботов падает (unhandled exception) — systemd поднимает процесс автоматически,
  остальные 4 бота продолжают работать независимо (изоляция процессов).
- Секреты (`MAGIC_LINK_SECRET`, `FERNET_MASTER_KEY`, токены ботов) — проверить, что prod-значения
  отличаются от dev/локальных и нигде не закоммичены (`git log -p` / `git grep` по репозиториям
  перед публикацией).
- CORS: запрос к LMS API с домена, отличного от `learn.victor-komlev.ru` (например, localhost
  разработчика) — должен быть отклонён в prod-конфигурации `CORS_ALLOWED_ORIGINS`.
- Ручной деплой: смержили в main, но забыли нажать "deploy" в панели App Platform — прод должен
  остаться на предыдущей рабочей версии (не частично обновлённое состояние).

## Критические пути
- Полный цикл: DNS/SSL для `api.learn.victor-komlev.ru` и `learn.victor-komlej.ru` → App Platform
  деплой LMS+SPW → `alembic upgrade head` на DBaaS → smoke-логин ученика → TG Mini App открывается.
- Полный цикл: VDS поднят → 5 systemd unit запущены → каждый бот отвечает в Telegram → бот
  успешно вызывает prod LMS API по `API_URL`/`API_KEY`.
- Откат: если prod LMS после деплоя отдаёт 5xx — операторский runbook отката на предыдущую
  версию App Platform (проверить наличие такой функции в панели — если нет, зафиксировать как
  ограничение).
