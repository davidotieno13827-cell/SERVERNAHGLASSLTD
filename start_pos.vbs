Option Explicit

Dim shell, fileSystem, projectFolder, launcherPath
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcherPath = projectFolder & "\start_pos.bat"

If Not fileSystem.FileExists(launcherPath) Then
    MsgBox "POS launcher not found: " & launcherPath, vbExclamation, "Savannah Glassmart"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectFolder
shell.Run Chr(34) & launcherPath & Chr(34), 0, False