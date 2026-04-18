' VBScript para crear tareas programadas de forma confiable
' Ejecutar como Administrator

Dim objScheduler, objRootFolder
Set objScheduler = CreateObject("Schedule.Service")
Call objScheduler.Connect()
Set objRootFolder = objScheduler.GetFolder("\")

' ========== TAREA 1: TRADER DIARIO A LAS 08:00 ==========
Dim objTask1, objAction1, objTrigger1
Set objTask1 = objScheduler.NewTask(0)

objTask1.RegistrationInfo.Description = "Ejecuta Paper Trader diariamente a las 08:00"

Set objAction1 = objTask1.Actions.Create(0)
objAction1.Path = "c:\proyectos\techain_ia\run_paper_trader_scheduled.bat"
objAction1.WorkingDirectory = "c:\proyectos\techain_ia"

Set objTrigger1 = objTask1.Triggers.Create(2)  ' 2 = Daily trigger
objTrigger1.DaysInterval = 1
objTrigger1.StartBoundary = "2026-04-07T08:00:00"
objTrigger1.Enabled = True

objTask1.Principal.Id = "Principal1"
objTask1.Principal.UserId = "SYSTEM"
objTask1.Principal.RunLevel = 1  ' Highest

objTask1.Settings.AllowDemandStart = True
objTask1.Settings.Enabled = True
objTask1.Settings.StartWhenAvailable = True
objTask1.Settings.AllowHardTerminate = True

objRootFolder.RegisterTaskDefinition "Techain Paper Trader 08AM", objTask1, 6, , , 5

WScript.Echo "OK - Tarea 1: Trader diario a las 08:00"

' ========== TAREA 2: WATCHDOG AL ARRANQUE ==========
Dim objTask2, objAction2, objTrigger2
Set objTask2 = objScheduler.NewTask(0)

objTask2.RegistrationInfo.Description = "Inicia Watchdog Service al arranque"

Set objAction2 = objTask2.Actions.Create(0)
objAction2.Path = "c:\windows\system32\cmd.exe"
objAction2.Arguments = "/c cd c:\proyectos\techain_ia && python apps\watchdog_service.py"

Set objTrigger2 = objTask2.Triggers.Create(8)  ' 8 = AtStartup trigger
objTrigger2.Enabled = True

objTask2.Principal.Id = "Principal1"
objTask2.Principal.UserId = "SYSTEM"
objTask2.Principal.RunLevel = 1

objTask2.Settings.AllowDemandStart = True
objTask2.Settings.Enabled = True
objTask2.Settings.StartWhenAvailable = True

objRootFolder.RegisterTaskDefinition "Techain Watchdog Startup", objTask2, 6, , , 5

WScript.Echo "OK - Tarea 2: Watchdog al arranque"
WScript.Echo ""
WScript.Echo "Ambas tareas creadas correctamente."
