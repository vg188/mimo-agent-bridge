# MiMo Agent Bridge · 快速开始

把 **MiMo Desktop 已购套餐** 变成本地 OpenAI / Anthropic / Responses API，给 Claude Code、Cursor、Continue 等用。

## 对外交付（推荐）

**只发一个文件** `MiMoAgentBridge.exe`（约 6.5MB）。

双击即可：自动释放 UI / WebView2Loader / 网关代码到本地，**无需再附带 dll 或文件夹**。

## 3 分钟上手

### 1. 导入登录态（只需一次）

1. 打开 MiMo Desktop，确认已登录小米账号  
2. `Ctrl+Shift+I` → **Network** → 任意请求 → 复制整行 `Cookie:`  
3. 执行：

```powershell
cd MiMoAgentBridge
.\auth.ps1 -Cookie 'userId=...; passToken=...; serviceToken=...'
```

或关闭 MiMo Desktop 后：

```powershell
.\auth.ps1 -Extract
```

### 2. 自检 + 启动

```powershell
.\start.ps1
```

自动打开控制台：`http://127.0.0.1:8787/ui`  
Tauri 桌面版：双击 `ui\mimo-agent-bridge-ui.exe`（会自动拉起网关）  
> `ui\WebView2Loader.dll` 必须与 exe 在同一目录；若仍报 WebView 错误，安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。

### 3. 接到你的 Agent

**OpenAI 兼容（Cursor / Continue / Cline…）**

| 配置项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:8787/v1` |
| API Key | `mimo-local` |
| Model | `mimo-v2.6-pro` 或 `mimo-v2.6-flash` |

**Claude Code**

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8787"
$env:ANTHROPIC_AUTH_TOKEN = "mimo-local"
$env:ANTHROPIC_MODEL = "mimo-v2.6-pro"
claude
```

**Python**

```python
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="mimo-local")
print(c.chat.completions.create(model="mimo-v2.6-pro",
      messages=[{"role":"user","content":"你好"}]))
```

## 本地 API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v1/chat/completions` | 对话（多模态 / stream） |
| POST | `/v1/responses` | OpenAI Responses |
| POST | `/v1/messages` | Anthropic Messages |
| POST | `/v1/audio/speech` | TTS |
| POST | `/v1/audio/transcriptions` | ASR |
| POST | `/v1/images/generations` | 文生图/图生图 |
| GET | `/v1/models` `/v1/capabilities` | 模型与全模态能力 |
| GET | `/plan` `/usage` `/account` | 套餐 / 用量 / 账号 |
| GET | `/ui` `/health` `/openapi.json` | 控制台 / 健康 / 文档 |

## 常用命令

```powershell
$env:MIMO_PYTHON -m mimo_bridge doctor      # 环境自检
$env:MIMO_PYTHON -m mimo_bridge plan        # 看套餐
$env:MIMO_PYTHON -m mimo_bridge chat "你好" --stream
$env:MIMO_PYTHON -m mimo_bridge serve       # 起网关
$env:MIMO_PYTHON -m tests.smoke_live        # 冒烟
```

## 故障

| 现象 | 处理 |
|---|---|
| 401 auth-expired | 重新登录 Desktop，再 `.\auth.ps1 -Cookie '...'` |
| extract failed | Desktop 占用 Cookie 库 → 粘贴 Cookie，或关掉 Desktop 再 `-Extract` |
| 网关连不上 | `.\start.ps1`；检查 8787 端口 |
| 无套餐 | 该账号未订阅，或 `/plan` 里 `current: null` |

---

仅限**本人已购订阅**本机使用。Cookie ≈ 账号凭证，请勿外传。
