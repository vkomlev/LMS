[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Локальный триггер деплоя LMS на прод (VPS lms-spw-vds).
# Запускает deploy/vps/deploy.sh на сервере через ssh -tt (pty обязателен —
# see D:\Work\LMS\TODOS.md, Defaults use_pty в sudoers app-deploy).
# Всегда деплоит origin/main HEAD (deploy.sh делает git fetch + reset --hard).

Write-Host "== Деплой LMS (origin/main -> lms-spw-vds:/opt/lms) ==" -ForegroundColor Cyan

ssh -tt lms-spw-vds "sudo -u app bash /opt/lms/deploy/vps/deploy.sh"
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host "`nДеплой LMS завершён успешно." -ForegroundColor Green
} else {
    Write-Host "`nДеплой LMS завершился с ошибкой (код $exitCode). Смотри вывод выше." -ForegroundColor Red
}

exit $exitCode
