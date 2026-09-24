[CmdletBinding()]
param(
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$At = '00:10',
    [string]$PythonPath = '',
    [switch]$WakeToRun
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Venv Python not found. Supply -PythonPath with the full path to your existing python.exe.'
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$RuntimePath = Join-Path $ProjectRoot 'backend\runtime.local.json'
if (-not (Test-Path -LiteralPath $RuntimePath)) {
    $RuntimeValues = Get-Content -Raw (Join-Path $ProjectRoot 'backend\runtime.example.json') | ConvertFrom-Json
    foreach ($Property in $RuntimeValues.PSObject.Properties) {
        $OverrideValue = [Environment]::GetEnvironmentVariable($Property.Name, 'Process')
        if (-not [string]::IsNullOrWhiteSpace($OverrideValue)) { $Property.Value = $OverrideValue }
    }
    $ConfiguredTesseract = [Environment]::GetEnvironmentVariable('TESSERACT_CMD', 'Process')
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredTesseract)) {
        $RuntimeValues | Add-Member -NotePropertyName 'TESSERACT_CMD' -NotePropertyValue $ConfiguredTesseract
    }
    $RuntimeValues | ConvertTo-Json | Set-Content -LiteralPath $RuntimePath -Encoding UTF8
}
$TaskName = 'ReelsDigest-Nightly'
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($ExistingTask -and $ExistingTask.Description -notlike 'Reels Digest:*') {
    throw 'A different task already uses ReelsDigest-Nightly. Rename that task before installing.'
}
$Account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument '-u -m backend.nightly --date yesterday' -WorkingDirectory $ProjectRoot
$DailyTrigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add([timespan]::Parse($At)))
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $Account
$Principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType Interactive -RunLevel Limited
$TaskSettings = @{
    StartWhenAvailable = $true
    MultipleInstances = 'IgnoreNew'
    ExecutionTimeLimit = (New-TimeSpan -Hours 2)
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    Priority = 7
}
if ($WakeToRun) { $TaskSettings.WakeToRun = $true }
$Settings = New-ScheduledTaskSettingsSet @TaskSettings
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $Action -Trigger @($DailyTrigger, $LogonTrigger) -Principal $Principal -Settings $Settings -Description 'Reels Digest: previous-day understanding, clustering and Discord delivery; preserve its database.' -Force | Out-Null
Write-Host "Installed $TaskName for $At each day in Windows timezone $((Get-TimeZone).Id), plus sign-in catch-up."
Write-Host "Runs as $Account while signed in (locking the screen is fine). No digest was triggered by this installer."
Write-Host 'Keep the laptop powered on; use -WakeToRun if you want Windows to request a wake from sleep.'
Write-Host 'Use local discord.local.json credentials; temporary terminal-only Discord variables are not saved.'
Write-Host 'Daily summaries: backend\data\logs\nightly.log; latest result: backend\data\nightly-last.json'
