# 通讯频道（`im_gateway`）

> 统一即时消息通道网关。飞书 / 企业微信 / Telegram / LINE / Slack /
> Discord / 钉钉 / QQ 出站消息，入站 webhook 验签（fail-closed），
> 含 Web 第三方登录。

- **标识符：** `im_gateway`
- **版本：** 3.1.1
- **类别：** system
- **菜单分组：** 系统 → 通讯频道
- **最低 App 版本：** 0.10.0
- **兼容版本：** enterprise、standard、pro、finance、minipro
- **Agent 角色：** `service`
- **Python 依赖：** 无

---

## 核心功能

### 1. 8 通道出站适配器
- `adapters/feishu.py`（8.6 KB）— 飞书。
- 其他适配器：wecom（企业微信）、telegram、line、slack、discord、
  dingtalk（钉钉）、qq。
- `http_client.py`（8 KB）— 统一 HTTP 调用封装。

### 2. 入站 webhook 验签（fail-closed）
- 验签失败直接拒绝，不进入消息处理。
- 跨 worker 频控：`im_channel_rate_events` 表（已改名避免与平台共享
  频控表 `public.rate_limit_events` 冲突）。

### 3. Web 第三方登录
- `routes_third_login.py`（9.7 KB）— 登录路由。
- `login/exchange.py`（10 KB）— token 交换。

### 4. 股票告警联动
- 监听 `stock.alert.triggered`——股票告警自动推到启用的 IM 通道。

### 5. 钩子
- 提供：`im_gateway/send_message`、`im_gateway/push_media`。

### 6. 预置频道种子
- 默认启用：飞书、企业微信。
- 默认禁用：QQ、钉钉。

---

## 速览

| 维度 | 值 |
|---|---|
| 存储 | PG schema `im_gateway`，**2 张表** |
| 蓝图 | `/admin/channels`（管理）+ `/webhook/<channel>`（入站）+ `/auth/third`（第三方登录） |
| 适配器 | 8 个（飞书 / 企业微信 / Telegram / LINE / Slack / Discord / 钉钉 / QQ） |
| 管理 UI | `templates/admin_imgateway.html`（27.3 KB） |
| 登录 | `login/exchange.py`（10 KB）+ `routes_third_login.py`（9.7 KB） |
| Python 依赖 | 无 |
| 权限 | api:read、api:write、network:request |
| Capabilities | im.channel.list、im.message.send、im.message.receive、im.account.bind |
| 提供的钩子 | im_gateway/send_message、im_gateway/push_media |
| 监听的钩子 | stock.alert.triggered |
| 仪表盘 | total_channels、enabled_channels |

### 2 张表

| 表名 | 用途 |
|---|---|
| `channel_configs` | 每通道配置（channel / config_json / is_enabled） |
| `im_channel_rate_events` | 跨 worker 频控计数 |

### 配置
无 `settings_schema`——每通道配置存 `channel_configs.config_json`。

---

## 五分钟快速上手

1. 启用插件——自动建 2 张表，播种飞书 / 企业微信。
2. 打开 **系统 → 通讯频道**。
3. 在每通道配置 App ID / Secret / Webhook。
4. 启用要推的通道。
5. 其他插件通过 `im_gateway/send_message` 钩子发消息。

---

## 文档

| 文档 | 读者 | 位置 |
|---|---|---|
| 本文件 | 运维 / 集成方 | `README_CN.md` |
| 技术白皮书（开发者向） | 开发者 | [`docs/whitepaper_CN.md`](docs/whitepaper_CN.md) |
| 使用指南 / 操作手册 | 运维 | [`docs/user-guide_CN.md`](docs/user-guide_CN.md) |
| 变更日志 | 全部 | [`CHANGELOG.md`](CHANGELOG.md) |
