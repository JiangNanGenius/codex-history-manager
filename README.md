# Codex Enhance Manager

> A Windows desktop control center for Codex. It currently manages chat history, account/provider sync, token usage, and backups, and is being expanded into a local provider, routing, media, quota, and cost-management layer for Codex.

[中文说明](README.zh-CN.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## Current Features

### Token Monitoring
- Always-on token dashboard with overview cards, model/provider charts, hourly distribution, and top sessions.
- Desktop always-on-top semi-transparent token monitor with right-click menu, drag, collapse, animation, and alert state.
- Token usage tracker for a work interval, with configurable alert threshold.
- When tracking is not active, the floating monitor shows the last 1 hour of token usage.
- Large numbers automatically compact into K/M/B or localized Chinese units.

### Session Browser
- Browse, search, sort, and filter Codex sessions by status, source, model, and provider.
- View conversation details without loading entire huge JSONL files into memory.
- Export a session as Markdown, text, or JSON from the detail dialog.
- Archive and unarchive sessions.

### Account Sync
- Fixes the common provider-switch problem where sessions disappear after changing Codex accounts.
- Syncs three layers: `threads` in SQLite, JSONL `session_meta`, and `session_index.jsonl`.
- Dry-run preview shows the exact change count before writing.
- One-click sync + restart closes Codex, auto-detects the current `config.toml` provider/model, syncs, then starts Codex.
- Starting Codex from the app also performs an automatic safety sync first.

### Backup And Restore
- Full SQLite backups before destructive actions.
- Incremental backups for changed threads.
- Restore full backups with a pre-restore safety backup.
- Old backup pruning by configurable retention count.

### Provider Registry
- JSON-backed local provider registry with 16 built-in presets (OpenAI, Azure, OpenRouter, DeepSeek, Moonshot, Zhipu, SiliconFlow, MiniMax, Alibaba Bailian, Volcengine Ark, ModelScope, StepFun, NVIDIA, and custom endpoints).
- Provider schema with short alias, country/region, native currency, catalog visibility, custom headers, and User-Agent.
- Secret redaction for safe diagnostics export.
- Bulk model selection actions: select all, deselect all, select vision-capable, select high-context, select low-cost.
- Provider visibility quick toggle: hidden, focused only, always visible, selected models.

### Unified Model Catalog (UMC)
- Generates a combined model catalog from multiple providers with provider-prefixed model IDs (`qwen/qwen3-coder-plus`).
- Visibility policies: always-visible providers, selected models only, focus provider override.
- Catalog preview before injecting into Codex.

### Adaptive Model Rotation (AMR)
- In-memory routing engine with rotation groups and candidate priorities.
- Capability-aware routing: text, vision, tools, reasoning, images, videos.
- Failure cooldown with automatic fallback to the next capable candidate.
- Group context window = minimum enabled candidate context.
- AMR registry with JSON persistence, CRUD, and dynamic candidate building from providers.

### Local Proxy
- Independent HTTP server (not Flask-bound) running on localhost.
- `/v1/chat/completions` direct pass-through and SSE streaming.
- `/v1/responses` with Responses-to-Chat Completions conversion and back.
- `/v1/models` returning UMC visible models with provider prefixes.
- Provider routing by `provider/model` hard prefix or exact model ID match.
- Windows-specific fixes: IPv4 binding, system proxy bypass, port conflict pre-check.

### Codex Config Safety
- Safe read/write for `~/.codex/config.toml` and `auth.json` with backups.
- Automatic detection of official OAuth vs legacy API key auth mode.
- Preserves official login state by default; writes third-party config only when explicitly allowed.
- Rollback on write failure.
- Diff preview before writing.

### Diagnostics
- Structured diagnostics collector covering Codex config, auth mode, proxy status, providers, model catalog, AMR groups, and system environment.
- Redacted diagnostics export for safe sharing.
- Provider connectivity probe (HEAD request to base URL).
- Error ring buffer tracking the last 50 proxy and system errors.

### Move Repair
- Thread/workspace metadata reader from SQLite and JSONL.
- Move dry-run with Git repo and tracked-file verification.
- Atomic move with rollback: updates SQLite `threads.cwd`, JSONL `session_meta.cwd`, and `session_index.jsonl`.
- Post-move consistency verification.

### Desktop Experience
- PyWebView desktop window with local Flask backend.
- System tray support: minimize to tray, restore from tray, and close prompt with tray/exit/cancel choices.
- Runtime subprocess calls use hidden Windows flags to avoid flashing CMD windows.
- Auto-detects Codex DB, sessions directory, archived sessions, Codex CLI, Codex++, and current provider/model.
- Bilingual UI: Chinese and English.

## Enhancement Roadmap

The project is being expanded beyond history management.

**Implemented:**
- ✅ Unified Model Catalog (UMC): show selected models from multiple providers at the same time, with provider aliases and always-visible models.
- ✅ Adaptive Model Rotation (AMR): route each request by priority, capability, context window, health, quota, and fallback policy.
- ✅ Local proxy for Codex with Responses/Chat conversion, auth preservation, and route diagnostics.
- ✅ Provider registry with 16 presets, bulk actions, and visibility controls.
- ✅ Codex config safety layer with backup, rollback, and auth preservation.
- ✅ Diagnostics and safe export.
- ✅ Project/conversation move repair with dry-run and rollback.

**In Progress / Planned:**
- Independent image/video providers, including OpenAI-compatible pass-through and adapters for Alibaba Bailian and Volcengine Ark.
- Cache read/write token accounting from Codex rollout logs, local proxy logs, and compatible proxy databases.
- Provider balance/quota checks, detailed cost estimates, multi-currency display, and manual exchange-rate overrides.
- Codex page enhancements: session delete, export, timeline, conversation width, scroll restore.
- Carefully layered settings UX: quick setup, presets, preview-before-write, route simulator, tests before enabling, and rollback for Codex config changes.

Local development plans live under `_local_notes/` and are intentionally ignored by Git.

## Quick Start

### Windows EXE

Download the latest release from [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) and run the bundled EXE.

### From Source

```bash
pip install -r requirements.txt
python main.py
```

The app opens a desktop window backed by `http://127.0.0.1:51234`.

## Build EXE

```bash
python build_exe.py
```

The build script creates a single-file EXE with bundled static assets, app icon, PyWebView, Flask, Pillow, and tray support.

## How Account Sync Works

Codex filters the session list by `model_provider`. If you switch between the official OpenAI account and a custom/API-provider account, sessions from the other provider can appear to vanish.

Sync solves this by:

1. Reading the current `~/.codex/config.toml` provider/model.
2. Updating `model_provider` and `model` in the Codex SQLite `threads` table.
3. Updating `session_meta` in JSONL rollout files using a streaming first-line rewrite.
4. Rebuilding `session_index.jsonl` with unified provider/model fields.

After sync, sessions stay visible across account/provider switches.

## Data Sources

Token statistics come from the `tokens_used` column in Codex's `threads` table. Cache-hit metrics are not fabricated; they are shown as unsupported unless a compatible proxy database is configured.

Huge JSONL files are read line by line, so multi-GB archived sessions can be inspected without loading the full file into memory.

## Privacy And Local State

This is a local desktop application. Settings are stored on your machine, and diagnostics should redact API keys, bearer tokens, and sensitive headers. Future provider, proxy, quota, and cost features are planned with local-first storage, preview-before-write, and backup/rollback safeguards for Codex configuration files.

## Configuration

Settings are stored in `~/.codex_gui_config.json`.

| Setting | Description |
| --- | --- |
| `db_path` | Codex SQLite database path |
| `sessions_dir` | Active Codex sessions directory |
| `archived_dir` | Archived Codex sessions directory |
| `backup_dir` | Backup output directory |
| `codex_cli_path` | Codex CLI executable path |
| `codex_plus_plus_path` | Codex++ launcher path |
| Proxy cache database | Optional path for proxy cache-token statistics |
| `page_size` | Session list page size |
| `backup_interval_hours` | Auto-backup interval |
| `max_backups` | Backup retention count |
| `large_file_threshold_mb` | Threshold for large-file reading limits |

## Project Structure

```text
Codex-Enhance-Manager/
├── main.py              # PyWebView entry point
├── app.py               # Flask app and REST API
├── config.py            # Settings management
├── db.py                # SQLite operations
├── reader.py            # Streaming JSONL reader/exporter
├── backup.py            # Full/incremental backup and restore
├── sync.py              # Multi-account provider sync engine
├── auto_detect.py       # Path and provider auto-detection
├── token_stats.py       # Token statistics queries
├── build_exe.py         # PyInstaller packaging script
├── icon.png             # Source app icon
├── icon.ico             # Windows app/tray icon
└── static/              # Frontend SPA assets
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
