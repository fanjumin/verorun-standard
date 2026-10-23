# 短信服务 — 技术白皮书

**插件：** `sms` · **版本：** 1.5.4 · **最低 App：** 0.10.0
**类别：** system · **Agent 角色：** `service`
**Python 依赖：** 无

---

## 1. 架构

```
┌────────────────────────────────────────────────────────────┐
│  调用方（auth / verification / order_notify）              │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  services.py     统一发送 / 生成码 / 校验                    │
│   provider：阿里云、Twilio                                 │
│  countries.py     国家 / 区号目录                           │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  routes.py  （9 个端点，/admin/sms）                       │
└─────────────┬──────────────────────────────────────────────┘
              │
┌─────────────▼──────────────────────────────────────────────┐
│  models.py  （3 张表）                                     │
└────────────────────────────────────────────────────────────┘
```

---

## 2. HTTP API（9 个端点，`/admin/sms`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET/POST | `/templates` | 列 / 建 |
| PUT/DELETE | `/templates/<id>` | 改 / 删 |
| GET | `/logs` | 发送日志 |
| POST | `/test-send` | 发测试短信 |
| GET/POST | `/settings` | 取 / 改 provider 配置 |
| GET | `/countries` | 国家列表 |

---

## 3. 数据模型（3 张表）

| 表 | 用途 |
|---|---|
| `sms_templates` | 短信模板（带变量） |
| `sms_logs` | 发送历史 |
| `sms_rate_limits` | 按手机号 / IP 限流 |

---

## 4. 钩子

- **提供**：`sms/send`、`sms/generate_code`、
  `sms/validate_phone`、`sms/check_rate_limit`。

---

## 5. Capabilities

- `sms.send`、`sms.code.generate`、`sms.verify`。

---

## 6. 配置（3 个键，阿里云）

| 键 | 默认 | 作用 |
|---|---|---|
| `aliyun_sms_sign_name` | "" | 签名 |
| `aliyun_sms_access_key` | "" | AccessKey ID |
| `aliyun_sms_secret` | "" | AccessKey secret（只写） |

Twilio 配置通过 `/settings` 存。

---

## 7. 仪表盘

- `today_sent`、`total_templates`。
