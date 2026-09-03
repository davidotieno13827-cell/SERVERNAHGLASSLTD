Option Explicit

Dim shell, fileSystem, projectFolder, pythonPath
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonPath = projectFolder & "\.venv\Scripts\pythonw.exe"

If Not fileSystem.FileExists(pythonPath) Then
    MsgBox "POS environment not found. Run the setup commands in README.md first.", vbExclamation, "Savannah Glassmart"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectFolder
shell.Run Chr(34) & pythonPath & Chr(34) & " run.py", 0, False
WScript.Sleep 2500
shell.Run "http://127.0.0.1:5000", 1, False