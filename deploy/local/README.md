# Локальный триггер деплоя (Windows)

Быстрый способ задеплоить LMS на прод одной командой, без ручного SSH и без
диалога с Claude Code — `deploy-lms.ps1` просто вызывает уже существующий
`deploy/vps/deploy.sh` на сервере через `ssh -tt lms-spw-vds`.

## Запуск

- Двойной клик по `deploy-lms.cmd` — открывает консоль, показывает прогресс,
  ждёт нажатия клавиши перед закрытием.
- Или из PowerShell: `.\deploy\local\deploy-lms.ps1`

## Предпосылки

- SSH-алиас `lms-spw-vds` уже настроен в `~/.ssh/config` (использовался всю
  сессию 2026-07-07 без проблем).
- Деплоит всегда `origin/main` — закоммить и запуш изменения ДО запуска.
- Требует pty (`ssh -tt`) — сервер настроен на `Defaults use_pty` в sudoers,
  без `-tt` `sudo systemctl restart lms` падает без пароля (см. `TODOS.md`,
  запись про `deploy.sh`/`rollback.sh`).

## Откат

Пока без отдельного локального триггера — `rollback.sh` уже есть на сервере,
откат вручную:

```
ssh lms-spw-vds
sudo -u app bash /opt/lms/deploy/vps/rollback.sh
```

Скажи, если нужен такой же локальный wrapper для отката — делается по той же схеме.
