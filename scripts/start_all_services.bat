@echo off
REM Script para iniciar todos los servicios de Techain-IA
REM Debe ejecutarse al arranque del sistema

setlocal enabledelayedexpansion

cd /d c:\proyectos\techain_ia

REM Log de ejecucion
set TIMESTAMP=%date:~-4,4%-%date:~-10,2%-%date:~-7,2% %time:~0,2%:%time:~3,2%:%time:~6,2%
echo [%TIMESTAMP%] Iniciando servicios de Techain-IA >> logs\startup.log

REM Iniciar watchdog (monitorea trader + dashboard)
echo [%TIMESTAMP%] Iniciando Watchdog... >> logs\startup.log
start "Techain Watchdog" python apps\watchdog_service.py
ping localhost -n 3 > nul

echo [%TIMESTAMP%] Servicios iniciados >> logs\startup.log
echo Techain-IA services started at %TIMESTAMP%
