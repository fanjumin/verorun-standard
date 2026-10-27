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

---

## 9. 工作流 DAG 节点（`send_email`）

发信是一等工作流步骤。`EmailPlugin.register_dag_nodes()` 返回
`dag_nodes.py` 中的 `{"send_email": handle_send_email}`，经
`BasePlugin.register_dag_nodes()` 由平台注入 `WorkflowEngine`，内核不必知道
邮件业务的存在。处理器签名遵循引擎契约 `handler(node_def, input_data)`；
由于工作流节点运行在无 Flask app context 的裸线程中，注册时把
`self.manager` 以 `pm` 偏绑定传入（WP-E2）。

| 维度 | 行为 |
|---|---|
| 节点类型 | `send_email`（`GET /admin/workflows/node-types` 同时可见） |
| 必填 `config` | `to`、`subject`，以及 `body` / `body_html` 至少一项 |
| 可选 `config` | `cc`、`bcc`、`reply_to`、`attachments`（`[{"filename", "data"(base64), "content_type"}]`） |
| 收件人 | 列表，或以 `,` `;` `，` `；` 分隔的字符串 |
| 模板变量 | `{{ key }}` / `{{ a.b }}` 从 `input_data.context` 取值，对 str / list / dict 递归渲染 |
| 幂等键 | `sha1(instance_id \| node_id \| 收件人小写排序)` |
| 重复执行 | 返回 `{"success": true, "skipped": true, "reason": "duplicate"}`，不再外发 |
| 失败 | 先写 `email_sent(status='failed')` 再抛异常，引擎才会把节点标记为 failed |
| 配置来源 | `services.send_email(..., pm=manager)` → `pm.get_config('email')` |
| 治理 | `send_email` 属于 `DANGEROUS_NODE_TYPES`，编写需 `workflow.script.author` 权限且上游必须有 `approval` 节点 |

幂等键只落在成功的 `email_sent` 记录（部分唯一索引）上：失败记录不带键，
可安全重跑；成功的执行由 pre-check 直接跳过，不会二次外发。
