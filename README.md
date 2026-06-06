# Codex History Manager

> A Windows desktop manager for Codex chat history: browse sessions, sync account providers, monitor token usage, and manage safety backups.

[中文说明](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

## Features

### Token Monitoring
- Always-on token dashboard with overview cards, model/provider charts, hourly distribution, and top sessions.
- Floating semi-transparent token monitor with drag, collapse, animation, and alert state.
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

### Desktop Experience
- PyWebView desktop window with local Flask backend.
- System tray support: minimize to tray, restore from tray, and close prompt with tray/exit/cancel choices.
- Runtime subprocess calls use hidden Windows flags to avoid flashing CMD windows.
- Auto-detects Codex DB, sessions directory, archived sessions, Codex CLI, Codex++, and current provider/model.
- Bilingual UI: Chinese and English.

## Quick Start

### Windows EXE

Download the latest release from [Releases](https://github.com/JiangNanGenius/codex-history-manager/releases) and run `CodexHistoryManager.exe`.

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
| `cc_switch_db_path` | Optional proxy database path for future cache statistics |
| `page_size` | Session list page size |
| `backup_interval_hours` | Auto-backup interval |
| `max_backups` | Backup retention count |
| `large_file_threshold_mb` | Threshold for large-file reading limits |

## Project Structure

```text
codex-history-manager/
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

MIT. See [LICENSE](LICENSE).
