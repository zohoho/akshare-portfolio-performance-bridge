param(
    [string]$BuildPython = "python",
    [string]$RuntimePython = "",
    [string]$RuntimeSitePackages = "",
    [switch]$SkipRuntime
)

$ErrorActionPreference = "Stop"
$project = [System.IO.Path]::GetFullPath($PSScriptRoot)
$pythonCommand = Get-Command $BuildPython -ErrorAction SilentlyContinue
$python = if (Test-Path -LiteralPath $BuildPython) {
    [System.IO.Path]::GetFullPath($BuildPython)
} elseif ($pythonCommand) {
    $pythonCommand.Source
} else {
    throw "Build Python was not found: $BuildPython"
}
$buildRoot = Join-Path $project "build"
$packageRoot = Join-Path $buildRoot "package"
$distRoot = Join-Path $buildRoot "pyinstaller-dist"
$workRoot = Join-Path $buildRoot "pyinstaller-work"
$buildHome = Join-Path $buildRoot "home"

foreach ($path in @($packageRoot, $distRoot, $workRoot)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
New-Item -ItemType Directory -Path $buildHome -Force | Out-Null
$env:USERPROFILE = $buildHome
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot "pyinstaller-config"

& $python -m PyInstaller `
    --noconfirm --clean --windowed --onedir `
    --name AkSharePPBridge `
    --distpath $distRoot --workpath $workRoot `
    --specpath $buildRoot `
    --exclude-module akshare --exclude-module pandas --exclude-module numpy `
    --hidden-import gui --hidden-import service_manager --hidden-import update_manager --hidden-import app_paths --hidden-import runtime_tk `
    "$project\app.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

robocopy "$distRoot\AkSharePPBridge" $packageRoot /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the GUI build failed: $LASTEXITCODE" }

foreach ($file in @("app.py", "app_paths.py", "bridge.py", "service_manager.py", "update_manager.py", "runtime_tk.py", "start_service_hidden.cmd", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "requirements.txt")) {
    Copy-Item -LiteralPath "$project\$file" -Destination "$packageRoot\$file" -Force
}

if (-not $SkipRuntime) {
    $runtimeArgs = @{ Destination = "$packageRoot\runtime"; PythonExecutable = $python }
    if ($RuntimePython) { $runtimeArgs.SourcePython = $RuntimePython }
    if ($RuntimeSitePackages) { $runtimeArgs.SourceSitePackages = $RuntimeSitePackages }
    & "$project\build_runtime.ps1" @runtimeArgs
    if ($LASTEXITCODE -ne 0) { throw "Private runtime build failed." }
}

Write-Host "Application directory created: $packageRoot"
