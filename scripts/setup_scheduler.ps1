# PowerShell script para crear tarea programada de Windows
# Ejecutar como Administrator

$taskName = "TeChain Paper Trader Daily"
$taskDescription = "Ejecuta paper trader automaticamente cada dia a las 08:00"
$scriptPath = "c:\proyectos\techain_ia\run_paper_trader_scheduled.bat"
$time = "08:00"

# Crear accion
$action = New-ScheduledTaskAction -Execute $scriptPath

# Crear trigger (diario a las 08:00)
$trigger = New-ScheduledTaskTrigger -Daily -At $time

# Crear principal (ejecutar con permisos del usuario actual)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

# Crear settings (evitar suspender si la tarea toma mucho tiempo)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# Registrar tarea
Register-ScheduledTask -TaskName $taskName `
                       -Action $action `
                       -Trigger $trigger `
                       -Principal $principal `
                       -Settings $settings `
                       -Description $taskDescription `
                       -Force

Write-Host "OK - Tarea programada creada: $taskName"
Write-Host "Horario: Diariamente a las $time"
Write-Host "Script: $scriptPath"
