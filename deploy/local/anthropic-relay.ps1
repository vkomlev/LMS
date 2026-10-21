# tsk-1376: пересыльщик к API Anthropic и обратный тоннель на lms-spw-vds.
# Запускается Планировщиком заданий при входе оператора. ПК выключен — третье
# (сейчас первое) звено наставника недоступно, LMS переходит к остальным моделям.
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = "D:\Work\LMS"
$Log = Join-Path $Root "logs\anthropic-relay.log"
function Write-Log($msg) { Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg) -Encoding UTF8 }

# Пересыльщик: один экземпляр на порт 18443.
$busy = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18443 -State Listen -ErrorAction SilentlyContinue
if (-not $busy) {
    Start-Process -FilePath (Join-Path $Root ".venv\Scripts\python.exe") `
        -ArgumentList "scripts\anthropic_relay.py", "--port", "18443" `
        -WorkingDirectory $Root -WindowStyle Hidden
    Write-Log "пересыльщик запущен"
} else {
    Write-Log "пересыльщик уже слушает 18443"
}

# Тоннель: переподключение при обрыве.
while ($true) {
    Write-Log "тоннель: подключаюсь"
    & ssh.exe -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
        -o BatchMode=yes -R 127.0.0.1:18443:127.0.0.1:18443 lms-spw-vds 2>> $Log
    Write-Log ("тоннель: оборвался, код {0}; повтор через 30 c" -f $LASTEXITCODE)
    Start-Sleep -Seconds 30
}
