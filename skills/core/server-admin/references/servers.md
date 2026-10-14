# Исторический снимок серверов — 2026-09-23

Источник: Claude `server-admin/references/servers.md`, прочитан 2026-09-23, **без подключения
к серверам**. Роли, IP, версии и доступ не проверены live. До использования сверить задачу,
SSH-конфиг и актуальное состояние; новые факты фиксировать в датированном инвентаре проекта.

| Алиас | Адрес из снимка | Указанная роль | Проектные сведения |
|---|---|---|---|
| `lms-spw-vds` | `5.42.102.20` | Ubuntu 22.04; LMS API, SPW, nginx, учебный полигон | `D:/Work/LMS/deploy/vps/`, `D:/Work/spw/deploy/vps/`; tsk-005, tsk-182 |
| `tg-lms-vds` | `72.56.247.22` | Ubuntu 22.04; боты TG_LMS/Root, Redis, OpsNotifyBot; исполнитель CB под `cb` | `D:/Work/TG_LMS/deploy/vps/`; tsk-1085, ADR-0056 CB |
| `vk-wp-vps` | `201.24.63.218` | Ubuntu 24.04; WordPress `victor-komlev.ru`, nginx/PHP-FPM/MariaDB | tsk-1083; `D:/Work/ContentBackbone/docs/specs/2026-09-23-vps-migration-victor-komlev-ru.md` |
| `vk-wp-old` | shared hosting Timeweb | Источник переноса WordPress и предполагаемый путь отката | tsk-1083; срок отката проверить |

В снимке CB имел `MemoryMax=768M`, swap 2 ГБ; Telegram Bot API с `tg-lms-vds` был нестабилен.
Это исторические наблюдения, не текущий диагноз.

Пути боевых конфигов: `/opt/lms/.env`, `/opt/spw/.env`, `/opt/tg_lms/.env`, `/opt/root/.env`,
`/opt/cb/.env`, полигонные `.env.*`, WordPress `wp-config.php`, `/etc/msmtprc`, `/root/.my.cnf`.
До записи применять актуальный ownership-протокол. Значений секретов здесь нет.

Вне VPS упоминались DBaaS Postgres (`learn`, `content_backbone`) и Valkey. Контур и операции
проверять по актуальным проектным инструкциям и `db-check`. Не переносить предположение,
что вход всегда root, пользователь всегда app, SSH всегда на 22 или ограничения Timeweb
остались прежними.
