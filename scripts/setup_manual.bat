@echo off
REM EJECUTA ESTO UNA VEZ COMO ADMINISTRATOR
REM Te permitira agregar Techain-IA al startup automático

echo.
echo ========================================
echo TECHAIN-IA SETUP - Registro en Startup
echo ========================================
echo.

echo Opcion 1: Usar Task Scheduler (recomendado)
echo.
echo Debes crear MANUALMENTE dos tareas en Windows Task Scheduler:
echo.
echo TAREA 1 - Trader diario
echo   Nombre: "Techain Paper Trader 08AM"
echo   Desencadenador: Diariamente a las 08:00
echo   Accion: c:\proyectos\techain_ia\run_paper_trader_scheduled.bat
echo.
echo TAREA 2 - Watchdog al arranque
echo   Nombre: "Techain Watchdog Startup"
echo   Desencadenador: Al arrancar el sistema
echo   Accion: python c:\proyectos\techain_ia\apps\watchdog_service.py
echo.
echo ---
echo.
echo Opcion 2: Linea de comando (si tienes acceso admin en CMD)
echo.
echo schtasks /create /tn "Techain Paper Trader 08AM" /tr "c:\proyectos\techain_ia\run_paper_trader_scheduled.bat" /sc daily /st 08:00 /rl highest /f
echo.
echo schtasks /create /tn "Techain Watchdog Startup" /tr "python c:\proyectos\techain_ia\apps\watchdog_service.py" /sc onstart /rl highest /f
echo.
echo.
pause
