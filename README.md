# Codex History Manager

> 🚀 Modern web-based manager for OpenAI Codex chat history — browse sessions, sync across accounts, track token usage, and manage backups.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

![Dark Theme Dashboard](https://img.shields.io/badge/UI-Dark%20Theme-1a1a2e)

## ✨ Features

### 📊 Token Statistics Dashboard
- **Overview cards**: Total tokens, sessions, today/week/month usage
- **Daily trend chart**: 30-day token usage line chart
- **Model distribution**: Doughnut chart by model (GPT-5.5, Codex Auto Review, etc.)
- **Provider breakdown**: Bar chart by provider (OpenAI, Custom, etc.)
- **Hourly heatmap**: Usage distribution across 24 hours
- **Top sessions**: Ranking of most token-intensive conversations

### 💬 Session Browser
- Browse, search, and filter all Codex chat sessions
- View full conversation history with code highlighting
- Archive/unarchive sessions
- Support for超大 JSONL files (8GB+) via streaming reader

### 🔄 Account Sync
- **Multi-account sync**: Unify `model_provider` across all sessions so switching accounts doesn't "lose" history
- **Three-layer sync**: Database threads → JSONL session_meta → session_index.jsonl
- **One-click sync & restart**: Close Codex → Sync → Restart (supports Codex++)
- **Dry-run preview**: See what will change before executing

### 💾 Backup System
- Full backup (SQLite database)
- Incremental backup (changed threads only)
- Auto-scheduled backups
- One-click restore with pre-restore safety backup

### 🔍 Auto-Detection
- Automatically finds Codex database (`state_*.sqlite`)
- Detects Codex CLI and Codex++ installation paths
- Reads current config from `config.toml`
- No manual configuration needed for standard installations

## 🚀 Quick Start

### Option 1: Download EXE (Windows)

Download the latest release from [Releases](https://github.com/JiangNanGenius/codex-history-manager/releases) and double-click to run.

### Option 2: Run from Source

```bash
# Install dependencies
pip install flask

# Run the application
python main.py
```

The app will start a local web server and automatically open your browser at `http://localhost:51234`.

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11+ · Flask |
| **Frontend** | HTML5 · Tailwind CSS (CDN) · Chart.js (CDN) · Vanilla JS |
| **Database** | SQLite 3 (Codex's native `state_5.sqlite`) |
| **Packaging** | PyInstaller (single-file EXE) |

## 📁 Project Structure

```
codex-history-manager/
├── main.py              # Entry point: starts Flask + opens browser
├── app.py               # Flask app + all API endpoints
├── config.py            # Configuration management
├── db.py                # SQLite operations layer
├── reader.py            # JSONL streaming reader (handles 8GB+ files)
├── backup.py            # Backup system (full/incremental/auto)
├── sync.py              # Multi-account sync engine
├── auto_detect.py       # Auto-detect Codex paths
├── token_stats.py       # Token statistics query engine
├── build_exe.py         # PyInstaller packaging script
├── static/              # Frontend files
│   ├── index.html       # SPA main page
│   ├── css/style.css    # Custom styles
│   └── js/              # JavaScript modules
│       ├── app.js       # Main app logic & routing
│       ├── sessions.js  # Session browser
│       ├── sync.js      # Account sync
│       ├── backup.js    # Backup management
│       ├── stats.js     # Token statistics & charts
│       └── settings.js  # Settings panel
├── LICENSE              # MIT License
└── README.md            # This file
```

## 🔧 How Account Sync Works

Codex filters the TUI session list by `model_provider`. When you switch between the official OpenAI account and a custom API account, sessions from the other provider "disappear".

**Sync solves this by:**
1. Reading your current `config.toml` to get the active `model_provider` and `model`
2. Updating all `threads` rows in `state_5.sqlite` to match
3. Updating `session_meta` headers in all JSONL rollout files
4. Rebuilding `session_index.jsonl` with unified provider/model

After sync, **all sessions are visible regardless of which account you're using**.

## 📊 Token Statistics Data Source

Token usage data comes directly from the `tokens_used` column in Codex's `threads` table. Statistics include:

- Total tokens consumed across all sessions
- Breakdown by model (GPT-5.5, Codex Auto Review, DeepSeek V4, etc.)
- Breakdown by provider (OpenAI, Custom)
- Daily/weekly/monthly trends
- Per-hour usage distribution
- Top sessions by token consumption

## ⚙️ Configuration

Settings are stored in `~/.codex_gui_config.json`. All paths are auto-detected on first run.

| Setting | Default | Description |
|---------|---------|-------------|
| `db_path` | Auto-detected | Codex SQLite database path |
| `sessions_dir` | `~/.codex/sessions` | Active sessions directory |
| `archived_dir` | `~/.codex/archived_sessions` | Archived sessions directory |
| `backup_dir` | `~/codex_backups` | Backup output directory |
| `codex_cli_path` | Auto-detected | Codex CLI executable path |
| `codex_plus_plus_path` | Auto-detected | Codex++ launcher path |
| `max_backups` | 20 | Maximum backup files to keep |
| `large_file_threshold_mb` | 500 | MB threshold for large file handling |

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [pangkk18/codex-history-sync](https://github.com/pangkk18/codex-history-sync) — Original sync logic reference
- [farion1231/cc-switch](https://github.com/farion1231/cc-switch) — UI design inspiration
- OpenAI Codex — The tool this manager is built for
