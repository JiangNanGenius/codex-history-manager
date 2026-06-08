# Codex Enhance Manager TODO

更新日期：2026-06-08

## 当前最高优先级

- [ ] 悬浮窗：继续实测桌面版打开、隐藏、置顶、深色背景和右键菜单，确认不再白底、不再点了没反应。本轮推进：桌面版默认显示悬浮窗，并在 WebView 启动完成后主动拉起一次；主界面左侧新增常驻“悬浮窗/启动 Codex”按钮；设置页新增“立即显示悬浮窗/启动 Codex”入口且默认勾选启动显示；悬浮窗右键菜单和标题栏菜单按钮支持启动 Codex、显示主窗口、打开设置、快速切换/自动选择供应商、隐藏和退出；托盘右键菜单同步支持显示主窗口、打开设置、显示/隐藏悬浮窗、启动 Codex、快速切换/自动选择供应商和退出。仍需打包后真实桌面点击验证。
- [ ] 退出清理：实测主窗口关闭、托盘退出、后台 Flask 线程、托盘线程和隐藏悬浮窗是否会留下进程。本轮推进：关闭询问改为“是=退出程序，否=缩小到托盘”，并新增默认行为设置。
- [ ] 设置向导：继续把设置页改成新手可一步步完成的向导，支持以后回来修改，不强迫重跑完整流程。本轮推进：快速设置已升级为 5 步向导，导入预设/新建供应商后自动进入连接信息步骤；加入专属样式和完整中英文文案。后续还要把供应商/模型能力编辑做成独立表单。
- [ ] 中文/英文双语：去掉主界面里过重的技术文案，中文界面补齐汉化，英文保持同等完整。本轮推进：移除首页“安全提示”卡片，淡化“重要改动确认”等提示型文案。
- [x] 自动审批：默认开启，保留“无感”体验；审批提示词允许用户自定义，并提供恢复默认。本轮已把后端和前端默认都改为自动处理低风险操作，并修正隐式默认不会在无 reviewer 的媒体测试路径上误拦截。

## 已调研依据

- Code++ 脚本市场：`https://github.com/BigPizzaV3/CodexPlusPlusScriptMarket`
- `hide-usage-alert`：隐藏 Codex 桌面的额度/用量提醒，目标是减少官方提醒对代理/自定义路由用户的干扰。
- `codex-token-usage`：从 Codex 响应、事件、SSE、WebSocket 和历史 bridge 中提取每轮 input/output/total/cache/context 用量。
- `codex-list-pagebuster`：补强 Codex 原生会话列表，为旧会话提供入口。
- Codex++ 本体：`https://github.com/BigPizzaV3/CodexPlusPlus`
- Codex++ 注入方式：外部启动 Codex，开启 Chromium DevTools Protocol，再通过 `Runtime.evaluate` 和 `Page.addScriptToEvaluateOnNewDocument` 注入，不改 Codex 安装文件。
- Codex++ 设置入口：注入脚本在 Codex 顶部/右上创建 `codex-plus-menu`，通过 bridge 调宿主后端。

## 设置向导设计

- [ ] 第 1 步：自动检测 Codex 数据库、sessions、归档目录、Codex++ 路径、代理端口。
- [ ] 第 2 步：添加供应商，支持一个供应商多个模型，并为缺失字段填默认值。
- [ ] 第 3 步：连接高级设置，可展开设置 User-Agent、自定义 headers、超时、重试、max tokens、模型名映射。
- [ ] 第 4 步：模型能力设置，按模型标记 text/vision/tools/images/videos/reasoning/streaming/context。
- [ ] 第 5 步：路由设置，支持动态调整顺序；后台切换模型或顺序后，在上一个会话结束后自动切换。
- [ ] 第 6 步：图片/视频模型设置，可选择“全局 fallback”，开启后所有模型获得该供应商提供的生成能力。
- [ ] 第 7 步：用量、额度、余额报警。用量以 Codex 输出事件为准；额度/余额只在有官方接口、预设脚本或用户导入脚本时开启。
- [ ] 第 8 步：保存、验证、以后修改入口。保存前给用户明确缺项提示，并允许先用默认值。

## 供应商与纯原生代理支持

- [x] 连接测试/预览必须和真实代理请求使用一致的 User-Agent 和自定义 headers。本轮新增“请求预览”dry-run：复用真实代理 `_build_upstream_headers`，显示 Content-Type、User-Agent、自定义 headers，并在返回前隐藏 Authorization/x-api-key 等敏感值；原有轻量网络检查仍保持低风险 HEAD。
- [ ] 纯原生 Responses 代理要检查图片/视频接口是否被同一 base URL 代理；若没有，界面应提示需要配置媒体供应商或开启全局 fallback。
- [ ] 一个供应商可提供多个不同能力模型，路由和目录不能只按供应商级能力判断。
- [x] 托盘/悬浮窗快速切换供应商：本轮新增持久化 `focus_provider_id`、`/api/providers/focus`、桌面 API 和本地代理焦点优先路由；媒体请求在焦点供应商具备能力时优先使用焦点，否则仍走全局媒体 fallback。
- [x] Responses 和 Chat 接口要能区分到模型级：本轮已增加模型级 `api_format`，目录预览显示最终接口来源，`/v1/responses` 路由会按模型级 Responses/Chat 选择原生或转换路径。
- [x] 模型是否原生支持审批要有设置项：本轮已增加模型级 `native_approval` 标记，并在模型能力 badge 中显示。
- [x] 模型名映射支持 exact、alias、regex，并在测试页显示最终上游模型名。本轮新增 provider 请求预览：显示 requested_model、upstream_model、api_format 和路由说明，覆盖 provider 前缀剥离、exact/case-insensitive alias 与 regex rewrite。
- [ ] 对不支持图片/视频的纯文本代理，不显示“全部不支持”，而是引导配置媒体 fallback。
- [x] 自动审批默认开启后不应破坏媒体代理：隐式默认无 reviewer 时允许媒体请求继续，用户显式开启的严格审批仍按失败策略处理。

## 用量、额度、余额报警

- [ ] 用量接口已能解析 Codex rollout `token_count` 的 input/output/total/cache/context；默认轮询只轻量扫描，完整“Codex 输出为准”还需要注入侧实时账本或后台索引，避免部分扫描冒充全量。
- [ ] 对齐 `codex-token-usage` 的更多来源：SSE 片段、WebSocket、bridge 历史恢复。先做本地 rollout 等价能力，再评估注入侧实时捕获。
- [ ] 额度报警：只有存在官方拉取、预设脚本或用户导入脚本时才允许开启。
- [ ] 余额报警：优先官方扣费；没有官方扣费时使用用户设置的余额和模型价格估算。
- [ ] 模型价格未设置时不计算费用，只显示 token 用量。
- [ ] 报警设置要区分 token、余额、额度、费用估算，并解释数据来源。

## Codex 右上角设置菜单

- [ ] 调研并复用 Codex++ 的安全注入思路：CDP 注入，不改 `app.asar`。
- [ ] 注入成功后在 Codex 右上角显示本应用入口，打开设置、路由、用量、诊断。
- [ ] 菜单必须有后端连接状态；断开时给出“重启注入/打开管理器”的简单操作。
- [ ] 选择器不能猜。需要用当前 Codex 版本实测 DOM，再写稳定 fallback。
- [ ] 注入脚本要可卸载、可重复注入、可检查版本，避免重复菜单。

## 更新与发布

- [x] 增加自动更新功能：本轮新增 GitHub Releases 检查、EXE 资产识别、设置页更新入口和本地下载到 `updates/<版本>/`；下载后提示关闭本程序并运行新版 EXE，不做静默覆盖。
- [ ] Release 必须附带打包好的 `.exe`。
- [ ] 打包后必须烟测试：`--smoke-test`、主界面、设置页、悬浮窗入口、退出清理。
- [ ] 软件内适当位置显示仓库直链：`https://github.com/JiangNanGenius/Codex-Enhance-Manager`

## 已知提醒安排

- [x] 已创建当前线程 2026-06-08 06:00 推进提醒。
- [ ] 8:00、10:00、12:00：Codex 当前线程只允许一个 heartbeat，未能直接创建；如需严格四次自动唤醒，需要用户确认改用其他自动化方式。
