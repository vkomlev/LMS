# Локальный триггер деплоя LMS на прод (VPS lms-spw-vds).
# Запускает deploy/vps/deploy.sh на сервере через ssh -tt (pty обязателен —
# see D:\Work\LMS\TODOS.md, Defaults use_pty в sudoers app-deploy).
# Всегда деплоит origin/main HEAD (deploy.sh делает git fetch + reset --hard).
#
# -HoldOk — осознанно выкатить, даже если в ветке есть работа, помеченная как
# ждущая решения оператора (строка Hold-For-Operator в теле коммита, tsk-672).
# Без флага такой выкат останавливается с кодом 2 и ничего не трогает.

param(
    [switch]$HoldOk
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$remoteCmd = if ($HoldOk) {
    "sudo -u app env DEPLOY_HOLD_OK=1 bash /opt/lms/deploy/vps/deploy.sh"
} else {
    "sudo -u app bash /opt/lms/deploy/vps/deploy.sh"
}

Write-Host "== Деплой LMS (origin/main -> lms-spw-vds:/opt/lms) ==" -ForegroundColor Cyan
if ($HoldOk) {
    Write-Host "Пометки «ждёт решения оператора» проигнорированы (-HoldOk)." -ForegroundColor Yellow
}

ssh -tt lms-spw-vds $remoteCmd
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host "`nДеплой LMS завершён успешно." -ForegroundColor Green
} elseif ($exitCode -eq 2) {
    Write-Host "`nДеплой остановлен: в ветке есть работа, ждущая решения оператора (см. вывод выше)." -ForegroundColor Yellow
    Write-Host "Прод не тронут. Решение получено — повторить с флагом -HoldOk." -ForegroundColor Yellow
} else {
    Write-Host "`nДеплой LMS завершился с ошибкой (код $exitCode). Смотри вывод выше." -ForegroundColor Red
}

exit $exitCode
