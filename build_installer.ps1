param([string]$IsccPath = "")

$ErrorActionPreference = "Stop"
$project = [System.IO.Path]::GetFullPath($PSScriptRoot)

if (-not $IsccPath) {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    $candidates = @(
        $(if ($command) { $command.Source }),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $IsccPath = $candidates | Select-Object -First 1
}

if (-not (Test-Path -LiteralPath "$project\build\package\AkSharePPBridge.exe")) {
    throw "Run build_app.ps1 first."
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw "Inno Setup compiler was not found. Install Inno Setup 6 or pass -IsccPath."
}

& $IsccPath "$project\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

Write-Host "Installer created in: $project\dist"
