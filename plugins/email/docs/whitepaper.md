# Email Service — Technical Whitepaper

**Plugin:** `email` · **Version:** 1.8.1 · **Min app:** 0.10.0
**Category:** system · **Agent role:** `service`
**Python deps:** none (stdlib smtplib / imaplib)

---

## 1. Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Frontend mail UI (inbox / compose / contacts)             │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  routes.py  (15 endpoints, blueprint /email)                │
│   Inbox:    /inbox, /read/<uid>, /folders                   │
│   Compose:  /send, /batch, /attachment/<uid>/<file>         │
│   Drafts:   /drafts (GET/POST), /drafts/<id> (DELETE)       │
│   Contacts: /contacts                                       │
│   Sent:     /sent                                          │
│   Settings: /settings (GET/POST), /test-config              │
│   Meta:     /providers                                      │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  services.py        send + IMAP fetch + quota              │
│  crypto.py          SMTP password encryption               │
│  mail_providers.py   preset providers (Gmail/Outlook/QQ…)  │
│  email_mcp_server.py  MCP server (LLM can send mail)       │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  models.py  (2 tables)                                     │
└────────────────────────────────────────────────────────────┘
```

---

## 2. Data model (2 tables)

| Table | Purpose |
|---|---|
| `email_sent` | Send log (for daily quota counting) |
| `email_drafts` | Saved drafts |

---

## 3. HTTP API (15 endpoints)

Blueprint prefix: `/email`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/inbox` | IMAP inbox listing |
| GET | `/read/<uid>` | Read message |
| GET | `/folders` | IMAP folders |
| POST | `/send` | Send single email |
| POST | `/batch` | Batch send |
| GET | `/sent` | Sent log |
| GET | `/contacts` | Contacts (from sent) |
| GET/POST | `/settings` | SMTP/IMAP config |
| POST | `/test-config` | Test SMTP/IMAP connection |
| GET | `/attachment/<uid>/<filename>` | Download attachment |
| GET | `/providers` | Preset provider list |
| GET/POST | `/drafts` | List / save draft |
| DELETE | `/drafts/<id>` | Delete draft |

---

## 4. Hooks

- **provides**: `email/send`, `email/send_contact`, `email/get_config`.
- **listens**: `stock.alert.triggered`, `stock.morning_brief.ready`
  → send stock alerts via email.

---

## 5. Capabilities

- `email.send`, `email.template`.

---

## 6. Security model

- SMTP password encrypted at rest (`crypto.py`).
- **Blocked attachment extensions** (`.exe,.bat,.cmd,.scr,.js,.vbs,.ps1,.jar,.msi`).
- **Daily send quota** (default 200) counted from `email_sent`.
- **Recipient domain allowlist** optional.
- **`allow_private_targets=false`** by default — test-config refuses
  loopback / private SMTP/IMAP (SSRF protection).

---

## 7. Configuration (11 keys)

| Key | Default | Purpose |
|---|---|---|
| `smtp_host` / `smtp_port` / `smtp_user` / `smtp_pass` / `smtp_from` | — | SMTP |
| `imap_host` / `imap_port` | — | IMAP |
| `alert_recipient` | empty | Stock alert target (env `ALERT_RECIPIENT` overrides) |
| `blocked_attachment_exts` | (see §6) | Blocked exts |
| `allow_private_targets` | false | SSRF guard |
| `daily_send_quota` | 200 | Per-day cap (0 = unlimited) |
| `recipient_domain_allowlist` | empty | Allowed domains |

---

## 8. Dashboard

- `total_sent`, `total_contacts`.
