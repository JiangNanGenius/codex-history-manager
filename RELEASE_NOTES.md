# Release Notes

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
