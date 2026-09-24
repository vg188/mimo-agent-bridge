# 一键启动 MiMo Agent Bridge 网关 + 打开 Web 控制台
param(
    [int]$Port = 8787,
    [switch]$NoBrowser,
    [switch]$Ui
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = if ($env:MIMO_PYTHON) { $env:MIMO_PYTHON } else { "python" }

Write-Host "==> MiMo Agent Bridge" -ForegroundColor Cyan
& $py -m mimo_bridge doctor
Write-Host ""

$psi = @{
    FilePath = $py
    ArgumentList = @("-m", "mimo_bridge", "serve", "--port", "$Port")
    WorkingDirectory = $root
    PassThru = $true
}
$proc = Start-Process @psi
Write-Host "==> gateway PID $($proc.Id)  http://127.0.0.1:$Port" -ForegroundColor Green

if (-not $NoBrowser) {
    Start-Sleep -Milliseconds 800
    Start-Process "http://127.0.0.1:$Port/ui"
}

if ($Ui) {
    $exeRelease = Join-Path $root "ui\src-tauri\target\release\mimo-agent-bridge-ui.exe"
    $exeDebug = Join-Path $root "ui\src-tauri\target\debug\mimo-agent-bridge-ui.exe"
    if (Test-Path $exeRelease) { Start-Process $exeRelease }
    elseif (Test-Path $exeDebug) { Start-Process $exeDebug }
    else { Write-Host "Tauri UI 未构建，已使用浏览器控制台 /ui" -ForegroundColor Yellow }
}

Write-Host "==> Ctrl+C 结束网关" -ForegroundColor DarkGray
try { Wait-Process -Id $proc.Id } finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}
