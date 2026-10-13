param([string]$Python = 'python', [string]$Destination = "$PSScriptRoot\dist")
$ErrorActionPreference = 'Stop'
Get-Command ffmpeg -ErrorAction Stop | Out-Null
Get-Command ffprobe -ErrorAction Stop | Out-Null
& $Python -m PyInstaller --noconfirm --distpath $Destination --workpath "$PSScriptRoot\build" "$PSScriptRoot\СжатьВидео.spec"
if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки PyInstaller' }
