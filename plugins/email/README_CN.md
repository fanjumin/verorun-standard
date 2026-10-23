# 邮件服务（`email`）

> SMTP / IMAP 邮件客户端：发送、收件箱、写邮件、草稿、附件、联系人、
> 批量发送、安全策略（附件扩展名黑名单 / 收件域白名单 / 每日配额）。

- **标识符：** `email`
- **版本：** 1.8.1
- **类别：** system
- **菜单分组：** 用户与支持 → 邮件服务
- **最低 App 版本：** 0.10.0
- **兼容版本：** enterprise、standard、pro、finance、research、minipro
- **Agent 角色：** `service`
- **Python 依赖：** 无

---

## 核心功能

### 1. 发送引擎（`services.py`，43 KB）
- SMTP 发信（`smtp_host` / `smtp_port` / `smtp_user` / `smtp_pass` /
  `smtp_from`）。
- IMAP 收件（`imap_host` / `imap_port`）。
- `mail_providers.py`（8.8 KB）— 多 provider 适配（Gmail / Outlook /
  企业邮箱等）。

### 2. 安全策略
- **附件扩展名黑名单**：`.exe,.bat,.cmd,.scr,.js,.vbs,.ps1,.jar,.msi`
  （可配）。
- **收件域白名单**：`recipient_domain_allowlist`（空 = 全放行）。
- **每日配额**：`daily_send_quota=200`（0 = 不限）。
- **私网目标**：`allow_private_targets=false`（默认禁测 loopback /
  私网 SMTP）。
- `POST /test-config` 安全探测。

### 3. 15 个 HTTP 端点
- **收件 / 发件**：`GET /inbox`、`GET /read/<uid>`、`POST /send`、
  `GET /sent`、`GET /folders`。
- **草稿**：`GET/POST /drafts`、`DELETE /drafts/<id>`。
- **联系人**：`GET /contacts`。
- **附件**：`GET /attachment/<uid>/<filename>`。
- **设置**：`GET/POST /settings`、`POST /test-config`、
  `GET /providers`。
- **批量**：`POST /batch`。

### 4. MCP Server
- `email_mcp_server.py`（10.6 KB）— 给 AI agent 用的邮件 MCP 接口。

### 5. 股票告警联动
- 监听 `stock.alert.triggered`、`stock.morning_brief.ready`——股票
  插件触发告警 / 晨报时自动发邮件到 `alert_recipient`。

### 6. 钩子
- 提供：`email/send`、`email/send_contact`、`email/get_config`。

---

## 速览

| 维度 | 值 |
|---|---|
| 存储 | PG schema `email`，**2 张表** |
| 蓝图 | `/api/email`（`routes.py`，19.8 KB） |
| HTTP 端点 | **15 个** |
| 服务层 | `services.py`（43 KB） |
| MCP | `email_mcp_server.py`（10.6 KB） |
| 管理 UI | `templates/admin_email.html`（33 KB） |
| Python 依赖 | 无 |
| 权限 | `network:request`、`api:read`、`api:write`、`events` |
| Capabilities | `email.send`、`email.template` |
| 提供的钩子 | `email/send`、`email/send_contact`、`email/get_config` |
| 监听的钩子 | `stock.alert.triggered`、`stock.morning_brief.ready` |
| 仪表盘 | `total_sent`、`total_contacts` |

### 2 张表

| 表名 | 用途 |
|---|---|
| `email_sent` | 已发邮件记录（配额计数来源） |
| `email_drafts` | 草稿 |

### 关键配置

| 键 | 默认 | 作用 |
|---|---|---|
| `smtp_host` / `smtp_port` | 空 / 0 | SMTP 服务器 |
| `smtp_user` / `smtp_pass` | 空 | SMTP 账号密码（敏感） |
| `smtp_from` | 空 | 发件人地址 |
| `imap_host` / `imap_port` | 空 / 0 | IMAP 服务器 |
| `alert_recipient` | 空 | 股票告警收件人（空 = 跳过邮件通道） |
| `blocked_attachment_exts` | .exe,.bat,.cmd,.scr,.js,.vbs,.ps1,.jar,.msi | 附件黑名单 |
| `allow_private_targets` | false | 是否允许测私网 / loopback |
| `daily_send_quota` | 200 | 每日发信上限（0 = 不限） |
| `recipient_domain_allowlist` | 空 | 收件域白名单 |

---

## 五分钟快速上手

1. 启用插件——自动建 2 张表。
2. 打开 **用户与支持 → 邮件服务**。
3. 填 SMTP / IMTP 配置 → `POST /test-config` 测连通。
4. 设 `alert_recipient`（股票告警用）。
5. 前端 `POST /send` 发信 / `GET /inbox` 收件。

---

## 文档

| 文档 | 读者 | 位置 |
|---|---|---|
| 本文件 | 终端用户 / 集成方 | `README_CN.md` |
| 技术白皮书（开发者向） | 开发者 | [`docs/whitepaper_CN.md`](docs/whitepaper_CN.md) |
| 使用指南 / 操作手册 | 运营 | [`docs/user-guide_CN.md`](docs/user-guide_CN.md) |
| 变更日志 | 全部 | [`CHANGELOG.md`](CHANGELOG.md) |
