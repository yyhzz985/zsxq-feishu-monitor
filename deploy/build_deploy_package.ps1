$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutDir = Join-Path $Root "dist"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StageDir = Join-Path $OutDir "zsxq-monitor-deploy-$Stamp"
$PackagePath = Join-Path $OutDir "zsxq-monitor-deploy-$Stamp.zip"

python (Join-Path $Root "validate_deploy_bundle.py")
if ($LASTEXITCODE -ne 0) {
    throw "Deploy bundle validation failed."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path -LiteralPath $StageDir) {
    Remove-Item -LiteralPath $StageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "docs") | Out-Null

$FileMap = @{
    "zsxq_monitor.py" = "zsxq_monitor.py"
    "zsxq_poll_linux.py" = "zsxq_poll_linux.py"
    "setup_server.sh" = "setup_server.sh"
    "zsxq-poll.service" = "zsxq-poll.service"
    "zsxq-poll.timer" = "zsxq-poll.timer"
    "zsxq-poll.env" = "zsxq-poll.env"
    "install_windows_task.ps1" = "install_windows_task.ps1"
    "validate_deploy_bundle.py" = "validate_deploy_bundle.py"
    "deploy_runbook.md" = "docs/deploy_runbook.md"
    "final_plan.md" = "docs/final_plan.md"
    "dev_log.md" = "docs/dev_log.md"
}

foreach ($item in $FileMap.GetEnumerator()) {
    $source = Join-Path $Root $item.Key
    $target = Join-Path $StageDir $item.Value
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing package source file: $source"
    }
    Copy-Item -LiteralPath $source -Destination $target -Force
}

$Items = Get-ChildItem -LiteralPath $StageDir -Force
Compress-Archive -LiteralPath $Items.FullName -DestinationPath $PackagePath -Force

Write-Host "Deploy package created: $PackagePath"
