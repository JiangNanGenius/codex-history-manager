# Codex Enhance Manager

> 面向 Windows 桌面的 Codex 本地控制中心：历史会话、Token/缓存用量、供应商预设、统一模型可见性、自适应路由、安全的 Codex 配置预览，以及带脱敏诊断的本地代理。

[English](README.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-informational.svg)](#快速开始)

Codex Enhance Manager 最初是 Codex 历史会话和 Token 管理器。现在它正在扩展为 Codex 的本地运维层：多供应商配置、模型目录可见性、请求路由、缓存/成本统计、诊断和恢复工具。

项目坚持 local-first：供应商密钥、应用设置、request log metadata、备份、导出和诊断文件默认都保存在本机。

## 当前状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 会话历史 | 可用 | 浏览、筛选、查看、导出、归档，以及修复移动后的会话元数据。 |
| Token/缓存用量 | 可用 | 读取 Codex DB 总量、Codex rollout 缓存事件、本地代理日志和兼容代理数据库。 |
| 供应商注册表 | 可用 | 16 个内置预设，支持别名、地区、币种、headers、User-Agent、媒体 profile 和可见性策略。 |
| 统一模型目录（UMC） | 可用 | 多供应商模型同时可见，支持 provider-prefixed model IDs。 |
| 自适应模型轮转（AMR） | 脚手架可用 | 优先级/能力路由、冷却、上下文限制、路由解释和 JSON 持久化。 |
| 本地代理 | 脚手架可用 | Chat、Responses、Models、媒体 pass-through 脚手架、端口退避、路由诊断、metadata-only 日志。 |
| 协议适配 | 脚手架可用 | Responses <-> Chat、Anthropic Messages foundation、国产 Responses guardrails；未知 shape 保持阻断。 |
| 成本与币种 | 脚手架可用 | 原生/展示币种、手动汇率覆盖、本地成本拆分、request FX snapshot。 |
| 余额/额度 | 脚手架可用 | 通用 JSON endpoint 探测、TTL cache、脱敏失败快照。 |
| Codex 配置安全 | 可用 | Diff Preview、备份、回滚、登录态保留和显式修改确认。 |

## 功能亮点

### 统一供应商配置

- 本地 provider registry，内置 OpenAI、Azure、OpenRouter、DeepSeek、Moonshot、智谱、SiliconFlow、MiniMax、阿里百炼、火山方舟、魔搭、阶跃星辰、NVIDIA 和自定义 endpoint 预设。
- Provider 字段包含 `short_alias`、原生币种、国家/地区、自定义 headers、User-Agent、media profile、quota template 和 model pricing hints。
- 可见性支持 hidden、focused-only、always-visible、selected-models。

### 统一模型目录（Unified Model Catalog）

- 从多个供应商生成一个 Codex 可见模型目录。
- 使用 `qwen/qwen3-coder-plus` 这样的 provider-prefixed ID。
- 保留 Provider Focus Switch，同时保留常驻模型和选中模型。
- 写入 Codex 前先预览目录输出。

### 本地代理

- 独立 localhost HTTP server，不绑定 Flask。
- 支持 `/v1/models`、`/v1/chat/completions`、`/v1/responses`、`/v1/responses/compact`，并预留 OpenAI-compatible media route。
- 按 `provider/model` 硬前缀、精确模型匹配、UMC 条目、media profile 或 AMR group 路由。
- Windows 下尽量使用严格端口绑定；配置端口被占用时自动退避到后续可用端口。
- 非流式请求写入 metadata-only JSONL：endpoint、provider、model、status、duration、normalized usage、cache read/write、本地成本估算和汇率快照。
- 代理日志不会保存 prompt、请求体、原始请求 headers 或原始上游响应。

### 用量、成本与币种

- 从 Codex `threads.tokens_used` 读取折叠总量。
- 从 Codex rollout `token_count`、本地代理日志和兼容代理数据库补充 cache read/write。
- 估算 input、output、cache read、cache write、reasoning、image、video 成本。
- 支持 provider/model 原生币种、展示币种、手动汇率覆盖、缓存汇率和每条请求的 FX snapshot。
- 在线汇率 adapter 在官方 API shape 可访问并复核前保持阻断。

### 诊断与恢复

- 脱敏诊断覆盖 Codex 配置、登录态、本地代理状态、providers、UMC、AMR、quota snapshot、request log summary 和系统环境。
- Config/auth 写入前创建备份，并支持回滚。
- 项目/会话移动修复会 dry-run 校验，再同步 SQLite、JSONL metadata 和 `session_index.jsonl`。
- 清理 API 使用 allowlist 和确认短语。

## 安全边界

| 边界 | 规则 |
| --- | --- |
| Codex auth/config/model catalog/process 写入 | 本窗口只做读取、dry-run 和 preview；真实修改测试由用户手动执行。 |
| 协议转换 | 不猜协议。Responses、Chat、Anthropic、SSE、tools、media、国产供应商差异必须来自官方文档/源码或明确源码分析。 |
| Secrets | API key、Bearer token 和敏感 headers 在诊断和 request log 中脱敏。 |
| 本地-only 文件 | `_local_notes/`、`research/`、含敏感信息的 diagnostics 和临时研究输出不会推送。 |
| Codex++ 内容边界 | 可以参考有用实现思路，但不迁移 sponsor/recommendation/ad 内容。 |

## 快速开始

### Windows EXE

从 [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) 下载最新构建并运行 EXE。

### 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

桌面窗口背后是本地 Flask 服务：`http://127.0.0.1:51234`。

### 打包 EXE

```bash
python build_exe.py
```

打包脚本会生成单文件 Windows EXE，并包含静态资源、图标、PyWebView、Flask、Pillow 和托盘支持。

## 本地存储

用户数据默认放在：

```text
Documents/Codex Enhance Manager/
```

如果存在旧版 `~/.codex_gui_config.json`，应用会按兼容逻辑导入。

| 路径或设置 | 用途 |
| --- | --- |
| `config.json` | 应用主配置。 |
| `providers/providers.json` | 本地供应商注册表。 |
| `logs/proxy_requests.jsonl` | metadata-only 本地代理 request log。 |
| `backups/` 和 `codex_backups/` | 应用备份与 Codex 配置备份。 |
| `diagnostics/` | 脱敏诊断包。 |
| `exports/` | 用户导出文件。 |
| `temp/` | 临时文件。 |

## 账户同步

Codex 会按 `model_provider` 过滤会话列表。在官方 OpenAI 登录态和自定义/API provider 之间切换时，另一个 provider 下的历史会话可能看起来像“消失了”。

同步流程：

1. 读取当前 `~/.codex/config.toml` 中的 provider/model。
2. 更新 Codex SQLite `threads` 表中的 `model_provider` 和 `model`。
3. 用流式首行重写更新 JSONL `session_meta`。
4. 重建 `session_index.jsonl`，统一 provider/model 字段。

同步完成后，不同账户/供应商之间切换时历史会话仍然可见。

## 核心模块

| 模块 | 作用 |
| --- | --- |
| `app.py` | Flask API 和桌面后端编排。 |
| `main.py` | PyWebView 桌面入口。 |
| `app_paths.py`, `config.py` | Documents-based 本地存储与设置迁移。 |
| `providers.py` | Provider registry、预设、schema normalize、redaction。 |
| `model_catalog.py` | Unified Model Catalog 生成。 |
| `model_rotation.py`, `amr_registry.py` | Adaptive Model Rotation 引擎和持久化。 |
| `proxy_server.py` | 本地 OpenAI-compatible proxy server。 |
| `request_logs.py` | metadata-only request log、保留策略、汇总、成本快照。 |
| `responses_adapter.py` | Responses <-> Chat 转换和 SSE normalization。 |
| `anthropic_adapter.py` | Anthropic Messages adapter foundation。 |
| `domestic_responses.py` | 阿里百炼/火山方舟 Responses 兼容性 profile 和 guardrails。 |
| `media_proxy.py` | OpenAI-compatible image/video 路由 helper。 |
| `codex_config.py` | Codex config/auth 备份、diff preview、写入、还原。 |
| `codex_rollout_usage.py`, `token_stats.py` | Token/cache usage reader。 |
| `currency.py`, `costing.py`, `quota.py` | FX snapshot、本地成本估算、通用 quota probe。 |
| `diagnostics.py`, `move_repair.py` | 安全诊断和项目/会话移动修复。 |

## 路线图

| 下一阶段 | 方向 |
| --- | --- |
| 媒体适配器 | 在 payload、polling、response 格式复核后接入阿里百炼和火山方舟图片/视频真实适配。 |
| 流式日志 | 确认 stream lifecycle、最终 usage 和半关闭 stream 语义后，再记录流式代理请求。 |
| 成本仪表盘 | 增加原生/展示币种、cache read/write、reasoning、image、video、provider reported vs estimated 成本列。 |
| 额度集成 | 在通用 probe scaffold 上逐步叠加 provider-specific balance/quota endpoint。 |
| 主题系统 | 增加更多主题预设、自定义主题编辑和主题导入。 |
| Codex 审批/沙箱修复 | 先调研官方 Codex approval path 和 sandbox config，再实现稳定修复。 |
| 打包发布 | 等 proxy/protocol 层达到更稳定里程碑后，构建并发布新的 EXE。 |

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
