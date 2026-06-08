# Release Notes

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
