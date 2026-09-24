# MiMo Agent Bridge

把 **MiMo Desktop 已购套餐** 暴露为本地 **OpenAI / Anthropic / Responses** API，方便在 Claude Code、Cursor、Continue 等本地 Agent 中继续使用同一条订阅额度。

> **仅限本人已购订阅、本机使用。** Cookie 等同账号凭证，请勿上传或分享。本项目与小米官方无关，接口为客户端行为整理，无兼容性保证。

## 功能

| 协议 | 路径 |
|---|---|
| OpenAI Chat（多模态 / stream） | `POST /v1/chat/completions` |
| OpenAI Responses | `POST /v1/responses` |
| Anthropic Messages | `POST /v1/messages` |
| OpenAI TTS / ASR（适配） | `POST /v1/audio/speech` · `/v1/audio/transcriptions` |
| 图像生成 | `POST /v1/images/generations` |
| 套餐 / 用量 / 能力矩阵 | `GET /plan` `/usage` `/v1/models` `/v1/capabilities` |

另有 **Tauri 桌面控制台**（登录态、套餐、启停网关、全模态矩阵）。

## 快速开始

```bash
# 1. 导入 MiMo Desktop 登录态（DevTools 复制 Cookie）
python -m mimo_bridge auth --cookie 'userId=...; passToken=...; serviceToken=...'

# 2. 自检
python -m mimo_bridge doctor

# 3. 启动网关
python -m mimo_bridge serve
# 控制台 http://127.0.0.1:8787/ui
```

Agent 配置示例：

```text
Base URL : http://127.0.0.1:8787/v1
API Key  : mimo-local
Model    : mimo-v2.6-pro
```

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_AUTH_TOKEN=mimo-local
export ANTHROPIC_MODEL=mimo-v2.6-pro
```

## 桌面端

```powershell
cd ui/src-tauri
cargo tauri build
# 产物: target/release/bundle/nsis/*-setup.exe
#       target/release/bundle/msi/*.msi
```

Windows 运行需要 **WebView2 Runtime**（Win11 自带）与 **Python 3.10+**（网关）。

## 开发

```bash
python -m tests.test_bridge      # 单元测试
python -m tests.smoke_live       # 冒烟
python -m mimo_bridge doctor --upstream
```

环境变量见 `mimo_bridge/config.py`（`MIMO_API_BASE_URL`、`MIMO_BRIDGE_PORT`、`MIMO_COOKIE` 等）。

## 免责声明

- 仅供订阅用户在本机调试与个人自动化使用  
- 请遵守小米 / MiMo 服务条款；勿用于转售、共享账号或绕过配额  
- 作者不对账号封禁、数据丢失或服务变更负责  

## License

[MIT](./LICENSE)
