# Release Notes

## v2.2.12 - 2026-06-09

### 中文

- 重做供应商页的信息架构：模型上下文窗口、接口覆盖、是否显示给 Codex、模型级文本/视觉/工具/图片/视频能力全部放到供应商编辑器的“模型明细”区。
- 保留高级批量模型清单，方便粘贴和迁移；保存、预览和测试优先读取新的可视化模型明细表。
- “模型轮换”正式改名为“智能路由”，用户可见的导航、说明、官方模式提示和 README 已统一改名。
- Codex 集成页新增三张连接模式卡：官方直连、保留登录 + 本地代理、第三方/本地代理；切回官方的入口现在直接可见并可一键启动。
- 设置向导增加当前步骤卡和进度条，步骤状态、说明和完成度会随切换同步，整体更接近真正的设置向导。
- 本次 EXE 大小 `73.22 MB`，SHA256 `896b034d5a81807c16bdf7ba555eba846b7266435f8d694170c36ffebd9d22e3`。
- 已通过 `python -m pytest -q`、前端 JS 静态检查、`python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest`。

### English

- Reworked Provider information architecture: model context window, interface override, Codex visibility, and model-level text, vision, tools, image, and video capabilities now live in the Provider editor’s Model Details section.
- Kept the advanced bulk model list for paste/migration workflows; save, preview, and test flows now prefer the visual Model Details table.
- Renamed user-facing “Model Rotation” to “Smart Routing” across navigation, copy, official-mode warnings, and README.
- Added three obvious connection-mode cards on Codex Integration: Official Direct, Keep Login + Local Proxy, and Third-party / Local Proxy, so switching back to official is discoverable and launchable.
- Improved the Settings Wizard with a current-step card and progress bar that sync title, detail, status, and completion as the user moves through steps.
- This EXE is `73.22 MB` with SHA256 `896b034d5a81807c16bdf7ba555eba846b7266435f8d694170c36ffebd9d22e3`.
- Verified with `python -m pytest -q`, frontend JS static checks, and `python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest`.

## v2.2.11 - 2026-06-09

### 中文

- 修复官方登录态识别：当 `auth.json` 为 ChatGPT/OAuth 登录且 `config.toml` 只配置模型时，界面会锁定显示官方 `openai` 登录态和当前模型（例如 `gpt-5.5`），不再误判为普通供应商缺失。
- 官方登录态改为只做可切换的直连状态，不进入本地代理、AMR 或模型轮换；安全的 Codex 页面增强注入仍可启用。
- 本地代理默认使用高熵 bearer token，设置页只显示指纹；Codex provider 写入会使用真实 token，并且代理端口被占用时会自动退避到后续可用端口。
- 启动 Codex 改为带进度的后台任务，完整历史同步会显示阶段进度；同步默认不再每次做完整备份，并新增备份清理入口。
- 新增一键修复 Codex 配置到模板态、首次切回官方登录的风险重置流程、Goal mode 总设置、官方用量统计读取和悬浮窗 token 消耗速度。
- 本次 EXE 大小 `73.21 MB`，SHA256 `da20b3222acd814a2bb9e0524cb9fda5f30ee91220b0d4d77fba365d10a84d09`。
- 已通过 `python -m pytest -q`、前端 JS 静态检查、`python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest`。

### English

- Fixed official-login detection: when `auth.json` contains ChatGPT/OAuth auth and `config.toml` only sets a model, the UI now locks to the official `openai` login state and current model such as `gpt-5.5` instead of treating the provider as missing.
- Official login is now a switch-only direct state and is excluded from the local proxy, AMR, and model rotation; safe Codex page enhancement injection can still run.
- The local proxy now uses a high-entropy bearer token by default, settings only show its fingerprint, Codex provider config writes the real token, and occupied proxy ports automatically back off to the next available port.
- Codex launch now runs as a progress-reporting background task; full history sync shows progress, full backup is no longer the default on every sync, and backups can be pruned from the UI.
- Added one-click Codex config template repair, a risk-confirmed official-login reset flow, a global Goal mode setting, official usage reading, and token consumption speed in the floating monitor.
- This EXE is `73.21 MB` with SHA256 `da20b3222acd814a2bb9e0524cb9fda5f30ee91220b0d4d77fba365d10a84d09`.
- Verified with `python -m pytest -q`, frontend JS static checks, and `python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest`.

## v2.2.10 - 2026-06-08

### 中文

- 修复点击设置页/进入设置向导时弹出 CMD 窗口的问题。
- 设置页会读取 Windows 开机启动状态，后端需要调用 `schtasks.exe /Query`；现在该调用统一带 `CREATE_NO_WINDOW`，查询、创建和删除任务都不会闪出控制台窗口。
- 补充测试，确保启动管理器默认命令 runner 永远传入隐藏控制台参数。
- 优化启动体感：后端平台识别不再触发 Windows WMI，总览页和设置页先渲染首屏，再后台刷新供应商、清理、启动状态和更新检查。
- 后端初始化实测从 500ms 级别降到约 `27ms`；本次 EXE 大小 `73.15 MB`，SHA256 `e9d7cebb3dc18b3ac2b5f41829a4ee658065051792787343ec58f5b86e80d544`；已验证打包版启动后 `/api/startup/status` 正常返回。

### English

- Fixed a CMD window flashing when opening Settings or the Settings Wizard.
- Settings reads Windows startup status through `schtasks.exe /Query`; the startup command runner now always uses `CREATE_NO_WINDOW`, so query/create/delete task operations do not flash a console window.
- Added coverage to ensure the startup manager default runner always passes the hidden-console flag.
- Improved perceived startup speed: backend platform detection no longer touches Windows WMI, and Overview/Settings render their first screen before provider, cleanup, startup-status, and update checks finish in the background.
- Backend initialization dropped from the 500ms range to about `27ms`; this EXE is `73.15 MB` with SHA256 `e9d7cebb3dc18b3ac2b5f41829a4ee658065051792787343ec58f5b86e80d544`; packaged startup plus `/api/startup/status` was verified.

## v2.2.9 - 2026-06-08

### 中文

- 修复双击应用后没有窗口的问题：如果 `51234` 被旧测试服务或普通 Flask 服务占用，启动器不再误判为“桌面应用已启动”，会自动切到 `51235` 之后的可用端口。
- 健康检查新增 `desktop_mode` 和 `desktop_port`，单实例逻辑只把真正的桌面实例当作已启动。
- 如果真实桌面实例已经在运行，再次启动会尝试把已有主窗口恢复到前台。
- 已补充入口测试，覆盖非桌面端口占用、真实桌面健康标记和动态端口 URL 更新。
- 本次 EXE 大小 `73.15 MB`，SHA256 `468bff7b618f9fa7c9f6e622422d40bb4d8acc0fd5a0c19afb257773b9f89e5a`；已验证源码桌面和打包 EXE 都能在端口冲突时启动到 `51235`。

### English

- Fixed the no-window launch failure: if `51234` is occupied by an old test server or a plain Flask server, the launcher no longer treats it as an already-running desktop app and automatically moves to the next available port after `51235`.
- Added `desktop_mode` and `desktop_port` to the health endpoint so single-instance checks only trust real desktop instances.
- When a real desktop instance is already running, launching again now tries to restore the existing main window.
- Added entrypoint tests for non-desktop port conflicts, desktop health markers, and dynamic backend URL updates.
- This EXE is `73.15 MB` with SHA256 `468bff7b618f9fa7c9f6e622422d40bb4d8acc0fd5a0c19afb257773b9f89e5a`; both source desktop startup and packaged EXE startup were verified to move to `51235` during a port conflict.

## v2.2.8 - 2026-06-08

### 中文

- 重写 README 中英文说明，把项目定位、连接模式、供应商和模型轮换边界、打包发布规则改成更清楚的用户语言。
- 设置向导、连接检查、审批规则测试、图片/视频能力检查、历史用量来源等文案继续去技术化，减少无意义的旧式检查说明。
- 自动审批默认提示词要求严格 JSON，包含 `decision`、`risk_level`、`reason`、`confidence`、`scope` 和 `reviewed_action_id`。
- Codex 连接页会自动检查将保存的连接信息，保存前使用同一套 `User-Agent` 和自定义 Header。
- 供应商页只负责连接、模型能力和媒体能力；模型轮换页负责新会话顺序、优先级和故障转移。
- 增强纯原生 Responses/Chat 代理的模型级区分，保留原生模式和 Codex 登录态下的配置锁定逻辑。
- 发布包必须包含 `CodexHistoryManager.exe` 和 `release-manifest.json`；本次 EXE 大小 `73.14 MB`，SHA256 `2c549ecf3188d5bd5b88771583ccd1b8272d7468a5615a42cf3cdb1d80dd1edd`。
- 已通过 `python -m pytest -q`、JS/Python 静态检查、`python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest` 和独立 `CodexHistoryManager.exe --smoke-test`。

### English

- Rewrote the English and Chinese README files with clearer user-facing positioning, connection modes, provider/routing boundaries, and release rules.
- Continued replacing technical or low-value check copy with connection checks, approval rule tests, media capability checks, and usage-source summaries.
- The default Auto Approval prompt now requires strict JSON with `decision`, `risk_level`, `reason`, `confidence`, `scope`, and `reviewed_action_id`.
- The Codex connection page now checks the connection that will be saved and uses the same `User-Agent` plus custom headers as real proxy requests.
- Provider setup is limited to connection and model/media capability details; Model Rotation owns new-session order, priority, and failover.
- Improved model-level separation for native Responses, compatible Responses, and Chat providers while preserving official-login and native-mode locks.
- Releases must include `CodexHistoryManager.exe` and `release-manifest.json`; this EXE is `73.14 MB` with SHA256 `2c549ecf3188d5bd5b88771583ccd1b8272d7468a5615a42cf3cdb1d80dd1edd`.
- Verified with `python -m pytest -q`, JS/Python static checks, `python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest`, and a separate `CodexHistoryManager.exe --smoke-test` run.
