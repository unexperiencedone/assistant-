# Builds dist\Nova\ (Nova.exe + nova-cli.exe). Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File C:\Assisstant\packaging\build.ps1
param([switch]$SkipCanvas)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipCanvas) {
    Write-Host "== canvas"
    Push-Location canvas
    if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund }
    npm run build
    if (-not $?) { throw "canvas build failed" }
    Pop-Location
}

Write-Host "== icon"
python -m assistant.ui.icon

Write-Host "== pyinstaller"
python -m PyInstaller packaging\nova.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Editable settings live next to the executables, not inside _internal.
$config = Join-Path $root "dist\Nova\config.toml"
Copy-Item config.toml $config -Force
$text = (Get-Content $config -Raw) -replace '(?m)^workspace = "\."', 'workspace = "~"'
# Keep the index (and its usage history) outside the install folder so rebuilds don't wipe it.
$text = $text -replace '(?m)^db_path = "data/index.db"', 'db_path = "%LOCALAPPDATA%/Nova/index.db"'
[IO.File]::WriteAllText($config, $text, (New-Object Text.UTF8Encoding $false))  # no BOM: tomllib rejects it

Copy-Item automations (Join-Path $root "dist\Nova\automations") -Recurse -Force  # editable, next to the exe

$size = (Get-ChildItem dist\Nova -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("== done: dist\Nova ({0:N0} MB)" -f $size)
