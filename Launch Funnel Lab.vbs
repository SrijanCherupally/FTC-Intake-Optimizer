Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
python = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
shell.CurrentDirectory = folder
If fs.FileExists(python) Then
  shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & folder & "\app.py" & Chr(34), 1, False
Else
  MsgBox "Run app.py with Python after installing requirements.txt. See README.md.", 64, "Funnel Lab"
End If
