# 启动本地网关（Windows）
# 用法: .\run.ps1 [port]
param([int]$Port = 8787)
$py = if ($env:MIMO_PYTHON) { $env:MIMO_PYTHON } else { "python" }
Set-Location $PSScriptRoot
& $py -m mimo_bridge serve --port $Port
