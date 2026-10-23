# IM Gateway (`im_gateway`)

> Unified instant-messaging channel gateway. Outbound messaging for
> Feishu / WeCom / Telegram / LINE / Slack / Discord / DingTalk / QQ,
> signed inbound webhooks (fail-closed), plus web third-party login.

- **Identifier:** `im_gateway`
- **Version:** 3.1.1
- **Category:** system
- **Menu group:** System → Channels
- **Min app version:** 0.10.0
- **Compatible editions:** enterprise, standard, pro, finance, minipro
- **Agent role:** `service`
- **Python deps:** none

---

## Core Features

### 1. 8 outbound adapters
- `adapters/feishu.py` (8.6 KB) — Feishu.
- Others: wecom, telegram, line, slack, discord, dingtalk, qq.
- `http_client.py` (8 KB) — unified HTTP wrapper.

### 2. Inbound webhook verification (fail-closed)
- Invalid signature rejected outright.
- Cross-worker rate limit: `im_channel_rate_events` table (renamed to
  avoid collision with platform `public.rate_limit_events`).

### 3. Web third-party login
- `routes_third_login.py` (9.7 KB).
- `login/exchange.py` (10 KB) — token exchange.

### 4. Stock alert integration
- Listens to `stock.alert.triggered` — auto-pushes to enabled IM channels.

### 5. Hooks
- Provided: `im_gateway/send_message`, `im_gateway/push_media`.

### 6. Seeded channels
- Enabled by default: Feishu, WeCom.
- Disabled by default: QQ, DingTalk.

---

## At a glance

| Area | Value |
|---|---|
| Storage | PG schema `im_gateway`, **2 tables** |
| Blueprints | `/admin/channels` + `/webhook/<channel>` + `/auth/third` |
| Adapters | 8 (Feishu / WeCom / Telegram / LINE / Slack / Discord / DingTalk / QQ) |
| Admin UI | `templates/admin_imgateway.html` (27.3 KB) |
| Login | `login/exchange.py` (10 KB) + `routes_third_login.py` (9.7 KB) |
| Python deps | none |
| Permissions | api:read, api:write, network:request |
| Capabilities | im.channel.list, im.message.send, im.message.receive, im.account.bind |
| Hooks provided | im_gateway/send_message, im_gateway/push_media |
| Hooks listened | stock.alert.triggered |
| Dashboard | total_channels, enabled_channels |

### 2 tables

| Table | Purpose |
|---|---|
| `channel_configs` | Per-channel config (channel / config_json / is_enabled) |
| `im_channel_rate_events` | Cross-worker rate-limit counter |

### Config
No `settings_schema` — per-channel config lives in `channel_configs.config_json`.

---

## Five-minute quick start

1. Enable — auto-creates 2 tables, seeds Feishu / WeCom.
2. Open **System → Channels**.
3. Fill App ID / Secret / webhook per channel.
4. Enable channels to push.
5. Other plugins send via `im_gateway/send_message` hook.

---

## Documents

| Document | Audience | Location |
|---|---|---|
| This file | Ops / integrator | `README.md` |
| Technical whitepaper | Developers | [`docs/whitepaper.md`](docs/whitepaper.md) |
| User guide / operation manual | Ops | [`docs/user-guide.md`](docs/user-guide.md) |
| Changelog | All | [`CHANGELOG.md`](CHANGELOG.md) |
