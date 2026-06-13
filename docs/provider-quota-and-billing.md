# Provider Quota And Billing Notes

This document records the quota/balance methods we verified from the local
CC Switch source tree and how Codex Enhance Manager maps them.

## Source References

- Balance probes: `research/cc-switch/src-tauri/src/services/balance.rs`
- Coding Plan quota probes: `research/cc-switch/src-tauri/src/services/coding_plan.rs`
- Official Codex OAuth quota: `research/cc-switch/src-tauri/src/services/subscription.rs`
- CC Switch quota footer UI: `research/cc-switch/src/components/SubscriptionQuotaFooter.tsx`

## Official Codex Login

Official login quota is not a provider `quota_check`. It uses the Codex OAuth
access token already present in `~/.codex/auth.json` and queries:

- `GET https://chatgpt.com/backend-api/wham/usage`
- Headers:
  - `Authorization: Bearer <Codex OAuth access_token>`
  - `User-Agent: codex-cli`
  - `ChatGPT-Account-Id: <account_id>` when available

The response `rate_limit.primary_window` and `rate_limit.secondary_window` are
converted to quota tiers. `limit_window_seconds=18000` maps to `five_hour`, and
`604800` maps to `seven_day`.

Implementation note: our `official_quota.py` is read-only. It never refreshes
tokens and never writes Codex files.

## Balance Providers

These are account balance or credits endpoints. They can show money/credits and
burn rate in the floating monitor.

| Provider | Endpoint | Auth | Unit | Mapping |
| --- | --- | --- | --- | --- |
| DeepSeek | `GET https://api.deepseek.com/user/balance` | Bearer | response currency | `balance_infos[0].total_balance` |
| StepFun | `GET https://api.stepfun.com/v1/accounts` | Bearer | CNY | `balance` |
| SiliconFlow CN | `GET https://api.siliconflow.cn/v1/user/info` | Bearer | CNY | `data.totalBalance` |
| SiliconFlow EN | `GET https://api.siliconflow.com/v1/user/info` | Bearer | USD | `data.totalBalance` |
| OpenRouter | `GET https://openrouter.ai/api/v1/credits` | Bearer | USD | `total_credits - total_usage` |
| Novita AI | `GET https://api.novita.ai/v3/user/balance` | Bearer | USD | `availableBalance / 10000` |

OpenRouter also exposes a useful utilization percentage:
`total_usage / total_credits * 100`, so the preset uses a script instead of
plain JSON paths.

## Coding Plan Quota Providers

These are subscription-style quota percentages. They are not token prices and
should be displayed as remaining/used percentage tiers.

| Provider | Endpoint | Auth | Tiers |
| --- | --- | --- | --- |
| KimiCode | `GET https://api.kimi.com/coding/v1/usages` | Bearer | `five_hour`, `weekly_limit` |
| Zhipu Coding Plan | `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit` or `https://api.z.ai/api/monitor/usage/quota/limit` | raw API key in `Authorization` | sorted `TOKENS_LIMIT` entries: `five_hour`, `weekly_limit` |
| MiniMax CN | `GET https://api.minimaxi.com/v1/api/openplatform/coding_plan/remains` | Bearer | `general` model, five-hour plus weekly when `current_weekly_status == 1` |
| MiniMax EN | `GET https://api.minimax.io/v1/api/openplatform/coding_plan/remains` | Bearer | same as CN |
| ZenMux | configured `baseUrl` | Bearer | `quota_5_hour`, `quota_7_day` |

Volcengine Ark, Volcengine Coding Plan, and Volcengine Agent Plan are kept as
separate provider families. The model API plan endpoints do not expose a public
token balance through the same model key, so public presets stay in
`token_plan_monthly` cost mode and do not pretend to be metered.

## UI And Alert Policy

- Provider page owns pricing, tiered pricing, balance probes, and scripts.
- Model page can override model-level prices, but Smart Routing should not
duplicate model capability fields already owned by providers.
- Floating monitor shows:
  - balance and burn rate when a balance exists;
  - quota tiers when `values.tiers[]` or `quota_percent` exists;
  - official Codex quota only when the current mode is official-direct usage.
- Quota utilization colors:
  - `<70%`: normal
  - `70%-89%`: warning
  - `>=90%`: danger and one notification per provider/tier bucket

Private proxy/vendor-specific methods belong only in the local app config and
must not be committed as public presets.
