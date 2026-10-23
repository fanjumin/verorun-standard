# 邮件服务 — 技术白皮书

**插件：** `email` · **版本：** 1.8.1 · **最低 App：** 0.10.0
**类别：** system · **Agent 角色：** `service`
**Python 依赖：** 无（stdlib smtplib / imaplib）

---

## 1. 架构

```
┌────────────────────────────────────────────────────────────┐
│  前端邮件 UI（收件 / 写信 / 联系人）                       │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  routes.py  （15 个端点，蓝图 /email）                     │
│   收件：/inbox、/read/<uid>、/folders                       │
│   写信：/send、/batch、/attachment/<uid>/<file>              │
│   草稿：/drafts（GET/POST）、/drafts/<id>（DELETE）         │
│   联系人：/contacts                                         │
│   已发：/sent                                              │
│   设置：/settings（GET/POST）、/test-config                 │
│   元数据：/providers                                        │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  services.py        发送 + IMAP 拉取 + 配额                 │
│  crypto.py          SMTP 密码加密                          │
│  mail_providers.py  预设提供商（Gmail/Outlook/QQ…）        │
│  email_mcp_server.py  MCP server（LLM 可发邮件）             │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  models.py  （2 张表）                                     │
└────────────────────────────────────────────────────────────┘
```

---

## 2. 数据模型（2 张表）

| 表 | 用途 |
|---|---|
| `email_sent` | 发送日志（用于每日配额） |
| `email_drafts` | 保存的草稿 |

---

## 3. HTTP API（15 个端点）

蓝图前缀：`/email`。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/inbox` | IMAP 收件列表 |
| GET | `/read/<uid>` | 读信 |
| GET | `/folders` | IMAP 文件夹 |
| POST | `/send` | 单发 |
| POST | `/batch` | 批量发 |
| GET | `/sent` | 已发日志 |
| GET | `/contacts` | 联系人（从已发收集） |
| GET/POST | `/settings` | SMTP/IMAP 配置 |
| POST | `/test-config` | 测连接 |
| GET | `/attachment/<uid>/<filename>` | 下载附件 |
| GET | `/providers` | 预设提供商列表 |
| GET/POST | `/drafts` | 列 / 存草稿 |
| DELETE | `/drafts/<id>` | 删草稿 |

---

## 4. 钩子

- **提供**：`email/send`、`email/send_contact`、`email/get_config`。
- **监听**：`stock.alert.triggered`、`stock.morning_brief.ready`
  → 邮件发送股票告警。

---

## 5. Capabilities

- `email.send`、`email.template`。

---

## 6. 安全模型

- SMTP 密码静态加密（`crypto.py`）。
- **附件扩展名黑名单**（`.exe,.bat,.cmd,.scr,.js,.vbs,.ps1,.jar,.msi`）。
- **每日发送配额**（默认 200），按 `email_sent` 计数。
- 可选 **收件域名白名单**。
- **`allow_private_targets=false`** 默认——test-config 拒绝
  loopback / 内网 SMTP/IMAP（SSRF 防护）。

---

## 7. 配置（11 个键）

| 键 | 默认 | 作用 |
|---|---|---|
| `smtp_host` / `smtp_port` / `smtp_user` / `smtp_pass` / `smtp_from` | — | SMTP |
| `imap_host` / `imap_port` | — | IMAP |
| `alert_recipient` | 空 | 股票告警目标（env `ALERT_RECIPIENT` 覆盖） |
| `blocked_attachment_exts` | （见 §6） | 黑名单扩展名 |
| `allow_private_targets` | false | SSRF 防护 |
| `daily_send_quota` | 200 | 每日上限（0=不限） |
| `recipient_domain_allowlist` | 空 | 允许域名 |

---

## 8. 仪表盘

- `total_sent`、`total_contacts`。
