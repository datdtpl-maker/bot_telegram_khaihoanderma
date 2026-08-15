Set WshShell = CreateObject("WScript.Shell")
currentDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir
WshShell.Run "powershell.exe -WindowStyle Hidden -NoLogo -NoProfile -ExecutionPolicy Bypass -File """ & currentDir & "\run_telegram_bot.ps1""", 0, False
