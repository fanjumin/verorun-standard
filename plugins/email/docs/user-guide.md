# Email Service — User Guide

This guide is for **ops** configuring SMTP/IMAP and using the built-in
mail UI.

---

## 1. Prerequisites

- VeroRun app ≥ 0.10.0.
- SMTP account (Gmail app password / Outlook / QQ / enterprise).
- Optional: IMAP for inbox.

---

## 2. Configure

1. **Users & Support → Email Service → Settings**.
2. Pick a provider from `/providers` (auto-fills host/port).
3. Enter SMTP user / password / from.
4. IMAP host / port (optional).
5. **Test config** → should return success.

---

## 3. Use the mail UI

- **Inbox** — read incoming (IMAP).
- **Compose** — send; attachments allowed except blocked exts.
- **Drafts** — save / resume.
- **Sent** — history.
- **Contacts** — auto-collected from sent.

---

## 4. Stock alerts

- Set `alert_recipient` → stock alerts and morning briefs are emailed.
- Leave empty to skip email channel.

---

## 5. Safety guardrails

- Daily quota (default 200) prevents accidental spam.
- Blocked attachments stop executable delivery.
- Private SMTP targets are refused unless `allow_private_targets=true`.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Send fails | Wrong SMTP / auth | **Test config** |
| Inbox empty | IMAP not configured | Set IMAP host/port |
| Attachment rejected | Extension blocked | Rename / zip |
| Quota hit | Daily cap reached | Wait; raise `daily_send_quota` |
| Test config refuses localhost | SSRF guard | Only enable in trusted LAN |
