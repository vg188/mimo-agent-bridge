# 重新构建单文件成品 dist/MiMoAgentBridge.exe
# 依赖: rust cargo, 以及已构建的 ui WebView2Loader.dll / mimo_bridge
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent   # mimo-agent-bridge
$pkg  = Join-Path $root "packaging"
$app  = Join-Path $pkg "app"

Write-Host "==> 1/3 组装运行时 payload"
if (Test-Path $app) { Remove-Item -Recurse -Force $app }
New-Item -ItemType Directory -Force "$app\ui" | Out-Null
New-Item -ItemType Directory -Force "$app\mimo_bridge\static" | Out-Null

# prefer release UI
$uiExe = Join-Path $root "ui\src-tauri\target\release\mimo-agent-bridge-ui.exe"
if (-not (Test-Path $uiExe)) { $uiExe = Join-Path $root "ui\src-tauri\target\debug\mimo-agent-bridge-ui.exe" }
$dll = Join-Path (Split-Path $uiExe) "WebView2Loader.dll"
if (-not (Test-Path $dll)) { throw "WebView2Loader.dll not found next to $uiExe" }

Copy-Item $uiExe "$app\ui\mimo-agent-bridge-ui.exe" -Force
Copy-Item $dll "$app\ui\WebView2Loader.dll" -Force
Copy-Item (Join-Path $root "mimo_bridge\*.py") "$app\mimo_bridge\" -Force
Copy-Item (Join-Path $root "mimo_bridge\static\index.html") "$app\mimo_bridge\static\" -Force
Set-Content -Encoding utf8 "$app\README.txt" "MiMo Agent Bridge`n默认网关 http://127.0.0.1:8787`n控制台: 打开应用后按提示导入 Cookie"

Write-Host "==> 2/3 打包 app.zip"
$zip = Join-Path $pkg "launcher\assets\app.zip"
if (Test-Path $zip) { Remove-Item $zip }
Push-Location $app
tar -a -cf $zip *
Pop-Location

Write-Host "==> 3/3 编译单文件启动器"
Push-Location (Join-Path $pkg "launcher")
cargo build --release
Pop-Location

$dist = Join-Path (Split-Path $root -Parent) "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
Copy-Item (Join-Path $pkg "launcher\target\release\mimo-agent-bridge-launcher.exe") (Join-Path $dist "MiMoAgentBridge.exe") -Force
Write-Host "OK -> $(Join-Path $dist 'MiMoAgentBridge.exe')" -ForegroundColor Green
