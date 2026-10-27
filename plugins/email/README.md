# Email Service (`email`)

> SMTP / IMAP email client: send, inbox, compose, drafts, attachments,
> contacts, batch send, security policy (attachment extension blocklist /
> recipient domain allowlist / daily quota).

- **Identifier:** `email`
- **Version:** 1.8.1
- **Category:** system
- **Menu group:** Users & Support → Email Service
- **Min app version:** 0.10.0
- **Compatible editions:** enterprise, standard, pro, finance, research, minipro
- **Agent role:** `service`
- **Python deps:** none

---

## Core Features

### 1. Send engine (`services.py`, 43 KB)
- SMTP send (`smtp_host` / `smtp_port` / `smtp_user` / `smtp_pass` /
  `smtp_from`).
- IMAP inbox (`imap_host` / `imap_port`).
- `mail_providers.py` (8.8 KB) — multi-provider adapter (Gmail / Outlook /
  enterprise).

### 2. Security policy
- **Attachment extension blocklist**:
  `.exe,.bat,.cmd,.scr,.js,.vbs,.ps1,.jar,.msi` (configurable).
- **Recipient domain allowlist**: `recipient_domain_allowlist` (empty =
  allow all).
- **Daily quota**: `daily_send_quota=200` (0 = unlimited).
- **Private targets**: `allow_private_targets=false` (blocks loopback /
  private SMTP probing).
- `POST /test-config` safe probe.

### 3. 15 HTTP endpoints
- **Inbox / send**: `GET /inbox`, `GET /read/<uid>`, `POST /send`,
  `GET /sent`, `GET /folders`.
- **Drafts**: `GET/POST /drafts`, `DELETE /drafts/<id>`.
- **Contacts**: `GET /contacts`.
- **Attachments**: `GET /attachment/<uid>/<filename>`.
- **Settings**: `GET/POST /settings`, `POST /test-config`,
  `GET /providers`.
- **Batch**: `POST /batch`.

### 4. MCP server
- `email_mcp_server.py` (10.6 KB) — email MCP interface for AI agents.

### 5. Stock alert integration
- Listens to `stock.alert.triggered`, `stock.morning_brief.ready` —
  auto-sends to `alert_recipient` on stock alerts / morning briefs.

### 6. Hooks
- Provided: `email/send`, `email/send_contact`, `email/get_config`.

### 7. Workflow DAG node (`send_email`)
- Registered by `EmailPlugin.register_dag_nodes()` through
  `BasePlugin.register_dag_nodes()`; the engine only sees a node type, the
  mail business stays inside this plugin.
- Node type id: **`send_email`** (`dag_nodes.py`), also returned by
  `GET /admin/workflows/node-types`. Handler signature is
  `handler(node_def, input_data)`; `self.manager` is bound as `pm` so SMTP
  settings are read without a Flask app context.
- Required `config`: `to`, `subject`, and at least one of `body` /
  `body_html`. Optional: `cc`, `bcc`, `reply_to`, `attachments`
  (`[{"filename", "data" (base64), "content_type"}]`). Recipients accept a
  list or a comma/semicolon separated string.
- Placeholders `{{ key }}` / `{{ a.b }}` in any config value are rendered
  from `input_data.context` (recursive for str / list / dict).
- Idempotency: `dedup_key = sha1(instance_id | node_id | sorted recipients)`;
  if a `status='sent'` record already exists the node returns
  `{"success": true, "skipped": true, "reason": "duplicate"}` and does not
  send again. Failed records carry no key so they can be retried.
- On failure the node writes `email_sent(status='failed')` and then raises,
  because the engine only marks `failed` on exceptions.
- Governance: `send_email` is a dangerous node type — authoring requires the
  `workflow.script.author` permission and an `approval` node upstream.

---

## At a glance

| Area | Value |
|---|---|
| Storage | PG schema `email`, **2 tables** |
| Blueprint | `/api/email` (`routes.py`, 19.8 KB) |
| HTTP endpoints | **15** |
| Service layer | `services.py` (43 KB) |
| MCP | `email_mcp_server.py` (10.6 KB) |
| Admin UI | `templates/admin_email.html` (33 KB) |
| Python deps | none |
| Permissions | `network:request`, `api:read`, `api:write`, `events` |
| Capabilities | `email.send`, `email.template` |
| Hooks provided | `email/send`, `email/send_contact`, `email/get_config` |
| Hooks listened | `stock.alert.triggered`, `stock.morning_brief.ready` |
| Workflow node | `send_email` (config: `to`, `subject`, `body`/`body_html`) |
| Dashboard | `total_sent`, `total_contacts` |

### 2 tables

| Table | Purpose |
|---|---|
| `email_sent` | Sent log (quota counter source) |
| `email_drafts` | Drafts |

### Key config

| Key | Default | Purpose |
|---|---|---|
| `smtp_host` / `smtp_port` | empty / 0 | SMTP server |
| `smtp_user` / `smtp_pass` | empty | SMTP credentials (sensitive) |
| `smtp_from` | empty | From address |
| `imap_host` / `imap_port` | empty / 0 | IMAP server |
| `alert_recipient` | empty | Stock alert recipient (empty = skip email channel) |
| `blocked_attachment_exts` | .exe,.bat,.cmd,.scr,.js,.vbs,.ps1,.jar,.msi | Attachment blocklist |
| `allow_private_targets` | false | Allow probing private / loopback |
| `daily_send_quota` | 200 | Daily send cap (0 = unlimited) |
| `recipient_domain_allowlist` | empty | Recipient domain allowlist |

---

## Five-minute quick start

1. Enable — auto-creates 2 tables.
2. Open **Users & Support → Email Service**.
3. Fill SMTP / IMAP → `POST /test-config`.
4. Set `alert_recipient` (stock alerts).
5. Frontend `POST /send` / `GET /inbox`.

---

## Documents

| Document | Audience | Location |
|---|---|---|
| This file | End user / integrator | `README.md` |
| Technical whitepaper | Developers | [`docs/whitepaper.md`](docs/whitepaper.md) |
| User guide / operation manual | Ops | [`docs/user-guide.md`](docs/user-guide.md) |
| Changelog | All | [`CHANGELOG.md`](CHANGELOG.md) |
