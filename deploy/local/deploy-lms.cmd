@echo off
chcp 65001 >nul
REM Аргументы пробрасываются в ps1: deploy-lms.cmd -HoldOk (см. tsk-672).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-lms.ps1" %*
echo.
pause
