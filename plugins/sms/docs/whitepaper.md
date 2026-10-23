# SMS Service — Technical Whitepaper

**Plugin:** `sms` · **Version:** 1.5.4 · **Min app:** 0.10.0
**Category:** system · **Agent role:** `service`
**Python deps:** none

---

## 1. Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Callers (auth / verification / order_notify)             │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  services.py     unified send / code gen / verify          │
│   providers: Aliyun, Twilio                                │
│  countries.py     country / dialing-code catalog          │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  routes.py  (9 endpoints, /admin/sms)                       │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  models.py  (3 tables)                                     │
└────────────────────────────────────────────────────────────┘
```

---

## 2. HTTP API (9 endpoints, `/admin/sms`)

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/templates` | List / create |
| PUT/DELETE | `/templates/<id>` | Update / delete |
| GET | `/logs` | Send log |
| POST | `/test-send` | Send a test SMS |
| GET/POST | `/settings` | Get / update provider config |
| GET | `/countries` | Country list |

---

## 3. Data model (3 tables)

| Table | Purpose |
|---|---|
| `sms_templates` | SMS templates (with variables) |
| `sms_logs` | Send history |
| `sms_rate_limits` | Per-phone / per-IP limits |

---

## 4. Hooks

- **provides**: `sms/send`, `sms/generate_code`,
  `sms/validate_phone`, `sms/check_rate_limit`.

---

## 5. Capabilities

- `sms.send`, `sms.code.generate`, `sms.verify`.

---

## 6. Configuration (3 keys, Aliyun)

| Key | Default | Purpose |
|---|---|---|
| `aliyun_sms_sign_name` | "" | Signature |
| `aliyun_sms_access_key` | "" | AccessKey ID |
| `aliyun_sms_secret` | "" | AccessKey secret (write-only) |

Twilio config is stored via `/settings`.

---

## 7. Dashboard

- `today_sent`, `total_templates`.
