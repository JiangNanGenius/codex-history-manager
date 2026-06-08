# Codex Enhance Manager

<p align="center">
  <strong>面向 Codex 的本地优先 Windows 控制中心：会话、供应商、路由、用量和恢复都放在一个地方。</strong>
</p>

<p align="center">
  <a href="README.md">English</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager">项目仓库</a>
  ·
  <a href="https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases">下载发布版</a>
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

## 这是什么

Codex Enhance Manager 是 Codex 的桌面辅助工具。它帮助你保留官方登录态、保持本地历史可见、切换供应商时不丢上下文，并且更直观地看到 Token 用量、连接状态和恢复入口。

它默认只在本机工作。供应商配置、备份、请求元数据、诊断、导出和临时文件都保存在你的电脑上。API Key、Bearer Token 和敏感 Header 会在界面、诊断和日志里默认隐藏。

## 现在能做什么

| 功能区 | 用户看到的结果 |
| --- | --- |
| 设置向导 | 一步步完成 Codex 路径、供应商、模型能力、路由、媒体 fallback、用量提醒、开机启动和保存检查。 |
| 供应商管理 | 添加一个或多个供应商；一个供应商可以配置多个模型；支持 Header、`User-Agent`、模型名映射和模型级能力标记。 |
| Codex 连接 | 支持官方登录、保留官方登录并接入本地代理/API、非官方供应商三类模式。官方直连会锁定会改变路由的配置。 |
| Responses 和 Chat | 在模型级区分原生 Responses、兼容 Responses 和 Chat 接口，不再把所有模型当成一种协议处理。 |
| 模型目录 | 决定哪些模型会显示给 Codex，同时把“供应商连接”和“新会话轮换策略”分开。 |
| 模型轮换 | 设置下一个新会话的模型顺序、优先级、能力筛选、故障转移和会话结束后的动态切换。 |
| 图片和视频 | 可以指定媒体供应商，也可以开启全局 fallback，让文本模型借用指定供应商的图片/视频生成能力。 |
| 自动审批 | 默认开启低风险无感处理；用户可以自定义审批提示词；模型回复必须是严格 JSON 决策。 |
| 用量和费用 | 读取 Codex Token、缓存用量、本地代理请求元数据、本地费用估算，以及可用时的供应商官方扣费信息。 |
| 悬浮窗 | 原生悬浮窗显示 Token，用托盘和右键菜单启动 Codex、打开主界面、切换供应商、调透明度和刷新数据。 |
| 恢复和更新 | 备份/恢复 Codex 配置和登录文件，修复移动后的会话，导出脱敏诊断，检查 GitHub Releases 并下载新版 EXE。 |

## 连接模式

| 模式 | 适合谁 | 行为 |
| --- | --- | --- |
| 官方登录直连 | 想完全保持 Codex 官方账号行为的用户。 | 保留官方 OAuth 登录态，并关闭会改变本地路由的供应商功能；只读显示增强仍可使用。 |
| 保留登录并接入代理/API | 想保留官方登录，同时把部分请求交给本地代理或配置好的 API 的用户。 | 默认保留登录态，只有用户选择该模式时才改变路由。 |
| 第三方供应商 | 使用自定义供应商或代理商运行 Codex 的用户。 | 开启供应商密钥、模型级 Responses/Chat、媒体 fallback、模型映射、额度脚本和模型轮换。 |

## 供应商和轮换的边界

供应商页只负责连接信息：密钥、地址、Header、`User-Agent`、模型名称、模型能力、媒体能力、额度脚本和目录可见性。

模型轮换页只负责下一个新会话如何选模型：顺序、优先级、能力匹配、故障转移，以及当前会话结束后的动态切换。

Codex 连接页只负责启动和写入：保留官方登录、接入代理/API、更新配置、备份和恢复入口。

## 快速开始

### Windows EXE

从 [Releases](https://github.com/JiangNanGenius/Codex-Enhance-Manager/releases) 下载最新 Windows 构建，然后运行 `CodexHistoryManager.exe`。

### 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

桌面窗口背后是本地 Flask 服务：

```text
http://127.0.0.1:51234
```

## 打包和发布

构建并验证 Windows EXE：

```bash
python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest
```

发布资产是：

```text
dist/CodexHistoryManager.exe
```

每个 GitHub Release 都必须上传打包好的 EXE 和 `dist/release-manifest.json`。只有源码压缩包不算完整的用户发布。发布描述需要同时包含中文和英文，模板见 `RELEASE_NOTES.md`。

## 本地存储

新用户数据默认保存到：

```text
Documents/Codex Enhance Manager/
```

| 路径 | 用途 |
| --- | --- |
| `config.json` | 应用主设置。 |
| `providers/providers.json` | 本地供应商注册表。 |
| `logs/proxy_requests.jsonl` | 只记录元数据的本地代理请求日志。 |
| `backups/`, `codex_backups/` | 应用和 Codex 配置备份。 |
| `diagnostics/` | 脱敏诊断包。 |
| `exports/` | 用户主动导出的文件。 |
| `temp/` | 临时文件。 |

## 开发检查

```bash
python -m pytest -q
node --check static/js/i18n.js static/js/providers.js static/js/amr.js static/js/sync.js
python -m py_compile approval_broker.py app.py main.py providers.py capabilities.py
```

## 参考资料

- [OpenAI Codex 源码](https://github.com/openai/codex)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat)
- [OpenAI Images API](https://platform.openai.com/docs/api-reference/images)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
