# IM 网关 — 使用指南

本指南面向 **运营**，指导你配置通知渠道与第三方登录。

---

## 1. 前置条件

- VeroRun App ≥ 0.10.0。
- 目标渠道的 bot token / webhook URL。

---

## 2. 启用

1. **系统 → 通讯频道**。
2. 选渠道（如飞书）。
3. 填 bot token / webhook URL / 签名密钥。
4. **测试** → 应收到消息。
5. 打开 **启用**。

---

## 3. 入站 webhook

- 公共 URL：`https://<your-domain>/webhook/<channel>`。
- 验签；签名错被拒。

---

## 4. 第三方登录

1. **登录设置** → 加提供商（飞书 / 企微 / Google / …）。
2. 填 client id / secret。
3. 用户可在个人资料绑定外部账号。

---

## 5. 股票告警扇出

- `stock.alert.triggered` 触发时，已启用渠道自动收告警。

---

## 6. 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 测试消息没收到 | token 错 | 查渠道配置 |
| webhook 401 | 签名错 | 核对签名密钥 |
| 渠道离线 | 被限流 | 查 `im_channel_rate_events` |
| OAuth 回调失败 | state 不匹配 | 重启登录流程 |
