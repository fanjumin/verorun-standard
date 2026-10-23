# IM Gateway — Technical Whitepaper

**Plugin:** `im_gateway` · **Version:** 3.1.1 · **Min app:** 0.10.0
**Category:** system · **Agent role:** `service`
**Python deps:** none

---

## 1. Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Outbound: platform alerts / events                         │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  gateway.py        unified send()                          │
│  adapters/         8 channel adapters:                    │
│   feishu, wecom, telegram, line, slack, discord,           │
│   dingtalk, qq                                             │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  routes.py          /admin/im/<channel>  config (GET/PUT)  │
│  routes_overview.py  dashboard                              │
│  routes_webhook.py    inbound webhook /webhook/<channel>    │
│  webhook_signing.py   signature verify (fail-closed)        │
│  routes_login.py      OAuth login flow                      │
│  routes_third_login.py  third-party web login              │
│  login/providers.py   provider catalog                     │
│  login/exchange.py    code → platform user                  │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  models.py  (5 tables)                                     │
└────────────────────────────────────────────────────────────┘
```

---

## 2. Data model (5 tables)

| Table | Purpose |
|---|---|
| `channel_configs` | Per-channel credentials / webhook URLs |
| `im_channel_rate_events` | Per-channel rate limit events |
| `login_providers` | OAuth provider config |
| `login_user_bindings` | Platform user ↔ external account |
| `oauth_login_states` | CSRF state for OAuth |

---

## 3. HTTP API

### 3.1 Channel config (admin, `/admin/im`)
| Method | Path | Purpose |
|---|---|---|
| GET | `` | Overview |
| GET | `/<channel>` | Channel config |
| PUT | `/<channel>` | Update config |
| POST | `/<channel>/test` | Send test message |

### 3.2 Inbound webhook (`/webhook`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/<channel>` | Signed inbound (fail-closed) |

### 3.3 OAuth / third-party login
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/login/...` | OAuth flow |
| GET/POST | `/third-login/...` | Web third-party login |

---

## 4. Security

- Inbound webhook **signature verified, fail-closed** (bad signature
  → 401, no body processing).
- Outbound credentials stored encrypted in `channel_configs`.
- OAuth state table prevents CSRF.

---

## 5. Hooks

- **provides**: `im_gateway/send_message`, `im_gateway/push_media`.
- **listens**: `stock.alert.triggered` → fan-out to enabled channels.

---

## 6. Capabilities

- `im.channel.list`, `im.message.send`, `im.message.receive`,
  `im.account.bind`.

---

## 7. Channels (8)

Feishu · WeCom · Telegram · LINE · Slack · Discord · DingTalk · QQ.

---

## 8. Dashboard

- `total_channels`, `enabled_channels`.
