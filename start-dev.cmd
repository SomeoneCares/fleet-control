@echo off
rem Start Fleet Studio for local development (current, updated or both). See scripts\start-dev.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" %*
