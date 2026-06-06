# Codex 历史记录管理器

> 面向 Windows 桌面的 Codex 历史记录管理工具：浏览会话、同步不同账户的历史记录、监控 Token 用量、管理安全备份。

[English](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## 功能亮点

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

### 桌面体验
- 使用 PyWebView 桌面窗口，后端为本地 Flask 服务。
- 支持系统托盘：最小化到托盘、从托盘恢复、关闭时询问“最小化到托盘 / 退出 / 取消”。
- 运行时调用 `tasklist`、`wmic`、`taskkill`、`where` 等命令时隐藏控制台窗口，尽量避免 CMD 黑窗闪烁。
- 自动识别 Codex 数据库、会话目录、归档目录、Codex CLI、Codex++、当前供应商和模型。
- 中英文界面切换。

## 快速开始

### 使用 Windows EXE

从 [Releases](https://github.com/JiangNanGenius/codex-history-manager/releases) 下载最新版 `CodexHistoryManager.exe`，双击运行。

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
codex-history-manager/
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

MIT。详见 [LICENSE](LICENSE)。
