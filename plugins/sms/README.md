# SMS Service (`sms`)

> Phone verification-code / notification SMS gateway. Aliyun (CN) and
> Twilio (INTL) dual-provider, templates, send logs, rate limits, country
> dialing codes.

- **Identifier:** `sms`
- **Version:** 1.5.4
- **Category:** system
- **Menu group:** Security & Compliance → SMS Management
- **Min app version:** 0.10.0
- **Compatible editions:** enterprise, standard, pro, finance, research, minipro
- **Agent role:** `service`
- **Python deps:** none

---

## Core Features

### 1. Template management
- `sms_templates` table.
- `GET/POST /templates`, `PUT/DELETE /templates/<id>`.

### 2. Sending (`services.py`, 6.8 KB)
- Aliyun / Twilio dual-provider.
- `POST /test-send`.
- `sms/send` hook.

### 3. Verification codes
- `sms.code.generate` capability.
- `sms.verify` capability.
- `sms/generate_code`, `sms/validate_phone` hooks.

### 4. Logs
- `sms_logs` table.
- `GET /logs`.

### 5. Rate limiting
- `sms_rate_limits` table.
- `sms/check_rate_limit` hook.

### 6. Country codes
- `countries.py` (7.6 KB).
- `GET /countries`.

### 7. Settings
- `GET/POST /settings`.
- `aliyun_sms_sign_name` / `aliyun_sms_access_key` / `aliyun_sms_secret`.

---

## At a glance

| Area | Value |
|---|---|
| Storage | PG schema `sms`, **3 tables** |
| Blueprint | `/admin/sms` (`routes.py`, 8.7 KB) |
| HTTP endpoints | **9** |
| Service | `services.py` (6.8 KB) |
| Countries | `countries.py` (7.6 KB) |
| Admin UI | `templates/admin_sms.html` (9.7 KB) |
| Python deps | none |
| Permissions | api:read/write, network:request, admin:access |
| Capabilities | sms.send, sms.code.generate, sms.verify |
| Hooks provided | sms/send, sms/generate_code, sms/validate_phone, sms/check_rate_limit |
| Dashboard | today_sent / total_templates |

### 3 tables

| Table | Purpose |
|---|---|
| `sms_templates` | SMS templates |
| `sms_logs` | Send logs |
| `sms_rate_limits` | Rate limits |

### Config (3 items, sensitive)

| Key | Default | Purpose |
|---|---|---|
| `aliyun_sms_sign_name` | empty | Aliyun SMS signature |
| `aliyun_sms_access_key` | empty | Aliyun AccessKey ID |
| `aliyun_sms_secret` | empty | Aliyun AccessKey Secret (writeOnly) |

---

## Five-minute quick start

1. Enable — auto-creates 3 tables.
2. Open **Security & Compliance → SMS Management**.
3. Fill Aliyun signature / AccessKey.
4. Add template.
5. Test via `/test-send`.

---

## Documents

| Document | Audience | Location |
|---|---|---|
| This file | Ops / backend | `README.md` |
| Technical whitepaper | Developers | [`docs/whitepaper.md`](docs/whitepaper.md) |
| User guide / operation manual | Ops | [`docs/user-guide.md`](docs/user-guide.md) |
| Changelog | All | [`CHANGELOG.md`](CHANGELOG.md) |
