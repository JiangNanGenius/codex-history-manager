# Codex Enhance Manager

<p align="center">
  <strong>面向 Codex 重度用户的本地优先 Windows 控制中心。</strong>
</p>

<p align="center">
  管理 Codex 历史、Token/缓存用量、供应商预设、统一模型可见性、
  自适应路由、本地代理诊断、安全配置预览和恢复工具。
</p>

<p align="center">
  <a href="README.md">English</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases">Releases</a>
  ·
  <a href="#快速开始">快速开始</a>
  ·
  <a href="#安全边界">安全边界</a>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img alt="License: Apache-2.0" src="https://img.shields.io/badge/License-Apache--2.0-green.svg">
  </a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-blue.svg">
  <img alt="Platform: Windows" src="https://img.shields.io/badge/Platform-Windows-informational.svg">
  <img alt="Local first" src="https://img.shields.io/badge/Design-local--first-0f766e.svg">
</p>

---

## 项目定位

Codex 真正好用的时候，应该能保持登录态、保留本地历史、清楚知道 Token 和缓存用量，并且在切换供应商时不把配置、沙箱或授权状态搞坏。

Codex Enhance Manager 就是围绕这个工作流做的本地运维层。它最初是 Codex 历史和 Token 管理器，现在正在扩展成供应商配置、模型目录可见性、本地代理、协议适配、用量成本分析、诊断和回滚工具。

项目坚持 local-first：供应商配置、备份、request log 元数据、诊断包、导出文件和临时文件默认都放在本机。

## 现在能做什么

| 模块 | 当前状态 |
| --- | --- |
| 历史与用量 | 浏览会话、查看高用量对话、读取 Codex DB 总量，并补充 rollout 事件、本地代理日志和兼容代理 DB 的缓存读写用量。 |
| 供应商配置 | 管理带 `short_alias`、地区、币种、自定义 headers、`User-Agent`、审批 profile、媒体 profile、额度模板和目录可见性策略的 provider preset。 |
| 统一模型目录 | 预览并过滤 `qwen/qwen3-coder-plus` 这类 Codex 可见模型 ID，支持 provider、capability、context window、cost hint、currency、visibility、focus、AMR group 和 collision-safe ID。 |
| 自适应模型轮转 | 编辑本地 rotation group、批量加入已选 provider models、调整 candidate priority/capability/context 限制，并运行只读 route preview。 |
| 本地代理 | 独立 localhost proxy，支持端口占用自动退避、capability-aware AMR group routing、provider network policy、timeout/retry policy、Auto Approval broker 设置、路由诊断、metadata-only request log 和 OpenAI-compatible 路由。 |
| 协议适配 | 只在有文档或源码依据的地方转换 Responses、Chat、Anthropic Messages、tools、images、SSE events 和国产 Responses profile。 |
| 成本与币种 | 估算 input/output/cache/reasoning/media 成本，保存每条请求的 FX snapshot，支持手动汇率覆盖；在线汇率在接口形状复核前保持关闭。 |
| 配置恢复 | Codex config diff preview、审批/沙箱配置审计、备份、恢复 config/auth、保留官方登录态，以及修复移动后的会话/项目元数据。 |
| 设置体验 | 更多主题预设、完整自定义主题颜色、主题导入导出、设置导入导出、开机启动/提权预览、安全清理预览/执行、卸载清理写锁。 |

## 桌面端结构

这个应用按“操作台”设计，而不是 landing page。

- **Overview**：健康状态、路径、护栏和关键提示。
- **Token Dashboard**：Codex 总量、缓存读写、request log 汇总、成本快照和悬浮 Token 监视器。
- **Providers**：preset-first 配置、分区测试、network health check、status strip、自定义 `User-Agent`、Auto Approval、媒体模式控制、目录可见性、已选模型加入 AMR 和只读 Route Simulator。
- **Unified Model Catalog**：在写入 Codex 之前过滤预览最终模型目录。
- **Adaptive Model Rotation**：rotation group 与 candidate 编辑器，支持已选模型导入、request capability detection 和基于已保存状态的 route preview。
- **Local Proxy**：启动/停止/状态、实际绑定端口、路由解释和日志保留策略。
- **Settings**：存储路径、主题编辑、导入导出、开机启动/提权控制、安全清理、卸载清理、币种设置和悬浮窗字段自定义。
- **Diagnostics and Recovery**：脱敏诊断、汇率状态、审批/沙箱审计、备份恢复、回滚和移动修复。

## 安全边界

| 边界 | 规则 |
| --- | --- |
| Codex auth/config/model catalog/process 写入 | 本窗口只做读取、dry-run 和 preview；真实修改测试必须由用户手动执行。 |
| Windows 开机启动/提权写入 | 状态和预览可以在本窗口运行；创建/移除 Startup folder 启动项或 Task Scheduler 任务需要确认口令，并由用户手动实测。 |
| Auto Approval broker | 用户打开开关后，本地代理可以使用配置模型自动评审并回答 Codex approval request；response payload 必须匹配已从 Codex app-server 源码复核的 JSON-RPC 形状。 |
| 协议转换 | 不猜协议。Responses、Chat、Anthropic、SSE、tools、media 和国产供应商差异必须来自官方文档/源码或明确源码分析。 |
| Secrets | API key、Bearer token 和敏感 headers 在诊断与 request log 中脱敏。 |
| Request log | 本地代理只记录元数据，不保存 prompt、请求体、原始请求 headers 或原始上游响应。 |
| 本地-only 文件 | `_local_notes/`、`research/`、含敏感信息的 diagnostics 和临时研究输出已忽略，不能推送。 |

## 快速开始

### Windows EXE

从 [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) 下载最新构建并运行 EXE。

### 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

桌面窗口背后是本地 Flask 服务：

```text
http://127.0.0.1:51234
```

### 打包 EXE

```bash
python build_exe.py
```

打包脚本会生成单文件 Windows EXE，并包含静态资源、图标、PyWebView、Flask、Pillow 和托盘支持。

## 本地存储

新版本用户数据默认放在：

```text
Documents/Codex Enhance Manager/
```

如果存在旧版 `~/.codex_gui_config.json`，应用会按兼容逻辑导入。

| 路径 | 用途 |
| --- | --- |
| `config.json` | 应用主配置。 |
| `providers/providers.json` | 本地供应商注册表。 |
| `logs/proxy_requests.jsonl` | metadata-only 本地代理 request log。 |
| `backups/`, `codex_backups/` | 应用备份与 Codex 配置备份。 |
| `diagnostics/` | 脱敏诊断包。 |
| `exports/` | 用户导出文件。 |
| `temp/` | 临时文件。 |

## 供应商与模型目录

Codex Enhance Manager 把“模型可见性”和“路由”分开处理。

1. 添加或导入带 `qwen`、`ds`、`kimi`、`openai` 等 alias 的供应商。
2. 设置哪些供应商或模型常驻显示。
3. 手动勾选临时需要的模型。
4. 使用 Provider Focus Switch 临时显示某个供应商的所有模型。
5. 在任何 Codex 写入前预览最终目录。

最终可见模型目录来自：

```text
常驻模型
+ 选中模型
+ 当前聚焦供应商模型
+ Adaptive Model Rotation groups
```

## 本地代理日志

本地代理把非流式和流式请求元数据写到 `logs/proxy_requests.jsonl`。
流式日志会根据 terminal SSE event 和 usage trailer 结束记录。

会记录：

- endpoint、provider、model、status、duration
- 归一化后的 input/output/cache/reasoning/media usage
- 本地成本估算和 FX snapshot
- 安全的路由诊断

不会记录：

- prompt 文本
- 原始请求体
- 原始请求 headers
- 原始上游响应

## 账号同步

Codex 会按 `model_provider` 过滤会话列表。在官方 OpenAI 登录态和自定义/API provider 之间切换时，另一个 provider 下的历史会话可能看起来像“消失了”。

同步流程：

1. 读取当前 `~/.codex/config.toml` 中的 provider/model。
2. 更新 Codex SQLite `threads` 表中的 `model_provider` 和 `model`。
3. 用流式首行重写更新 JSONL `session_meta`。
4. 重建 `session_index.jsonl`，统一 provider/model 字段。

同步完成后，不同账号/供应商之间切换时历史会话仍然可见。

## 核心模块

| 模块 | 作用 |
| --- | --- |
| `app.py` | Flask API 与桌面后端编排。 |
| `main.py` | PyWebView 桌面入口。 |
| `app_paths.py`, `config.py` | Documents-based 本地存储与设置迁移。 |
| `startup_manager.py` | Windows Startup folder 与 Task Scheduler 的预览、应用、移除集成，并带确认护栏。 |
| `providers.py` | Provider registry、presets、schema normalization 和 redaction。 |
| `model_catalog.py` | Unified Model Catalog 生成与预览。 |
| `model_rotation.py`, `amr_registry.py` | Adaptive Model Rotation 引擎与持久化。 |
| `proxy_server.py` | 本地 OpenAI-compatible proxy server。 |
| `approval_broker.py` | Auto Approval prompt builder、严格决策解析和 metadata-only 审批记录。 |
| `auto_approval_runtime.py` | Auto Approval 运行时模型 reviewer，使用已验证的 Chat、Responses 和 Anthropic request shape。 |
| `codex_approval_bridge.py` | 基于源码复核的 Codex app-server 审批 request/result 映射，用于 mocked Auto Approval response。 |
| `request_logs.py` | metadata-only request log、保留策略、汇总和成本快照。 |
| `responses_adapter.py` | Responses <-> Chat 转换和 SSE normalization。 |
| `anthropic_adapter.py` | Anthropic Messages adapter foundation。 |
| `domestic_responses.py` | 阿里百炼/火山方舟 Responses profile 与 guardrails。 |
| `media_proxy.py` | OpenAI-compatible image/video 路由 helper，以及 metadata-only 媒体 Auto Approval hook。 |
| `media_adapters.py` | 对 adapter-required 媒体供应商提供基于官方资料的 dry-run preview 与 guard。 |
| `codex_config.py` | Codex config/auth 备份、diff preview、写入和恢复。 |
| `codex_permissions.py` | 基于官方源码复核的 Codex 审批/沙箱配置审计和 diff preview。 |
| `codex_rollout_usage.py`, `token_stats.py` | Token/cache usage reader。 |
| `currency.py`, `costing.py`, `quota.py` | FX snapshot、本地成本估算和通用 quota probe。 |
| `diagnostics.py`, `move_repair.py` | 安全诊断和项目/会话移动修复。 |

## 参考资料

- [OpenAI Codex 源码](https://github.com/openai/codex)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat)
- [OpenAI Images API](https://platform.openai.com/docs/api-reference/images)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
- [阿里百炼 / 通义千问 OpenAI Responses](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)
- [阿里百炼控制台 Responses 入口](https://bailian.console.aliyun.com/cn-beijing?spm=5176.12818093_47.resourceCenter.1.52c916d0vlEMb0&tab=api#/api/?type=model&url=3016808)
- [火山方舟 Responses API](https://www.volcengine.com/docs/82379/1585128?lang=zh)
- [火山方舟 Seedream 图片生成](https://www.volcengine.com/docs/82379/1824121?lang=zh)
- [火山方舟 Seedance 视频生成](https://www.volcengine.com/docs/82379/2291680?lang=zh)
- [cc-switch](https://github.com/farion1231/cc-switch)
- [CodexPlusPlus](https://github.com/BigPizzaV3/CodexPlusPlus)

## 路线图

| 下一步 | 方向 |
| --- | --- |
| 协议复核 | 持续对照 official Codex、国产 Responses、Anthropic、tools、SSE、compact 和媒体 item 行为。 |
| 媒体适配 | 在 payload、polling、cancel 和 response 格式复核后接入阿里百炼与火山方舟图片/视频适配。 |
| 审批 broker 接线 | 在确认真实本地 proxy/app-server 拦截链路后，把源码复核过的 approval bridge 接入运行通道。 |
| 审批与沙箱修复 | 在已接入的源码复核审计基础上，等用户手动验证写入后再扩展为修复预设。 |
| 开机启动集成 | 用打包后的 EXE 手动验证 Startup folder 与 Task Scheduler 最高权限流程，再继续优化 UAC/task 错误体验。 |
| 成本仪表盘 | 补全原生/展示币种对照、过期汇率提醒、provider-reported vs estimated cost 和媒体价格层级。 |
| 额度集成 | 在通用 probe scaffold 上叠加 provider-specific balance/quota endpoint。 |
| UI 打磨 | 继续清理历史文案、图标、i18n 覆盖、截图和窄窗口布局。 |
| 打包发布 | 等 proxy/protocol 层到稳定里程碑后构建并发布新的 EXE。 |

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
