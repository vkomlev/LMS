<#
.SYNOPSIS
    Проверить, что локальный Redis отвечает, и поднять его, если он не запущен.

.DESCRIPTION
    tsk-611. Часть тестов LMS требует живого Redis (гостевые сессии Y-5, лимит
    демо-заданий, окно благодати на ротацию refresh-токена). На машине оператора
    установлен Memurai — Redis-совместимый сервер под Windows. Его бесплатная
    Developer-редакция сама себя выключает после нескольких суток непрерывной
    работы («Memurai Developer Edition automatic shutdown» в memurai-log.txt),
    после чего служба остаётся в состоянии «Остановлена». Именно это и дало
    26 непрозрачных падений прогона 14.08.

    Скрипт идемпотентный: если Redis уже отвечает на PING — не делает ничего.
    Иначе поднимает его, пробуя по порядку:
      1) службу Memurai (сработает, если у сессии есть права администратора);
      2) memurai.exe как обычный процесс пользователя (прав администратора
         не требует, рабочий каталог — %LOCALAPPDATA%\MemuraiDev).

.PARAMETER RedisHost
    Хост Redis. По умолчанию берётся из переменной REDIS_URL, иначе 127.0.0.1.

.PARAMETER Port
    Порт Redis. По умолчанию берётся из REDIS_URL, иначе 6379.

.PARAMETER TimeoutSec
    Сколько ждать ответа PING после запуска. По умолчанию 20 секунд.

.PARAMETER Quiet
    Печатать только ошибки (для запуска из планировщика задач).

.OUTPUTS
    Код возврата 0 — Redis отвечает. Код 1 — поднять не удалось.

.EXAMPLE
    .\scripts\dev\ensure-redis.ps1
#>
[CmdletBinding()]
param(
    [string]$RedisHost,
    [int]$Port,
    [int]$TimeoutSec = 20,
    [switch]$Quiet
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$MemuraiExe = 'C:\Program Files\Memurai\memurai.exe'

function Write-Info {
    param([string]$Text)
    if (-not $Quiet) { Write-Host $Text }
}

function Write-Err {
    <# Ошибка без стека PowerShell: оператору нужен текст, а не разбор вызовов. #>
    param([string]$Text)
    Write-Host "ОШИБКА: $Text" -ForegroundColor Red
}

function Get-RedisTarget {
    <#
        Хост и порт: явные параметры важнее REDIS_URL, REDIS_URL важнее дефолта.
        Разбирается форма redis://[user:pass@]host:port[/db].
    #>
    $resultHost = '127.0.0.1'
    $resultPort = 6379

    $url = $env:REDIS_URL
    if ($url) {
        try {
            $withoutScheme = $url -replace '^[a-zA-Z]+://', ''
            $authority = ($withoutScheme -split '/')[0]
            if ($authority -like '*@*') { $authority = $authority.Substring($authority.LastIndexOf('@') + 1) }
            $parts = $authority -split ':'
            if ($parts[0]) { $resultHost = $parts[0] }
            if ($parts.Count -gt 1 -and $parts[1] -match '^\d+$') { $resultPort = [int]$parts[1] }
        } catch {
            Write-Warning "Не удалось разобрать REDIS_URL ('$url'), беру 127.0.0.1:6379"
        }
    }

    if ($RedisHost) { $resultHost = $RedisHost }
    if ($Port -gt 0) { $resultPort = $Port }

    return [pscustomobject]@{ RedisHost = $resultHost; Port = $resultPort }
}

function Test-RedisPing {
    <#
        Живой ли сервер: открыть TCP-соединение и отправить команду PING.
        Ответ '+PONG' — жив; '-NOAUTH' — тоже жив, просто требует пароль.
        Проверка сокетом, а не через memurai-cli: так же работает и для Redis
        в контейнере или WSL.
    #>
    param(
        [string]$TargetHost,
        [int]$TargetPort,
        [int]$TimeoutMs = 1000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($TargetHost, $TargetPort, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($connect)

        $stream = $client.GetStream()
        $stream.ReadTimeout = $TimeoutMs
        $stream.WriteTimeout = $TimeoutMs
        $payload = [System.Text.Encoding]::ASCII.GetBytes("PING`r`n")
        $stream.Write($payload, 0, $payload.Length)

        $buffer = New-Object byte[] 32
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -le 0) { return $false }
        $answer = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)
        return ($answer.StartsWith('+PONG') -or $answer.StartsWith('-NOAUTH'))
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Start-MemuraiService {
    <# Служба Memurai: сработает только при правах администратора. #>
    $service = Get-Service -Name 'Memurai' -ErrorAction SilentlyContinue
    if (-not $service) { return $false }
    if ($service.Status -eq 'Running') { return $true }
    try {
        Start-Service -Name 'Memurai' -ErrorAction Stop
        Write-Info 'Служба Memurai запущена.'
        return $true
    } catch {
        Write-Info 'Служба Memurai есть, но запустить её не вышло (нужны права администратора) — поднимаю процессом пользователя.'
        return $false
    }
}

function Start-MemuraiProcess {
    <# memurai.exe обычным процессом пользователя, без прав администратора. #>
    param([int]$TargetPort)

    if (-not (Test-Path -LiteralPath $MemuraiExe)) { return $false }

    $workDir = Join-Path $env:LOCALAPPDATA 'MemuraiDev'
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null

    Start-Process -FilePath $MemuraiExe -WindowStyle Hidden -ArgumentList @(
        '--port', "$TargetPort",
        '--bind', '127.0.0.1',
        '--dir', $workDir,
        '--logfile', 'memurai-dev.log'
    )
    Write-Info "Memurai запущен процессом пользователя (данные и лог: $workDir)."
    return $true
}

# --- основной ход ---------------------------------------------------------

$target = Get-RedisTarget
$targetHost = $target.RedisHost
$targetPort = $target.Port

if (Test-RedisPing -TargetHost $targetHost -TargetPort $targetPort) {
    Write-Info "Redis уже отвечает на ${targetHost}:${targetPort} — ничего делать не нужно."
    exit 0
}

if ($targetHost -notin @('127.0.0.1', 'localhost', '::1')) {
    Write-Err "Redis на ${targetHost}:${targetPort} не отвечает. Это не локальный адрес — поднять его отсюда нельзя, проверь REDIS_URL и доступность узла."
    exit 1
}

Write-Info "Redis на ${targetHost}:${targetPort} не отвечает, поднимаю."

$started = Start-MemuraiService
if (-not $started) { $started = Start-MemuraiProcess -TargetPort $targetPort }

if (-not $started) {
    Write-Err @"
Поднять Redis нечем: службы Memurai нет и файла '$MemuraiExe' тоже.
Варианты: установить Memurai (https://www.memurai.com/get-memurai) либо поднять
контейнер: docker run -d --name lms-redis --restart unless-stopped -p ${targetPort}:6379 redis:7
"@
    exit 1
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 300
    if (Test-RedisPing -TargetHost $targetHost -TargetPort $targetPort) {
        Write-Info "Redis отвечает на ${targetHost}:${targetPort}."
        exit 0
    }
}

Write-Err "Redis не ответил на ${targetHost}:${targetPort} за $TimeoutSec с. Лог: $(Join-Path $env:LOCALAPPDATA 'MemuraiDev\memurai-dev.log')"
exit 1
