# Creates a "Live Subs" shortcut on the desktop that starts without the black console window.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $dir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Host "Run install.bat first."; exit 1 }
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "Live Subs.lnk"))
$lnk.TargetPath = $pyw
$lnk.Arguments = '"' + (Join-Path $dir "live_subs.py") + '" --translator claude --claude-model claude-haiku-5-5'
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = (Join-Path $dir "icon.ico") + ",0"
$lnk.Description = "Live Russian subtitles and translation"
$lnk.Save()
Write-Host "Created: $desktop\Live Subs.lnk"
