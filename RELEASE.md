# 发布说明 · MiMo Agent Bridge 0.2.0

对外正式分发请使用 `release/` 目录中三选一：

| 文件 | 说明 |
|---|---|
| **MiMoAgentBridge-0.2.0-x64-setup.exe** | **安装版（推荐）** · NSIS · 开始菜单快捷方式 · 自动处理 WebView2Loader |
| MiMoAgentBridge-0.2.0-x64.msi | MSI · 企业/静默部署 `msiexec /i ... /qn` |
| MiMoAgentBridge-0.2.0-x64-portable.zip | 便携版 · 解压后运行 `MiMoAgentBridge\MiMoAgentBridge.exe` |

## 与 0.1 的差异（正式产品）

- 使用 **Tauri 官方打包器**（NSIS / MSI），不再依赖自解压 exe + 手动拷 DLL
- **无控制台窗口**：网关用 `pythonw` + `CREATE_NO_WINDOW` 启动
- 界面重做：侧栏导航、总览/登录/套餐/接入/全模态
- 安装版资源内嵌 `mimo_bridge` 包

## 系统要求

- Windows 10/11 x64
- WebView2 Runtime（Windows 11 自带；安装版可自动下载）
- Python 3.10+（仅网关需要；`pythonw`/`py` 在 PATH）

## 用户流程

1. 安装或解压后打开 **MiMo Agent Bridge**
2. 「登录态」粘贴 MiMo Desktop Cookie（一次）
3. Agent 配置：`http://127.0.0.1:8787/v1` · key `mimo-local` · model `mimo-v2.6-pro`

## 重新构建

```powershell
cd mimo-agent-bridge\ui\src-tauri
cargo tauri build
# 产物在 target\release\bundle\{nsis,msi}
```
