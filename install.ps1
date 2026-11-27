[CmdletBinding()]
param(
    [switch]$NoStartup
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$MonitorScript = Join-Path $Root 'monitor.py'
$IconPath = Join-Path $Root 'assets\icon.ico'

if (-not (Test-Path $Pythonw)) { throw "Python runtime not found: $Pythonw" }
if (-not (Test-Path $MonitorScript)) { throw "Unified entry point not found: $MonitorScript" }

function New-AppShortcut {
    param(
        [Parameter(Mandatory=$true)][string]$LinkPath,
        [Parameter(Mandatory=$true)][string]$ScriptPath,
        [string]$ExtraArguments = '',
        [string]$Description = ''
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($LinkPath)
    $shortcut.TargetPath = $Pythonw
    $quoted = '"' + $ScriptPath + '"'
    $shortcut.Arguments = if ([string]::IsNullOrWhiteSpace($ExtraArguments)) { $quoted } else { $quoted + ' ' + $ExtraArguments }
    $shortcut.WorkingDirectory = $Root
    if (Test-Path $IconPath) { $shortcut.IconLocation = $IconPath }
    $shortcut.Description = $Description
    $shortcut.WindowStyle = 1
    $shortcut.Save()
}

function Remove-ShortcutIfExists {
    param([Parameter(Mandatory=$true)][string]$LinkPath)
    if (Test-Path $LinkPath) {
        Remove-Item -LiteralPath $LinkPath -Force
    }
}

$desktop = [Environment]::GetFolderPath('Desktop')
Remove-ShortcutIfExists -LinkPath (Join-Path $desktop 'Claude Usage Manager.lnk')
Remove-ShortcutIfExists -LinkPath (Join-Path $desktop 'AI Limits Monitor.lnk')
New-AppShortcut -LinkPath (Join-Path $desktop 'AI Limits Monitor.lnk') -ScriptPath $MonitorScript -Description 'AI limits with integrated detailed usage analytics'

$startup = [Environment]::GetFolderPath('Startup')
Remove-ShortcutIfExists -LinkPath (Join-Path $startup 'Claude Usage Manager.lnk')
Remove-ShortcutIfExists -LinkPath (Join-Path $startup 'AI Limits Monitor.lnk')
if (-not $NoStartup) {
    New-AppShortcut -LinkPath (Join-Path $startup 'AI Limits Monitor.lnk') -ScriptPath $MonitorScript -Description 'AI limits with integrated detailed usage analytics'
}

Write-Host 'Unified AI Limits Monitor shortcut installed.'
Write-Host 'Detailed analytics now opens from AI Limits Monitor.'
