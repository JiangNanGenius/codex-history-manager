# Codex Enhance Manager

<p align="center">
  <strong>A local-first Windows control center for Codex sessions, providers, routing, usage, and recovery.</strong>
</p>

<p align="center">
  <a href="README.zh-CN.md">中文说明</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager">Repository</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases">Releases</a>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img alt="License: Apache-2.0" src="https://img.shields.io/badge/License-Apache--2.0-green.svg">
  </a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-blue.svg">
  <img alt="Platform: Windows" src="https://img.shields.io/badge/Platform-Windows-informational.svg">
  <img alt="Design: local first" src="https://img.shields.io/badge/Design-local--first-0f766e.svg">
</p>

---

## What It Is

Codex Enhance Manager is a desktop companion for people who want Codex to stay logged in, keep local history visible, switch providers without losing context, and understand token usage without digging through files.

It runs locally on Windows. Provider settings, backups, request metadata, diagnostics, exports, and temporary files stay on your machine by default. Sensitive keys and tokens are hidden in the interface, diagnostics, and logs unless you explicitly handle them yourself.

## What It Does

| Area | User-facing result |
| --- | --- |
| Setup wizard | Walk through Codex paths, provider setup, model capabilities, routing, media fallback, usage alerts, startup, and save checks step by step. |
| Provider management | Add one or many providers, give each provider multiple models, set headers and `User-Agent`, map model names, and mark model-level capabilities. |
| Codex connection | Choose official login, official login with local proxy/API routing, or non-official provider mode. Official direct mode keeps provider routing changes locked. |
| Responses and Chat | Treat native Responses providers, compatible Responses providers, and Chat providers differently at the model level. |
| Model catalog | Decide which models Codex can see while keeping provider setup separate from new-session rotation strategy. |
| Model rotation | Configure the next-new-session order, priorities, capability filtering, and failover behavior without mixing it into provider credentials. |
| Image and video | Assign media-capable providers or enable global fallback so text models can borrow image/video generation from a selected provider. |
| Auto approval | Keep low-risk approval handling on by default, let users edit the system prompt, and require strict JSON decisions from the reviewer. |
| Usage and cost | Read Codex token totals, cache usage, proxy request metadata, local estimates, and optional provider-reported cost fields when available. |
| Floating monitor | Show token usage in a native floating window with tray actions, quick provider switching, opacity settings, rounded corners, and background refresh. |
| Recovery and updates | Back up and restore Codex config/auth, repair moved sessions, run redacted diagnostics, check GitHub Releases, and download newer EXE builds. |

## Connection Modes

| Mode | Best for | Behavior |
| --- | --- | --- |
| Official login direct | Users who want Codex to use the official account unchanged. | Keeps official OAuth state and disables local routing-changing provider features. Read-only display enhancements can still be used. |
| Keep login plus proxy/API | Users who want to keep the official login while routing selected traffic through a local proxy or configured API. | Preserves login state, but only changes routing when the user chooses this mode. |
| Third-party provider | Users running Codex through custom providers or proxy vendors. | Enables provider credentials, model-level Responses/Chat selection, media fallback, model mapping, quota scripts, and rotation. |

## Provider And Routing Boundary

Provider pages are for connection details: keys, base URLs, headers, `User-Agent`, model names, model capabilities, media support, quota scripts, and visibility.

Model Rotation is for what happens at the next new session: order, priority, capability matching, failover, and dynamic switching after the current session ends.

Codex Connection is for how Codex is launched and written to: official login preservation, proxy/API injection, config updates, backups, and restore points.

## Quick Start

### Windows EXE

Download the latest Windows build from [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases), then run `CodexHistoryManager.exe`.

### From Source

```bash
pip install -r requirements.txt
python main.py
```

The desktop window is backed by a local Flask service:

```text
http://127.0.0.1:51234
```

## Build And Release

Build and verify the Windows EXE with:

```bash
python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest
```

The release asset is:

```text
dist/CodexHistoryManager.exe
```

Every GitHub Release must include the packaged EXE and `dist/release-manifest.json`. Source archives alone are not a complete user release. Release descriptions should include both Chinese and English notes; see `RELEASE_NOTES.md`.

## Local Storage

New user data defaults to:

```text
Documents/Codex Enhance Manager/
```

| Path | Purpose |
| --- | --- |
| `config.json` | Main app settings. |
| `providers/providers.json` | Local provider registry. |
| `logs/proxy_requests.jsonl` | Metadata-only local proxy request log. |
| `backups/`, `codex_backups/` | App and Codex config backups. |
| `diagnostics/` | Redacted diagnostics bundles. |
| `exports/` | User-requested exports. |
| `temp/` | Temporary app files. |

## Development Checks

```bash
python -m pytest -q
node --check static/js/i18n.js static/js/providers.js static/js/amr.js static/js/sync.js
python -m py_compile approval_broker.py app.py main.py providers.py capabilities.py
```

## References

- [OpenAI Codex source](https://github.com/openai/codex)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat)
- [OpenAI Images API](https://platform.openai.com/docs/api-reference/images)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)

## License

Apache License 2.0. See [LICENSE](LICENSE).
