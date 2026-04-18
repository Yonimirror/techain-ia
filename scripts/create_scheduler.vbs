' VBScript para crear tarea programada de Windows
' Guarda este archivo y ejecuta: cscript create_scheduler.vbs

Dim objScheduler, objTask, objAction, objTrigger
Set objScheduler = CreateObject("Schedule.Service")
Call objScheduler.Connect()

Dim objRootFolder
Set objRootFolder = objScheduler.GetFolder("\")

Dim strTaskPath, objExistingTask
strTaskPath = "\TeChain Paper Trader Daily"

' Intenta eliminar si existe
On Error Resume Next
Call objRootFolder.DeleteTask(strTaskPath, 0)
On Error Goto 0

Dim objNewTask, objTaskDef
Set objNewTask = objScheduler.NewTask(0)
Set objTaskDef = objNewTask

objTaskDef.RegistrationInfo.Description = "Ejecuta paper trader automaticamente cada dia a las 08:00"

' Crear Accion
Dim objActionDef
Set objActionDef = objTaskDef.Actions.Create(0)
objActionDef.Path = "c:\proyectos\techain_ia\run_paper_trader_scheduled.bat"
objActionDef.WorkingDirectory = "c:\proyectos\techain_ia"

' Crear Trigger
Dim objTriggerDef
Set objTriggerDef = objTaskDef.Triggers.Create(1)
objTriggerDef.StartBoundary = "2026-04-06T08:00:00"
objTriggerDef.Enabled = True

' Crear Principal
objTaskDef.Principal.Id = "Principal1"
objTaskDef.Principal.UserId = ""
objTaskDef.Principal.LogonType = 3

' Configuracion
objTaskDef.Settings.AllowDemandStart = True
objTaskDef.Settings.AllowHardTerminate = True
objTaskDef.Settings.Enabled = True

' Registrar tarea
Const TASK_CREATE = 2
Const TASK_UPDATE = 4
Dim objFolder, objRegisteredTask
Set objFolder = objScheduler.GetFolder("\")
Set objRegisteredTask = objFolder.RegisterTaskDefinition("TeChain Paper Trader Daily", objTaskDef, 6, , , 3)

WScript.Echo "OK - Tarea creada: TeChain Paper Trader Daily"
