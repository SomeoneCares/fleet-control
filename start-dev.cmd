@echo off
rem Start Fleet Control for local development (current, updated or both). See scripts\start-dev.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" %*
