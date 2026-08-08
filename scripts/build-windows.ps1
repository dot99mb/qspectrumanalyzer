[CmdletBinding()]
param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"
$SpecPath = Join-Path $ProjectRoot "packaging\qspectrumanalyzer.spec"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    & $Python -3.12 -m venv $VenvPath
}

& $PythonPath -m pip install --upgrade pip
& $PythonPath -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")
& $PythonPath -m PyInstaller --noconfirm --clean $SpecPath

$Executable = Join-Path $ProjectRoot "dist\QSpectrumAnalyzer\QSpectrumAnalyzer.exe"
$ToolsPath = Join-Path $ProjectRoot "dist\QSpectrumAnalyzer\tools\rtl-sdr"
New-Item -ItemType Directory -Force $ToolsPath | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\RTL_SDR_TOOLS_RU.txt") -Destination $ToolsPath -Force
Write-Host "Windows build created: $Executable"
Write-Host "Place rtl_power.exe and its DLLs in: $ToolsPath"
