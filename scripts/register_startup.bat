@echo off
REM Registra el servicio para ejecutarse al arranque del sistema
REM Ejecutar como Administrator

echo Registrando Techain-IA para ejecutar al arranque...

REM Opcion 1: Usar Task Scheduler (metodo preferido)
schtasks /create /tn "Techain-IA Startup" /tr "c:\proyectos\techain_ia\start_all_services.bat" /sc onstart /f /rl highest 2>nul

if %errorlevel% equ 0 (
    echo OK - Tarea registrada en Task Scheduler
    echo La aplicacion se iniciara automaticamente al arranque del sistema
) else (
    echo Metodo alternativo: Usando registro de Windows...
    REM Opcion 2: Agregar al registro (startup folder)
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "TeChain-IA" /t REG_SZ /d "c:\proyectos\techain_ia\start_all_services.bat" /f
    if %errorlevel% equ 0 (
        echo OK - Entrada agregada al registro
    ) else (
        echo Error: No se pudo registrar. Ejecuta como Administrator.
    )
)

pause
