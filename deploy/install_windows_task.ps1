param(
    [string]$TaskName = "ZSXQ-Feishu-Monitor",
    [string]$PythonExe = "pythonw",
    [string]$ScriptPath = "C:\Users\1\.hermes\scripts\zsxq_monitor.py"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Script not found: $ScriptPath"
}

$resolvedPython = (Get-Command $PythonExe -ErrorAction Stop).Source

$check = & $resolvedPython $ScriptPath --check
if ($LASTEXITCODE -ne 0) {
    $check
    throw "Preflight check failed. Fix the items above before creating the scheduled task."
}

# Create VBS wrapper for silent execution (no console window popup)
$VbsPath = Join-Path (Split-Path $ScriptPath -Parent) "zsxq_monitor_run.vbs"
$VbsCode = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run $resolvedPython $ScriptPath, 0, False
Set WshShell = Nothing
"@
$VbsCode | Out-File -FilePath $VbsPath -Encoding ASCII
Write-Host "VBS wrapper created: $VbsPath"

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Check status with: Get-ScheduledTask -TaskName '$TaskName'"
