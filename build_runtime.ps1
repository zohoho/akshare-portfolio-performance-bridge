param(
    [string]$Destination = "$PSScriptRoot\build\package\runtime",
    [string]$PythonExecutable = "python",
    [string]$SourcePython = "",
    [string]$SourceSitePackages = ""
)

$ErrorActionPreference = "Stop"
$pythonCommand = Get-Command $PythonExecutable -ErrorAction SilentlyContinue
$pythonPath = if (Test-Path -LiteralPath $PythonExecutable) {
    [System.IO.Path]::GetFullPath($PythonExecutable)
} elseif ($pythonCommand) {
    $pythonCommand.Source
} else {
    throw "Python was not found: $PythonExecutable"
}
if (-not $SourcePython) {
    $SourcePython = (& $pythonPath -c "import sys; print(sys.base_prefix)").Trim()
}
if (-not $SourceSitePackages) {
    $SourceSitePackages = (& $pythonPath -c "import site; print(site.getsitepackages()[0])").Trim()
}

$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$sourcePythonPath = [System.IO.Path]::GetFullPath($SourcePython)
$sourceSitePath = [System.IO.Path]::GetFullPath($SourceSitePackages)

if (-not $destinationPath.StartsWith([System.IO.Path]::GetFullPath($PSScriptRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Runtime target must stay inside the project directory."
}
if (-not (Test-Path -LiteralPath "$sourcePythonPath\python.exe")) {
    throw "Python 3.12 source runtime was not found: $sourcePythonPath"
}
if (-not (Test-Path -LiteralPath "$sourceSitePath\akshare")) {
    throw "AkShare site-packages was not found: $sourceSitePath"
}

if (Test-Path -LiteralPath $destinationPath) {
    Remove-Item -LiteralPath $destinationPath -Recurse -Force
}
New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null

foreach ($directory in @("DLLs", "Lib")) {
    robocopy "$sourcePythonPath\$directory" "$destinationPath\$directory" /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying Python $directory failed: $LASTEXITCODE" }
}
foreach ($file in @("python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt")) {
    Copy-Item -LiteralPath "$sourcePythonPath\$file" -Destination "$destinationPath\$file" -Force
}

$targetSite = "$destinationPath\Lib\site-packages"
if (Test-Path -LiteralPath $targetSite) {
    Remove-Item -LiteralPath $targetSite -Recurse -Force
}
New-Item -ItemType Directory -Path $targetSite -Force | Out-Null
robocopy $sourceSitePath $targetSite /E /NFL /NDL /NJH /NJS /NP `
    /XD "PyInstaller" "_pyinstaller_hooks_contrib" "altgraph" "pefile" "win32ctypes" "__pycache__" `
    /XF "*.pyc" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying AkShare dependencies failed: $LASTEXITCODE" }

Get-ChildItem -LiteralPath $targetSite -Directory | Where-Object {
    $_.Name -match '^(pyinstaller|altgraph|pefile|pywin32_ctypes|pyinstaller_hooks_contrib)-.*\.dist-info$'
} | Remove-Item -Recurse -Force

& "$destinationPath\python.exe" -c "import akshare, pandas, pip; print('runtime ok', akshare.__file__)"
if ($LASTEXITCODE -ne 0) { throw "Private runtime import check failed." }
