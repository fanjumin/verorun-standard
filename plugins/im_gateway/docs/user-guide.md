# IM Gateway — User Guide

This guide is for **ops** configuring notification channels and
third-party login.

---

## 1. Prerequisites

- VeroRun app ≥ 0.10.0.
- Bot tokens / webhook URLs for the channels you want.

---

## 2. Enable

1. **System → Channels**.
2. Pick a channel (e.g. Feishu).
3. Paste bot token / webhook URL / signing secret.
4. **Test** → should receive a message.
5. Toggle **enabled**.

---

## 3. Inbound webhook

- Public URL: `https://<your-domain>/webhook/<channel>`.
- Signature verified; bad signature rejected.

---

## 4. Third-party login

1. **Login settings** → add provider (Feishu / WeCom / Google / …).
2. Set client id / secret.
3. Users can bind external accounts from their profile.

---

## 5. Stock alerts fan-out

- When `stock.alert.triggered` fires, enabled channels get the
  alert automatically.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Test message not received | Wrong token | Check channel config |
| Webhook 401 | Bad signature | Verify signing secret |
| Channel offline | Rate limited | Check `im_channel_rate_events` |
| OAuth callback fails | State mismatch | Restart login flow |
