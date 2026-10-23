# 短信服务（`sms`）

> 手机验证码 / 通知短信网关。阿里云（国内）与 Twilio（国际）双渠道、
> 模板管理、发送日志、限流、国家区号。

- **标识符：** `sms`
- **版本：** 1.5.4
- **类别：** system
- **菜单分组：** 安全与合规 → SMS Management
- **最低 App 版本：** 0.10.0
- **兼容版本：** enterprise、standard、pro、finance、research、minipro
- **Agent 角色：** `service`
- **Python 依赖：** 无

---

## 核心功能

### 1. 模板管理
- `sms_templates` 表。
- `GET/POST /templates`、`PUT/DELETE /templates/<id>`。

### 2. 发送（`services.py`，6.8 KB）
- 阿里云 / Twilio 双渠道。
- `POST /test-send` — 测试发送。
- `sms/send` 钩子。

### 3. 验证码
- `sms.code.generate` capability。
- `sms.verify` capability。
- `sms/generate_code`、`sms/validate_phone` 钩子。

### 4. 日志
- `sms_logs` 表。
- `GET /logs`。

### 5. 限流
- `sms_rate_limits` 表。
- `sms/check_rate_limit` 钩子。

### 6. 国家区号
- `countries.py`（7.6 KB）。
- `GET /countries`。

### 7. 设置
- `GET/POST /settings`。
- `aliyun_sms_sign_name` / `aliyun_sms_access_key` / `aliyun_sms_secret`。

---

## 速览

| 维度 | 值 |
|---|---|
| 存储 | PG schema `sms`，**3 张表** |
| 蓝图 | `/admin/sms`（`routes.py`，8.7 KB） |
| HTTP 端点 | **9 个** |
| 服务 | `services.py`（6.8 KB） |
| 国家区号 | `countries.py`（7.6 KB） |
| 管理 UI | `templates/admin_sms.html`（9.7 KB） |
| Python 依赖 | 无 |
| 权限 | api:read/write、network:request、admin:access |
| Capabilities | sms.send、sms.code.generate、sms.verify |
| 提供的钩子 | sms/send、sms/generate_code、sms/validate_phone、sms/check_rate_limit |
| 仪表盘 | today_sent / total_templates |

### 3 张表

| 表名 | 用途 |
|---|---|
| `sms_templates` | 短信模板 |
| `sms_logs` | 发送日志 |
| `sms_rate_limits` | 限流 |

### 配置（3 项，敏感）

| 键 | 默认 | 作用 |
|---|---|---|
| `aliyun_sms_sign_name` | 空 | 阿里云短信签名 |
| `aliyun_sms_access_key` | 空 | 阿里云 AccessKey ID |
| `aliyun_sms_secret` | 空 | 阿里云 AccessKey Secret（writeOnly） |

---

## 五分钟快速上手

1. 启用插件——自动建 3 张表。
2. 打开 **安全与合规 → SMS Management**。
3. 填阿里云签名 / AccessKey。
4. 加模板。
5. 用 `/test-send` 测发。

---

## 文档

| 文档 | 读者 | 位置 |
|---|---|---|
| 本文件 | 运维 / 后端 | `README_CN.md` |
| 技术白皮书（开发者向） | 开发者 | [`docs/whitepaper_CN.md`](docs/whitepaper_CN.md) |
| 使用指南 / 操作手册 | 运维 | [`docs/user-guide_CN.md`](docs/user-guide_CN.md) |
| 变更日志 | 全部 | [`CHANGELOG.md`](CHANGELOG.md) |
