Option Explicit
Dim shell, fs, folder, python, script
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
script = folder & "\overlay.py"
If Not fs.FileExists(python) Then
  MsgBox "Run Install.cmd first (Python 3.11 x64 required).", 48, "Mumble RU"
  WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
