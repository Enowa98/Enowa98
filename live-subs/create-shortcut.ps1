# Creates a "Live Subs" shortcut on the desktop that starts without the black console window.
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $dir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Host "Run install.bat first."; exit 1 }
$desktop = [Environment]::GetFolderPath("Desktop")

# The Windows shortcut API can't save into folders with non-English names (e.g. OneDrive\桌面),
# so build the shortcut in this folder first, then move it to the desktop.
$tmp = Join-Path $dir "Live Subs.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($tmp)
$lnk.TargetPath = $pyw
$lnk.Arguments = '"' + (Join-Path $dir "live_subs.py") + '" --translator claude --claude-model claude-haiku-5-5'
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = (Join-Path $dir "icon.ico") + ",0"
$lnk.Description = "Live Russian subtitles and translation"
$lnk.Save()

$dest = Join-Path $desktop "Live Subs.lnk"
Move-Item -LiteralPath $tmp -Destination $dest -Force
Write-Host "Created: $dest"
