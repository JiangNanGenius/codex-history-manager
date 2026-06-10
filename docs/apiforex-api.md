# apiforex.cn 汇率 API 文档

## 概述

apiforex.cn 提供实时和历史汇率数据，支持 30+ 主流货币。

## 基础信息

| 项目 | 值 |
|------|-----|
| 基础 URL | `https://apiforex.cn/api/v1` |
| 数据格式 | JSON |
| 编码 | UTF-8 |

## 认证

支持两种方式传递 API Key：

### 方式一：请求头（推荐）

```http
X-API-Key: your_api_key_here
```

### 方式二：查询参数

```http
GET /api/v1/latest?apikey=your_api_key_here
```

## 支持的货币

USD, EUR, JPY, GBP, CNY, CNH, AUD, CAD, CHF, HKD, SGD, SEK, NOK, NZD, MXN, INR, KRW, RUB, ZAR, TRY, BRL, PLN, HUF, CZK, DKK, ILS, THB, TWD, PHP, MYR, IDR

## 统一响应格式

### 成功响应

```json
{
  "success": true,
  "data": {
    // 具体数据内容
  }
}
```

### 错误响应

```json
{
  "success": false,
  "error": "错误描述信息"
}
```

## API 端点

### 获取最新汇率

```
GET /api/v1/latest
```

**请求头：**

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| `X-API-Key` | string | 是 | API 密钥 |
| `X-User-Type` | string | 否 | `paid` 表示付费用户 |

**查询参数：**

| 参数 | 类型 | 必需 | 默认值 | 描述 |
|------|------|------|--------|------|
| `base_currency` | string | 否 | USD | 基础货币代码 |
| `currencies` | string | 否 | 所有 | 逗号分隔的目标货币列表 |

**响应示例：**

```json
{
  "success": true,
  "data": {
    "base": "USD",
    "rates": {
      "CNY": 6.7769,
      "EUR": 0.8656,
      "GBP": 0.7473
    }
  }
}
```

### 获取历史汇率

```
GET /api/v1/historical
```

**查询参数：**

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| `date_start` | string | 是 | 开始日期 (YYYY-MM-DD) |
| `date_end` | string | 是 | 结束日期 (YYYY-MM-DD) |
| `base_currency` | string | 否 | 基础货币代码 |
| `currencies` | string | 否 | 目标货币列表 |

## 速率限制

| 用户类型 | 每日请求数 | 每分钟请求数 | 实时数据延迟 |
|----------|-----------|-------------|-------------|
| 免费版 | 10,000 | 10 | 30分钟 |
| 专业版 | 无限制 | 50 | 30秒内 |

## 项目中集成方式

文件：`currency.py`

函数：`_fetch_apiforex_rate(api_key, from_currency, to_currency)`

- 使用 `urllib.request` 发送 GET 请求到 `/api/v1/latest`
- 通过 `X-API-Key` 请求头传递密钥
- 解析响应中的 `data.rates.{to_currency}` 获取汇率
- 异常处理覆盖 HTTPError、网络超时、JSON 解析错误

## 配置项

在 `config.json` 中设置：

```json
{
  "exchange_rate_source": "apiforex",
  "exchange_rate_api_key": "ek_xxxxxxxx"
}
```
