[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Compiler,
    [string]$OutputDirectory,
    [string]$WorkDirectory
)

$ErrorActionPreference = 'Stop'
if (-not $Compiler) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $Compiler = $command.Source }
}
if (-not $Compiler) {
    foreach ($folder in @(${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $folder) { continue }
        foreach ($subpath in @('Inno Setup 6\ISCC.exe', 'Programs\Inno Setup 6\ISCC.exe')) {
            $candidate = Join-Path $folder $subpath
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $Compiler = $candidate; break }
        }
        if ($Compiler) { break }
    }
}
if (-not $Compiler) { throw 'Укажи путь к ISCC.exe в параметре -Compiler.' }
$arguments = @((Join-Path $PSScriptRoot 'build.py'), '--iscc', $Compiler)
if ($OutputDirectory) { $arguments += @('--output-dir', $OutputDirectory) }
if ($WorkDirectory) { $arguments += @('--work-dir', $WorkDirectory) }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "Сборка завершилась с кодом $LASTEXITCODE." }
