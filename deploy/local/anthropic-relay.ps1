﻿# tsk-1376: пересыльщик к API Anthropic и обратный тоннель на lms-spw-vds.
# Запускается Планировщиком заданий при входе оператора. ПК выключен — третье
# (сейчас первое) звено наставника недоступно, LMS переходит к остальным моделям.
# Перед каждым подключением снимает висящий приёмник порта на сервере (после сна ПК
# старая сессия sshd держала 18443 час — «remote port forwarding failed»), пока
# тоннель поднят — раз в минуту проверяет живость насквозь и при двух провалах
# подряд переподключается.
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Root = "D:\Work\LMS"
$Log = Join-Path $Root "logs\anthropic-relay.log"
$SshErr = Join-Path $Root "logs\anthropic-relay-ssh.err"
$Remote = Join-Path $Root "deploy\local\anthropic-relay-remote.sh"
$Port = 18443
function Write-Log($msg) { Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg) -Encoding UTF8 }

# Пересыльщик: один экземпляр на порт; поднимается заново, если процесс умер.
function Confirm-Relay {
    $busy = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $busy) {
        Start-Process -FilePath (Join-Path $Root ".venv\Scripts\python.exe") `
            -ArgumentList "scripts\anthropic_relay.py", "--port", "$Port" `
            -WorkingDirectory $Root -WindowStyle Hidden
        Write-Log "пересыльщик запущен"
    }
}

# Серверная часть (free | probe): скрипт уходит в ssh через stdin, на сервере не хранится.
function Invoke-Remote($mode) {
    $script = (Get-Content -Raw -Encoding UTF8 $Remote) -replace "`r", ""
    $out = $script | & ssh.exe -o BatchMode=yes -o ConnectTimeout=10 lms-spw-vds "bash -s -- $mode" 2>$null
    return (($out | Out-String).Trim())
}

Confirm-Relay
while ($true) {
    Write-Log ("сервер: {0}" -f (Invoke-Remote "free"))
    Write-Log "тоннель: подключаюсь"
    $ssh = Start-Process -FilePath "ssh.exe" -PassThru -WindowStyle Hidden -RedirectStandardError $SshErr `
        -ArgumentList "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", `
            "-o", "BatchMode=yes", "-R", "127.0.0.1:${Port}:127.0.0.1:${Port}", "lms-spw-vds"
    $null = $ssh.Handle  # без этого PowerShell 5.1 теряет ExitCode завершившегося процесса
    $fails = 0
    while (-not $ssh.HasExited) {
        Start-Sleep -Seconds 60
        if ($ssh.HasExited) { break }
        Confirm-Relay
        $code = Invoke-Remote "probe"
        if ($code -match '^[1-5]\d\d$') {
            $fails = 0
        } else {
            $fails++
            Write-Log ("живость: нет ответа через тоннель ({0}), провал {1} из 2" -f $code, $fails)
            if ($fails -ge 2) { Stop-Process -Id $ssh.Id -Force -ErrorAction SilentlyContinue; break }
        }
    }
    $ssh.WaitForExit()
    $err = (Get-Content -Raw -Encoding UTF8 $SshErr -ErrorAction SilentlyContinue)
    if ($err) { Write-Log ("ssh: {0}" -f $err.Trim()) }
    Write-Log ("тоннель: оборвался, код {0}; повтор через 30 c" -f $ssh.ExitCode)
    Start-Sleep -Seconds 30
}
