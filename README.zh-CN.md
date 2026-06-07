# Codex Enhance Manager

> 面向 Windows 桌面的 Codex 本地控制中心。目前已支持历史会话管理、账户/供应商同步、Token 用量监控和安全备份，并正在扩展为 Codex 的本地供应商、路由、媒体、额度和成本管理层。

[English](README.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## 当前功能

### Token 监控
- 常驻自动刷新的 Token 仪表盘：总览卡片、模型分布、供应商分布、小时分布、用量排行。
- 桌面置顶半透明悬浮监控窗：支持右键菜单、拖动、折叠、动画更新和报警状态。
- Token 用量追踪：记录一段工作时间内的 Token 增量，并支持自定义报警阈值。
- 未开启追踪时，悬浮窗自动显示最近 1 小时 Token 用量。
- 大数字自动进位显示，中文界面使用“千 / 百万 / 亿”，英文界面使用 `K / M / B`。

### 会话浏览
- 按状态、来源、模型、供应商搜索、筛选、排序 Codex 会话。
- 会话详情按流式方式读取 JSONL，不会一次性加载超大文件。
- 在详情弹窗里直接导出 Markdown、Text 或 JSON。
- 支持归档和取消归档会话。

### 账户同步
- 修复切换 Codex 账户或 API 供应商后，历史会话“消失”的问题。
- 三层同步：SQLite `threads` 表、JSONL `session_meta`、`session_index.jsonl`。
- Dry Run 预览会先显示将要修改的数量。
- “一键同步 + 重启”会关闭 Codex，自动识别当前 `config.toml` 的供应商和模型，同步历史记录后再启动 Codex。
- 从软件内启动 Codex 前，也会自动执行一次安全同步。

### 备份与还原
- 手动完整备份 SQLite 数据库。
- 增量备份变化过的 threads。
- 还原完整备份前自动创建安全备份。
- 可配置最大备份保留数量，自动清理旧备份。

### 供应商注册
- JSON 本地供应商注册表，内置 16 个预设（OpenAI、Azure、OpenRouter、DeepSeek、Moonshot、智谱、SiliconFlow、MiniMax、阿里百炼、火山方舟、魔搭、阶跃星辰、NVIDIA 及自定义端点）。
- 供应商 Schema：短简称、国家/地区、原生币种、目录可见性、自定义 Headers、User-Agent。
- Secret 脱敏：诊断导出时自动隐藏 api_key。
- 批量模型选择：全选、全不选、只选 Vision、只选高上下文、只选低成本。
- 快捷可见性切换：隐藏、仅焦点、常驻显示、只选中的模型。

### 统一模型目录（UMC）
- 从多个供应商生成合并模型目录，模型 ID 带供应商前缀（如 `qwen/qwen3-coder-plus`）。
- 可见性策略：常驻供应商、只选中的模型、焦点供应商覆盖。
- 注入 Codex 前的目录预览。

### 自适应模型轮转（AMR）
- 内存路由引擎：轮转组 + 候选优先级。
- 能力感知路由：文本、视觉、工具、推理、图片、视频。
- 故障冷却：上游失败后自动 fallback 到下一个可用候选。
- 组上下文窗口 = 所有启用候选的最小上下文窗口。
- AMR 注册表：JSON 持久化、CRUD、从供应商动态构建候选。

### 本地代理
- 独立 HTTP 服务器（不绑定 Flask），本地运行。
- `/v1/chat/completions` 直接转发 + SSE 流式转发。
- `/v1/responses` 含 Responses↔Chat Completions 双向转换。
- `/v1/models` 返回 UMC 可见模型列表（含供应商前缀）。
- 供应商路由：`provider/model` 硬前缀或精确模型 ID 匹配。
- Windows 专项修复：IPv4 绑定、系统代理绕过、端口冲突预检。

### Codex 配置安全
- 安全读写 `~/.codex/config.toml` 和 `auth.json`，自动备份。
- 自动检测官方 OAuth / 传统 API Key 登录模式。
- 默认保留官方登录态；仅在用户显式允许时才写入第三方配置。
- 写入失败时自动 rollback。
- 写入前 diff 预览。

### 诊断
- 结构化诊断收集器：覆盖 Codex 配置、登录态、代理状态、供应商、模型目录、AMR 组、系统环境。
- 脱敏诊断导出：安全分享。
- 供应商连通性探测：HEAD 请求检测 base_url 可达性。
- 错误环形缓冲区：保留最近 50 条代理和系统错误。

### 移动修复
- 从 SQLite 和 JSONL 读取会话/工作区元数据。
- 移动预演（dry-run）：验证目标 Git 仓库和 tracked files。
- 原子移动 + 回滚：同步更新 SQLite `threads.cwd`、JSONL `session_meta.cwd`、`session_index.jsonl`。
- 移动后一致性验证。

### 桌面体验
- 使用 PyWebView 桌面窗口，后端为本地 Flask 服务。
- 支持系统托盘：最小化到托盘、从托盘恢复、关闭时询问“最小化到托盘 / 退出 / 取消”。
- 运行时调用 `tasklist`、`wmic`、`taskkill`、`where` 等命令时隐藏控制台窗口，尽量避免 CMD 黑窗闪烁。
- 自动识别 Codex 数据库、会话目录、归档目录、Codex CLI、Codex++、当前供应商和模型。
- 中英文界面切换。

## 增强路线

项目正在从历史记录管理器扩展为 Codex Enhance Manager。

**已实现：**
- ✅ Unified Model Catalog (UMC) / 统一模型目录
- ✅ Adaptive Model Rotation (AMR) / 自适应模型轮转
- ✅ Codex 本地代理（Responses/Chat 转换、登录态保留、路由诊断）
- ✅ 供应商注册表（16 个预设、批量操作、可见性控制）
- ✅ Codex 配置安全层（备份、回滚、登录态保留）
- ✅ 诊断与安全导出
- ✅ 项目/会话移动修复（dry-run + 回滚）

**进行中 / 计划：**
- 独立图片/视频供应商：支持完全兼容 OpenAI 标准的媒体代理，也支持阿里百炼、火山方舟等适配器。
- 缓存读写 Token 统计：从 Codex rollout、local proxy log、兼容代理数据库读取 cache read/write。
- 供应商余额/额度查询、细粒度成本估算、多币种显示和手动汇率覆盖。
- Codex 页面增强：会话删除、导出、时间线、对话宽度、滚动恢复。
- 分层设置交互：快速设置、预设优先、写入前预览、路由模拟器、启用前测试、Codex 配置回滚。

本地开发计划位于 `_local_notes/`，该目录已被 Git 忽略，不会推送。

## 快速开始

### 使用 Windows EXE

从 [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) 下载最新版 EXE，双击运行。

### 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

应用会打开桌面窗口，底层服务地址为 `http://127.0.0.1:51234`。

## 打包 EXE

```bash
python build_exe.py
```

打包脚本会生成单文件 EXE，并包含静态资源、应用图标、PyWebView、Flask、Pillow 和系统托盘支持。

## 账户同步原理

Codex 会按 `model_provider` 过滤会话列表。你在官方 OpenAI 账户和自定义/API 中转账户之间切换时，另一个供应商下的历史会话可能看起来像“丢了”。

同步流程会：

1. 读取当前 `~/.codex/config.toml` 中的 provider/model。
2. 更新 Codex SQLite 数据库 `threads` 表里的 `model_provider` 和 `model`。
3. 流式更新 JSONL rollout 文件第一行里的 `session_meta`。
4. 重建 `session_index.jsonl`，统一 provider/model 字段。

同步完成后，不同账户/供应商之间切换时历史会话仍然可见。

## 数据来源

Token 统计来自 Codex 数据库 `threads.tokens_used` 字段。缓存命中不会伪造；未配置兼容代理数据库时，缓存指标会显示为不支持。

JSONL 会话读取采用逐行流式读取，即使归档目录里有数 GB 的会话文件，也不会一次性读入内存。

## 隐私与本地状态

这是一个本地桌面应用。设置保存在你的机器上，诊断信息应默认脱敏 API Key、Bearer Token 和敏感 Header。后续供应商、代理、额度和成本功能会按 local-first 方式设计，并为 Codex 配置文件写入提供写入前预览、备份和回滚保护。

## 配置文件

设置保存在 `~/.codex_gui_config.json`。

| 配置项 | 说明 |
| --- | --- |
| `db_path` | Codex SQLite 数据库路径 |
| `sessions_dir` | 当前会话目录 |
| `archived_dir` | 归档会话目录 |
| `backup_dir` | 备份输出目录 |
| `codex_cli_path` | Codex CLI 可执行文件路径 |
| `codex_plus_plus_path` | Codex++ 启动器路径 |
| 代理缓存数据库 | 可选路径，用于统计代理层缓存 Token |
| `page_size` | 会话列表每页数量 |
| `backup_interval_hours` | 自动备份间隔 |
| `max_backups` | 最大备份保留数量 |
| `large_file_threshold_mb` | 大文件读取限制阈值 |

## 项目结构

```text
Codex-Enhance-Manager/
├── main.py              # PyWebView 启动入口
├── app.py               # Flask 应用和 REST API
├── config.py            # 设置管理
├── db.py                # SQLite 操作层
├── reader.py            # 流式 JSONL 读取和导出
├── backup.py            # 完整/增量备份与还原
├── sync.py              # 多账户 provider 同步引擎
├── auto_detect.py       # 路径与 provider 自动检测
├── token_stats.py       # Token 统计查询
├── build_exe.py         # PyInstaller 打包脚本
├── icon.png             # 应用图标源图
├── icon.ico             # Windows 应用/托盘图标
└── static/              # 前端 SPA 资源
```

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
