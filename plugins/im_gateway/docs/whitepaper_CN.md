# IM 网关 — 技术白皮书

**插件：** `im_gateway` · **版本：** 3.1.1 · **最低 App：** 0.10.0
**类别：** system · **Agent 角色：** `service`
**Python 依赖：** 无

---

## 1. 架构

```
┌────────────────────────────────────────────────────────────┐
│  出站：平台告警 / 事件                                      │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  gateway.py        统一 send()                              │
│  adapters/         8 个渠道适配器：                        │
│   feishu、wecom、telegram、line、slack、discord、           │
│   dingtalk、qq                                             │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  routes.py          /admin/im/<channel> 配置（GET/PUT）   │
│  routes_overview.py  仪表盘                                 │
│  routes_webhook.py   入站 webhook /webhook/<channel>        │
│  webhook_signing.py  验签（fail-closed）                    │
│  routes_login.py      OAuth 登录流程                         │
│  routes_third_login.py 第三方 Web 登录                       │
│  login/providers.py   提供商目录                            │
│  login/exchange.py    code → 平台用户                        │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  models.py  （5 张表）                                     │
└────────────────────────────────────────────────────────────┘
```

---

## 2. 数据模型（5 张表）

| 表 | 用途 |
|---|---|
| `channel_configs` | 每渠道凭证 / webhook URL |
| `im_channel_rate_events` | 每渠道限流事件 |
| `login_providers` | OAuth 提供商配置 |
| `login_user_bindings` | 平台用户 ↔ 外部账号绑定 |
| `oauth_login_states` | OAuth CSRF state |

---

## 3. HTTP API

### 3.1 渠道配置（管理，`/admin/im`）
| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `` | 总览 |
| GET | `/<channel>` | 渠道配置 |
| PUT | `/<channel>` | 改配置 |
| POST | `/<channel>/test` | 发测试消息 |

### 3.2 入站 webhook（`/webhook`）
| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/<channel>` | 验签入站（fail-closed） |

### 3.3 OAuth / 第三方登录
| 方法 | 路径 | 作用 |
|---|---|---|
| GET/POST | `/login/...` | OAuth 流程 |
| GET/POST | `/third-login/...` | Web 第三方登录 |

---

## 4. 安全

- 入站 webhook **验签，fail-closed**（签名错 → 401，不处理 body）。
- 出站凭证加密存在 `channel_configs`。
- OAuth state 表防 CSRF。

---

## 5. 钩子

- **提供**：`im_gateway/send_message`、`im_gateway/push_media`。
- **监听**：`stock.alert.triggered` → 扇出到已启用渠道。

---

## 6. Capabilities

- `im.channel.list`、`im.message.send`、`im.message.receive`、
  `im.account.bind`。

---

## 7. 渠道（8 个）

飞书 · 企业微信 · Telegram · LINE · Slack · Discord · 钉钉 · QQ。

---

## 8. 仪表盘

- `total_channels`、`enabled_channels`。
