# 引入 Cookie 的几种方式
param(
    [string]$Cookie,
    [string]$CookieFile,
    [switch]$Extract
)
$ErrorActionPreference = "Stop"
$py = if ($env:MIMO_PYTHON) { $env:MIMO_PYTHON } else { "python" }
Set-Location $PSScriptRoot

if ($Cookie) {
    & $py -m mimo_bridge auth --cookie $Cookie
} elseif ($CookieFile) {
    & $py -m mimo_bridge auth --cookie-file $CookieFile
} elseif ($Extract) {
    & $py -m mimo_bridge auth --extract
} else {
    Write-Host "用法:" -ForegroundColor Yellow
    Write-Host "  .\auth.ps1 -Cookie 'userId=...; passToken=...; serviceToken=...'"
    Write-Host "  .\auth.ps1 -CookieFile cookie.txt"
    Write-Host "  .\auth.ps1 -Extract     # 关闭 MiMo Desktop 后从 Cookie 库提取"
    Write-Host ""
    Write-Host "DevTools: MiMo Desktop → Ctrl+Shift+I → Network → 复制 Cookie 头" -ForegroundColor DarkGray
    & $py -m mimo_bridge auth --status
}
