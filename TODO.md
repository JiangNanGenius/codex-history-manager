# Codex Enhance Manager TODO

更新日期：2026-06-08

## 本轮发布目标

- [x] 版本号升至 `v2.2.8`，避免复用已发布的 `v2.2.7`。
- [x] README 中英文重写为面向用户的说明，去掉旧的技术化检查说明和“手动修改限制”表达。
- [x] 新增中英文 `RELEASE_NOTES.md`，作为 GitHub Release 描述来源。
- [x] 跑完整测试、重新打包 `dist/CodexHistoryManager.exe`，并生成 `dist/release-manifest.json`。
- [x] 对打包后的 EXE 运行 `--smoke-test`。真实桌面冒烟仍需继续覆盖：主界面、设置向导、悬浮窗、托盘菜单、启动/关闭 Codex、退出清理。
- [ ] 提交、推送，并创建带 EXE 资产的 GitHub Release。

## 最高优先级

- [ ] 悬浮窗真实桌面验证：确认默认开启、可见、圆角、半透明、数据刷新、右键菜单、托盘菜单、快速切换供应商、打开主窗口、启动 Codex 和退出都能工作。
- [ ] 退出清理：确认主窗口关闭、托盘退出、悬浮窗隐藏、后台 Flask 线程、托盘线程、Codex 子进程和 PyInstaller 父进程看门狗不会留下残留。
- [ ] 设置向导继续产品化：供应商、多模型、模型能力、媒体 fallback、路由、额度脚本、余额提醒、默认值补齐和后续修改入口要真正按步骤走。
- [ ] 总览页增强：显示当前连接模式、Codex 登录状态、当前供应商/模型、代理状态、今日/本轮 Token、费用估算、余额/额度数据来源和仓库直链。
- [ ] 供应商页减负：只保留连接、密钥、Header、User-Agent、模型能力、媒体能力和额度脚本；新会话顺序、优先级和故障转移继续放到模型轮换页。
- [ ] 纯原生 Responses 代理支持：默认标记完整文本能力；原生模式配置不可随意修改；图片/视频若未被同一 base URL 代理，需要引导配置媒体供应商或全局 fallback。
- [ ] 官方登录态：检测到 Codex 登录后默认采用“保留登录注入模式”；官方直连模式下锁定会改变供应商/路由的配置，只保留只读显示增强。
- [ ] 开机启动：普通启动不承诺提权；需要最高权限时走 Windows 任务计划。界面要讲清“普通启动”和“管理员启动”的差别，并在打包版实测添加/移除。
- [ ] 自动审批：默认无感开启；用户可编辑提示词；默认提示词必须要求严格 JSON；支持模型是否原生审批的模型级设置。
- [ ] 用量、额度、余额报警：用量以 Codex 输出/事件为准；额度和余额只有在官方接口、预设脚本或用户导入脚本可用时才开启；没有模型价格就只计 Token 不计费。
- [ ] Codex 右上角设置入口：研究并实现 CDP 注入，不修改安装文件；注入成功后显示本应用菜单，支持打开设置、路由、用量和诊断。

## 已完成的关键推进

- [x] 自动更新：新增 GitHub Releases 检查、EXE 资产识别、下载到 `updates/<版本>/`，下载后提示用户关闭旧版并运行新版。
- [x] 自动审批默认开启：后端和前端默认都偏向低风险无感处理；无 reviewer 的媒体路径不会被隐式默认阻断。
- [x] 自动审批提示词：默认要求返回严格 JSON，包含 `decision`、`risk_level`、`reason`、`confidence`、`scope`、`reviewed_action_id`。
- [x] Responses 和 Chat 分流：新增模型级 `api_format`，`/v1/responses` 会按模型选择原生 Responses、Responses 兼容或 Chat 转换路径。
- [x] 原生审批标记：新增模型级 `native_approval`，并在模型能力 badge 中展示。
- [x] 模型名映射：支持 exact、alias、regex，并能显示最终上游模型名。
- [x] 连接测试 Header 一致性：测试 API 会使用和真实代理一致的 `User-Agent` 与自定义 Header，并默认隐藏敏感值。
- [x] 媒体 fallback 引导：文本代理不再显示“全部不支持”，而是提示图片/视频需要媒体供应商或全局 fallback。
- [x] 托盘/悬浮窗快速切换供应商：新增持久化 `focus_provider_id`、`/api/providers/focus`、桌面 API 和代理焦点优先路由。
- [x] 供应商与模型轮换职责边界：供应商页负责连接和能力；模型轮换页负责新会话策略；Codex 连接页负责启动、登录保留和配置写入。
- [x] README 正文不包含外部对照项目名，也不再保留“真实修改必须用户手动执行”的限制。

## 调研记录

- 参考脚本市场：`https://github.com/BigPizzaV3/CodexPlusPlusScriptMarket`
- `hide-usage-alert`：可隐藏 Codex 桌面里的官方用量提醒，适合做成“显示增强/请求路由增强”分离开关。
- `codex-token-usage`：会从 Codex 响应、事件、SSE、WebSocket 和历史 bridge 中提取 input/output/total/cache/context 用量；本项目先对齐本地 rollout 和代理日志，再评估注入侧实时账本。
- 参考桌面增强项目：`https://github.com/BigPizzaV3/CodexPlusPlus`
- 参考注入方法：外部启动 Codex，开启 Chromium DevTools Protocol，再通过 `Runtime.evaluate` 和 `Page.addScriptToEvaluateOnNewDocument` 注入，不修改 Codex 安装文件。
- 参考设置入口：注入脚本在 Codex 顶部/右上创建菜单，通过 bridge 调用宿主后端。

## 设置向导设计

- [ ] 第 1 步：自动检测 Codex 数据库、sessions、归档目录、Codex 可执行文件、代理端口和当前登录态。
- [ ] 第 2 步：添加供应商，支持一个供应商多个模型，并给缺失字段补默认值。
- [ ] 第 3 步：连接高级设置，可展开设置 `User-Agent`、自定义 Header、超时、重试、max tokens 和模型名映射。
- [ ] 第 4 步：模型能力设置，按模型标记 text、vision、tools、images、videos、reasoning、streaming、context 和 native approval。
- [ ] 第 5 步：路由设置，支持后台动态调整顺序；当前会话结束后自动切到下一组模型。
- [ ] 第 6 步：图片/视频设置，支持全局 fallback；开启后其他模型可借用该供应商的生成能力。
- [ ] 第 7 步：用量、额度、余额报警，明确数据来源；无官方接口或脚本时不开放额度/余额自动报警。
- [ ] 第 8 步：保存和以后修改入口；保存前明确缺项，允许先用默认值跑起来。

## 发布检查清单

- [x] `python -m pytest -q`
- [x] `node --check static/js/i18n.js static/js/providers.js static/js/amr.js static/js/sync.js`
- [x] `python -m py_compile approval_broker.py app.py main.py providers.py capabilities.py`
- [x] `python build_exe.py --no-desktop-copy --smoke-test --write-release-manifest`
- [ ] 打开打包版 EXE，检查主界面、设置页、总览、悬浮窗、托盘菜单、启动 Codex 和退出清理。
- [ ] GitHub Release 上传 `dist/CodexHistoryManager.exe` 和 `dist/release-manifest.json`。
- [ ] Release 描述必须中英文双语，并写清测试结果和已知风险。

## 已知提醒安排

- [x] 已创建当前线程 2026-06-08 06:00 推进提醒。
- [ ] 8:00、10:00、12:00：当前线程只允许一个 heartbeat，未能直接创建；如需严格四次自动推进，需要改用其他自动化方式。
