$ErrorActionPreference = "Stop"

$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $DeployDir
$OutDir = Join-Path $Root "dist"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StageDir = Join-Path $OutDir "zsxq-monitor-deploy-$Stamp"
$PackagePath = Join-Path $OutDir "zsxq-monitor-deploy-$Stamp.zip"

python (Join-Path $DeployDir "validate_deploy_bundle.py")
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
    "src/zsxq_monitor.py" = "zsxq_monitor.py"
    "deploy/setup_server.sh" = "setup_server.sh"
    "deploy/zsxq-poll.service" = "zsxq-poll.service"
    "deploy/zsxq-poll.timer" = "zsxq-poll.timer"
    "deploy/zsxq-poll.env" = "zsxq-poll.env"
    "deploy/install_windows_task.ps1" = "install_windows_task.ps1"
    "deploy/validate_deploy_bundle.py" = "validate_deploy_bundle.py"
    "docs/部署与切换手册.md" = "docs/deploy_runbook.md"
    "docs/服务器部署手册.md" = "docs/server_runbook.md"
    "docs/开发日志.md" = "docs/dev_log.md"
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
