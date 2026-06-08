<p align="center">
  <img src="icon.png" alt="Codex Enhance Manager icon" width="96">
</p>

<h1 align="center">Codex Enhance Manager</h1>

<p align="center">
  <strong>Keep Codex native. Add the control panel it should have had.</strong>
</p>

<p align="center">
  Official login switching · local proxy routing · smart routing · token telemetry · recovery tools
</p>

<p align="center">
  <a href="README.zh-CN.md">中文</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases">Download</a>
  ·
  <a href="RELEASE_NOTES.md">Release notes</a>
  ·
  <a href="LICENSE">License</a>
</p>

<p align="center">
  <img alt="Platform: Windows" src="https://img.shields.io/badge/Platform-Windows-2563eb.svg">
  <img alt="Local first" src="https://img.shields.io/badge/Local--first-by_default-0f766e.svg">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-334155.svg">
  <img alt="License: Apache-2.0" src="https://img.shields.io/badge/License-Apache--2.0-green.svg">
</p>

---

## Why This Exists

Codex feels best when it behaves like Codex: logged in, local, fast to launch, and not buried under provider plumbing. Codex Enhance Manager keeps that native experience, then adds the parts power users keep reaching for: provider control, safe routing, readable usage, backup/repair tools, and a floating monitor that stays out of the way.

It is a Windows desktop app backed by a local service. Your settings, providers, request metadata, diagnostics, backups, and exports stay on your machine by default.

## At A Glance

<table>
  <tr>
    <td width="50%">
      <strong>Official login stays official</strong><br>
      Detects ChatGPT/OAuth login, shows the effective OpenAI state, and keeps official direct mode out of local proxy routing and Smart Routing.
    </td>
    <td width="50%">
      <strong>Routing when you actually want it</strong><br>
      Starts a local proxy for routed modes, writes the real backoff port into Codex config, and protects the proxy with a high-entropy bearer token.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>Model control without credential clutter</strong><br>
      Provider setup owns keys, base URLs, headers, capabilities, and media support. Model rotation owns new-session order, failover, and capability matching.
    </td>
    <td width="50%">
      <strong>Recovery for real failure modes</strong><br>
      Repair broken Codex config, reset risky official-login transitions with explicit warnings, prune backups, and inspect redacted diagnostics.
    </td>
  </tr>
</table>

## Connection Modes

| Mode | Use It When | What Happens |
| --- | --- | --- |
| Official direct | You want Codex to use the official account exactly as-is. | Keeps OAuth login intact, locks routing-changing provider behavior, and still allows safe UI enhancement injection. |
| Login plus proxy/API | You want the official login preserved while using local proxy/API routing. | Starts the local proxy, writes the active port and bearer token into Codex provider config, then syncs history with progress. |
| Third-party provider | You run Codex through custom vendors, proxy providers, or compatibility APIs. | Enables provider credentials, Responses/Chat selection, model mapping, media fallback, quotas, and Smart Routing. |

## What You Get

- A setup flow for Codex paths, official login state, providers, model capabilities, routing, media fallback, startup, and save checks.
- Provider management with multiple models per provider, custom headers, `User-Agent`, model aliases, capability flags, and media routing.
- Model rotation for next-session order, priority, fallback, and capability filtering without mixing it into provider credentials.
- Token and cost visibility from Codex usage, cache totals, proxy metadata, local estimates, and provider-reported fields when available.
- A native floating monitor with tokens, cache, context, one-hour usage, token speed, opacity settings, tray actions, and quick switching.
- Backup, restore, config-template repair, moved-session repair, redacted diagnostics, update checks, and packaged EXE release support.

## Safety Model

- Local-first storage under `Documents/Codex Enhance Manager/`.
- API keys, bearer tokens, and sensitive headers are redacted in settings exports, diagnostics, and logs.
- The local proxy requires a generated bearer token; settings show only a fingerprint.
- Official direct mode is switch-only and does not enter local proxy routing or Smart Routing.
- Destructive Codex config/auth resets require explicit confirmation and warn that chat history may be lost.

## Install

### Windows EXE

Download the latest build from [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases), then run:

```text
CodexHistoryManager.exe
```

### From Source

```bash
pip install -r requirements.txt
python main.py
```

The desktop app uses a local backend, usually:

```text
http://127.0.0.1:51234
```

If that port is occupied, the desktop launcher automatically moves to the next available port.

## Build A Release

```bash
python -m pytest -q
node --check static/js/app.js static/js/providers.js static/js/i18n.js static/js/monitor.js
python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest
```

Release assets:

```text
dist/CodexHistoryManager.exe
dist/release-manifest.json
```

Every GitHub Release should include both files. Source archives alone are not a usable Windows release.

## Local Files

| Path | Purpose |
| --- | --- |
| `config.json` | Main app settings. |
| `providers/providers.json` | Local provider registry. |
| `logs/proxy_requests.jsonl` | Metadata-only proxy request log. |
| `backups/` | App and Codex config backups. |
| `diagnostics/` | Redacted diagnostic bundles. |
| `exports/` | User-requested exports. |
| `temp/` | Temporary app files. |

## License

Apache License 2.0. See [LICENSE](LICENSE).
