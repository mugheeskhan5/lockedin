[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Task = Get-ScheduledTask -TaskName 'ReelsDigest-Nightly' -TaskPath '\' -ErrorAction SilentlyContinue
if ($Task) {
    if ($Task.Description -notlike 'Reels Digest:*') { throw 'Refusing to remove an unrelated task.' }
    Stop-ScheduledTask -TaskName 'ReelsDigest-Nightly' -TaskPath '\'
    Unregister-ScheduledTask -TaskName 'ReelsDigest-Nightly' -TaskPath '\' -Confirm:$false
}
Write-Host 'Nightly task removed. Your database, captures and settings are retained.'
