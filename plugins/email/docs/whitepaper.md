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

---

## 9. Workflow DAG node (`send_email`)

Email sending is a first-class workflow step. `EmailPlugin.register_dag_nodes()`
returns `{"send_email": handle_send_email}` from `dag_nodes.py` and hands it to
`BasePlugin.register_dag_nodes()`, which the platform injects into
`WorkflowEngine`; the kernel does not know the mail business exists. The
handler signature follows the engine contract `handler(node_def, input_data)`,
with `self.manager` partial-bound as `pm` (WP-E2) because workflow nodes run in
a bare thread without a Flask app context.

| Aspect | Behavior |
|---|---|
| Node type | `send_email` (also listed by `GET /admin/workflows/node-types`) |
| Required `config` | `to`, `subject`, and at least one of `body` / `body_html` |
| Optional `config` | `cc`, `bcc`, `reply_to`, `attachments` (`[{"filename", "data" (base64), "content_type"}]`) |
| Recipients | List, or a string split on `,` `;` `，` `；` |
| Templating | `{{ key }}` / `{{ a.b }}` resolved from `input_data.context`, recursively for str / list / dict |
| Idempotency key | `sha1(instance_id \| node_id \| sorted lowercase recipients)` |
| Duplicate run | Returns `{"success": true, "skipped": true, "reason": "duplicate"}` without sending |
| Failure | Writes `email_sent(status='failed')`, then raises so the engine marks the node failed |
| Config source | `services.send_email(..., pm=manager)` → `pm.get_config('email')` |
| Governance | `send_email` is in `DANGEROUS_NODE_TYPES`; authoring needs `workflow.script.author` and an upstream `approval` node |

The dedup key is only stored on successful `email_sent` rows (partial unique
index), so a failed execution can be retried; a successful one is skipped by
the pre-check and never sends a second time.
