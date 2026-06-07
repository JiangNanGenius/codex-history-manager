# Codex Enhance Manager

<p align="center">
  <strong>A local-first Windows control center for Codex power users.</strong>
</p>

<p align="center">
  Manage Codex history, token/cache usage, provider presets, unified model visibility,
  adaptive routing, local proxy diagnostics, safe config previews, and recovery tools.
</p>

<p align="center">
  <a href="README.zh-CN.md">中文说明</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases">Releases</a>
  ·
  <a href="#quick-start">Quick Start</a>
  ·
  <a href="#safety-model">Safety Model</a>
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

## Why This Exists

Codex is strongest when it can stay logged in, keep its local history intact, and move between providers without breaking config, sandbox, or token accounting. Codex Enhance Manager is the local operations layer around that workflow.

It began as a Codex history and token manager. It is now growing into a safer desktop toolkit for provider setup, model catalog visibility, local proxy routing, usage/cost analysis, diagnostics, and rollback.

The project is intentionally local-first: provider settings, backups, request-log metadata, diagnostics, exports, and temporary files live on your machine by default.

## What You Can Do Today

| Area | Current state |
| --- | --- |
| History and usage | Browse sessions, inspect heavy conversations, read Codex DB totals, and add cache read/write usage from rollout events, proxy logs, and compatible proxy DBs. |
| Provider setup | Manage provider presets with `short_alias`, region, currency, custom headers, `User-Agent`, media profile, quota template, and catalog visibility. |
| Unified Model Catalog | Preview Codex-visible model IDs such as `qwen/qwen3-coder-plus` from always-visible, selected, focused-provider, and AMR entries. |
| Local proxy | Run an independent localhost proxy with automatic occupied-port backoff, route diagnostics, metadata-only request logs, and OpenAI-compatible route scaffolding. |
| Protocol adapters | Convert verified Responses, Chat, Anthropic Messages, tools, images, SSE events, and domestic Responses profiles only where behavior is sourced. |
| Cost and currency | Estimate input/output/cache/reasoning/media cost, preserve per-request FX snapshots, support manual FX overrides, and keep online FX blocked until verified. |
| Config recovery | Preview Codex config diffs, audit approval/sandbox settings, create backups, restore config/auth, preserve official login state, and repair moved session/project metadata. |
| Settings polish | Use richer built-in themes, full custom theme colors, theme import/export, settings import/export, startup/elevation preview, cleanup preview/execute, and uninstall cleanup write-lock. |

## The Desktop Surface

The app is organized as an operational console rather than a marketing dashboard.

- **Overview**: health, current paths, guardrails, and high-signal status.
- **Token Dashboard**: Codex totals, cache read/write, request-log summaries, cost snapshots, and floating token monitor controls.
- **Providers**: preset-first setup, focused editing, section-local testing, status strips, custom `User-Agent`, and visibility policy.
- **Unified Model Catalog**: preview before writing anything into Codex.
- **Local Proxy**: start/stop/status, actual bound port, route explanations, and log retention.
- **Settings**: storage paths, theme editor, import/export, startup/elevation controls, safe cleanup, uninstall cleanup, currency settings, and monitor field customization.
- **Diagnostics and Recovery**: redacted diagnostics, approval/sandbox audit, backup/restore, rollback, and move repair.

## Safety Model

| Boundary | Rule |
| --- | --- |
| Codex auth/config/model catalog/process writes | Read-only checks, dry-runs, and previews are allowed in this Codex window. Real mutation testing must be performed manually by the user. |
| Windows startup/elevation writes | Status and preview are safe to run here. Creating/removing Startup folder entries or Task Scheduler jobs requires typed confirmation and manual user testing. |
| Protocol conversion | No guessed adapters. Responses, Chat, Anthropic, SSE, tool, media, and domestic provider differences must be verified from official docs/source or explicit source analysis. |
| Secrets | API keys, bearer tokens, and sensitive headers are redacted in diagnostics and request logs. |
| Request logs | Local proxy logs are metadata-only. They do not store prompts, request bodies, raw headers, or raw upstream responses. |
| Local-only materials | `_local_notes/`, `research/`, diagnostics with secrets, and temporary research output are ignored and must not be pushed. |
| Codex++ content | Implementation ideas may be studied. Sponsor, recommendation, marketplace ad, or promotional content is not migrated. |

## Quick Start

### Windows EXE

Download the latest build from [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) and run the EXE.

### From Source

```bash
pip install -r requirements.txt
python main.py
```

The desktop window is backed by a local Flask service at:

```text
http://127.0.0.1:51234
```

### Build EXE

```bash
python build_exe.py
```

The build script creates a single-file Windows EXE with bundled static assets, icon, PyWebView, Flask, Pillow, and tray support.

## Local Storage

New user data defaults to:

```text
Documents/Codex Enhance Manager/
```

Legacy settings from `~/.codex_gui_config.json` are imported when present.

| Path | Purpose |
| --- | --- |
| `config.json` | Main app settings. |
| `providers/providers.json` | Local provider registry. |
| `logs/proxy_requests.jsonl` | Metadata-only local proxy request log. |
| `backups/`, `codex_backups/` | App and Codex config backups. |
| `diagnostics/` | Redacted diagnostics bundles. |
| `exports/` | User-requested exports. |
| `temp/` | Temporary app files. |

## Provider And Model Flow

Codex Enhance Manager treats model visibility separately from routing.

1. Add or import providers with aliases such as `qwen`, `ds`, `kimi`, or `openai`.
2. Choose which providers or models are always visible.
3. Select extra models manually when needed.
4. Use the Provider Focus Switch to temporarily show every model from one provider.
5. Preview the final Codex catalog before any Codex config write.

The visible catalog is built from:

```text
always-visible models
+ selected models
+ focused-provider models
+ Adaptive Model Rotation groups
```

## Local Proxy Logging

The local proxy writes non-streaming request metadata into `logs/proxy_requests.jsonl`.

Recorded:

- endpoint, provider, model, status, duration
- normalized input/output/cache/reasoning/media usage
- local cost estimate and FX snapshot
- safe route diagnostics

Never recorded:

- prompt text
- raw request body
- raw request headers
- raw upstream response

## Account Sync

Codex filters sessions by `model_provider`. Switching between official OpenAI login and custom/API-provider accounts can make sessions from the other provider appear to vanish.

The sync flow:

1. Reads the current `~/.codex/config.toml` provider/model.
2. Updates `model_provider` and `model` in the Codex SQLite `threads` table.
3. Updates JSONL `session_meta` with a streaming first-line rewrite.
4. Rebuilds `session_index.jsonl` with unified provider/model fields.

After sync, sessions remain visible across account/provider switches.

## Core Modules

| Module | Role |
| --- | --- |
| `app.py` | Flask API surface and desktop backend orchestration. |
| `main.py` | PyWebView desktop entry point. |
| `app_paths.py`, `config.py` | Documents-based storage and settings migration. |
| `startup_manager.py` | Windows Startup folder and Task Scheduler preview/apply/remove integration with confirmation guardrails. |
| `providers.py` | Provider registry, presets, schema normalization, and redaction. |
| `model_catalog.py` | Unified Model Catalog generation and preview. |
| `model_rotation.py`, `amr_registry.py` | Adaptive Model Rotation engine and persistence. |
| `proxy_server.py` | Local OpenAI-compatible proxy server. |
| `request_logs.py` | Metadata-only proxy request logs, retention, summaries, and cost snapshots. |
| `responses_adapter.py` | Responses <-> Chat conversion and SSE normalization. |
| `anthropic_adapter.py` | Anthropic Messages adapter foundation. |
| `domestic_responses.py` | Alibaba Bailian and Volcengine Ark Responses profiles and guardrails. |
| `media_proxy.py` | OpenAI-compatible image/video route helpers. |
| `codex_config.py` | Codex config/auth backup, diff preview, write, and restore. |
| `codex_permissions.py` | Source-verified Codex approval/sandbox config audit and diff preview. |
| `codex_rollout_usage.py`, `token_stats.py` | Token/cache usage readers. |
| `currency.py`, `costing.py`, `quota.py` | FX snapshots, local cost estimates, and generic quota probes. |
| `diagnostics.py`, `move_repair.py` | Safe diagnostics and project/thread move repair. |

## Roadmap

| Next | Direction |
| --- | --- |
| Protocol verification | Continue source/doc comparison for official Codex, domestic Responses, Anthropic, tools, SSE, compacting, and media item behavior. |
| Media adapters | Add real Alibaba Bailian and Volcengine Ark image/video adapters after payload, polling, cancel, and response formats are verified. |
| Approval and sandbox repair | Expand the source-verified approval/sandbox audit into corruption repair presets once user-manual write testing is complete. |
| Startup integration | Manually verify Startup folder and Task Scheduler highest-privilege flows from the packaged EXE, then polish UX around UAC/task errors. |
| Cost dashboard | Add deeper native/display currency comparison, stale FX warnings, provider-reported-vs-estimated cost, and media pricing tiers. |
| Quota integrations | Layer provider-specific balance/quota endpoints on top of the generic probe scaffold. |
| UI polish | Continue cleaning legacy copy, icons, i18n coverage, screenshots, and narrow-window layout checks. |
| Packaging | Build and publish a fresh EXE once the proxy/protocol layer reaches a stable milestone. |

## License

Apache License 2.0. See [LICENSE](LICENSE).
