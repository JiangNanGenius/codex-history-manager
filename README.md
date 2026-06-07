# Codex Enhance Manager

> A local-first Windows desktop control center for Codex: history, token/cache usage, provider presets, unified model visibility, adaptive routing, safe Codex config previews, and a localhost proxy with redacted diagnostics.

[中文说明](README.zh-CN.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-informational.svg)](#quick-start)

Codex Enhance Manager started as a Codex history and token manager. It is now evolving into a local operations layer for Codex: multi-provider setup, model catalog visibility, request routing, cache/cost accounting, diagnostics, and recovery tools.

The project is intentionally local-first. Provider secrets, app settings, request-log metadata, backups, exports, and diagnostics stay on your machine by default.

## Current Build

| Area | Status | Notes |
| --- | --- | --- |
| Session history | Working | Browse, filter, inspect, export, archive, and repair moved sessions. |
| Token/cache usage | Working | Reads Codex DB totals, Codex rollout cache events, local proxy logs, and compatible proxy DBs. |
| Provider registry | Working | 16 built-in presets with aliases, regions, currencies, headers, User-Agent, media profile, and visibility policy. |
| Unified Model Catalog | Working | Shows selected models from multiple providers at once with provider-prefixed model IDs. |
| Adaptive Model Rotation | Working scaffold | Priority/capability routing, cooldown, context limiter, explanations, and JSON persistence. |
| Local proxy | Working scaffold | Chat, Responses, Models, media pass-through scaffolding, port backoff, route diagnostics, metadata-only logs. |
| Protocol adapters | Working scaffold | Responses <-> Chat, Anthropic Messages foundation, domestic Responses guardrails. Unknown shapes stay blocked. |
| Cost and currency | Working scaffold | Native/display currency, manual FX overrides, local estimate breakdowns, request FX snapshots. |
| Quota/balance | Working scaffold | Generic JSON endpoint probe with TTL cache and redacted failure snapshots. |
| Codex config safety | Working | Diff preview, backups, rollback, auth preservation, and explicit mutation confirmation. |

## Highlights

### Unified Provider Setup

- Local provider registry with OpenAI, Azure, OpenRouter, DeepSeek, Moonshot, Zhipu, SiliconFlow, MiniMax, Alibaba Bailian, Volcengine Ark, ModelScope, StepFun, NVIDIA, and custom endpoint presets.
- Provider fields include `short_alias`, native currency, country/region, custom headers, User-Agent, media profile, quota template, and model pricing hints.
- Provider visibility supports hidden, focused-only, always-visible, and selected-models modes.

### Unified Model Catalog

- Builds one visible catalog from multiple providers.
- Uses provider-prefixed IDs such as `qwen/qwen3-coder-plus`.
- Keeps a manual Provider Focus Switch while still preserving always-visible and selected models.
- Previews catalog output before writing anything into Codex.

### Local Proxy

- Runs as an independent localhost HTTP server, separate from Flask.
- Supports `/v1/models`, `/v1/chat/completions`, `/v1/responses`, `/v1/responses/compact`, and OpenAI-compatible media route scaffolding.
- Routes by `provider/model` hard prefix, exact model match, UMC entries, media profile, or AMR group.
- Uses strict Windows port binding where available and automatically backs off when the configured port is occupied.
- Writes metadata-only JSONL logs for non-streaming requests: endpoint, provider, model, status, duration, normalized usage, cache read/write, local cost estimate, and FX snapshot.
- Surfaces those logs in the Token Dashboard with filters, cache read/write columns, cost display, FX display, and retention cleanup.
- Does not store prompts, request bodies, raw request headers, or raw upstream responses in proxy logs.

### Usage, Cost, And Currency

- Reads collapsed Codex totals from `threads.tokens_used`.
- Adds cache read/write details from Codex rollout `token_count` events, local proxy logs, and compatible proxy databases.
- Estimates input, output, cache read, cache write, reasoning, image, and video costs.
- Supports provider/model native currency, display currency, manual FX overrides, cached rates, and per-request FX snapshots.
- Online exchange-rate adapters are intentionally blocked until the official API shape is reachable and verified.

### Diagnostics And Recovery

- Redacted diagnostics cover Codex config, auth mode, local proxy status, providers, UMC, AMR, quota snapshots, request-log summaries, and system environment.
- Config/auth writes create backups and support rollback.
- Thread/project move repair updates SQLite, JSONL metadata, and `session_index.jsonl` with dry-run and rollback.
- Cleanup APIs use allowlists and confirmation phrases.

## Safety Model

| Boundary | Rule |
| --- | --- |
| Codex auth/config/model catalog/process writes | Preview and dry-run are allowed here; real mutation testing must be performed manually by the user. |
| Protocol conversion | No guessed adapters. Responses, Chat, Anthropic, SSE, tools, media, and domestic provider differences must be verified from official docs/source or explicit source analysis. |
| Secrets | API keys, bearer tokens, and sensitive headers are redacted in diagnostics and request logs. |
| Local-only materials | `_local_notes/`, `research/`, diagnostics with secrets, and temporary research output are ignored and must not be pushed. |
| Codex++ content | Useful implementation ideas may be studied, but sponsor/recommendation/ad content is not migrated. |

## Quick Start

### Windows EXE

Download the latest build from [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) and run the EXE.

### From Source

```bash
pip install -r requirements.txt
python main.py
```

The desktop window is backed by a local Flask service at `http://127.0.0.1:51234`.

### Build EXE

```bash
python build_exe.py
```

The build script creates a single-file Windows EXE with bundled static assets, icons, PyWebView, Flask, Pillow, and tray support.

## Storage

User data defaults to:

```text
Documents/Codex Enhance Manager/
```

Legacy settings from `~/.codex_gui_config.json` are imported when present.

| Path or setting | Purpose |
| --- | --- |
| `config.json` | Main app settings. |
| `providers/providers.json` | Local provider registry. |
| `logs/proxy_requests.jsonl` | Metadata-only local proxy request log. |
| `backups/` and `codex_backups/` | App and Codex config backups. |
| `diagnostics/` | Redacted diagnostics bundles. |
| `exports/` | User-requested exports. |
| `temp/` | Temporary app files. |

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
| `providers.py` | Provider registry, presets, schema normalization, redaction. |
| `model_catalog.py` | Unified Model Catalog generation. |
| `model_rotation.py`, `amr_registry.py` | Adaptive Model Rotation engine and persistence. |
| `proxy_server.py` | Local OpenAI-compatible proxy server. |
| `request_logs.py` | Metadata-only proxy request logs, retention, summaries, cost snapshots. |
| `responses_adapter.py` | Responses <-> Chat conversion and SSE normalization. |
| `anthropic_adapter.py` | Anthropic Messages adapter foundation. |
| `domestic_responses.py` | Alibaba Bailian and Volcengine Ark Responses compatibility profiles and guardrails. |
| `media_proxy.py` | OpenAI-compatible image/video route helpers. |
| `codex_config.py` | Codex config/auth backup, diff preview, write, restore. |
| `codex_rollout_usage.py`, `token_stats.py` | Token/cache usage readers. |
| `currency.py`, `costing.py`, `quota.py` | FX snapshots, local cost estimates, generic quota probes. |
| `diagnostics.py`, `move_repair.py` | Safe diagnostics and project/thread move repair. |

## Roadmap

| Next area | Direction |
| --- | --- |
| Media adapters | Add real Alibaba Bailian and Volcengine Ark image/video adapters after payload, polling, and response formats are verified. |
| Streaming logs | Record streaming proxy requests only after lifecycle, final usage, and half-closed stream semantics are confirmed. |
| Cost dashboard | Add UI columns for native/display currency, cache read/write, reasoning, image, video, and provider-reported-vs-estimated costs. |
| Quota integrations | Layer provider-specific balance/quota endpoints on top of the generic probe scaffold. |
| Theme system | Add richer theme presets, custom theme editing, and theme import. |
| Codex approval/sandbox repair | Audit official Codex approval paths and sandbox config handling before implementing fixes. |
| Packaging | Build and publish a fresh EXE once the proxy/protocol layer reaches a stable milestone. |

## License

Apache License 2.0. See [LICENSE](LICENSE).
