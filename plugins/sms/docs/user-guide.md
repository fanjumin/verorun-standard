# SMS Service — User Guide

This guide is for **ops** configuring SMS verification.

---

## 1. Prerequisites

- Aliyun SMS account or Twilio account.

---

## 2. Enable

1. **Security → SMS Management**.
2. **Settings** → fill Aliyun (or Twilio) credentials.

---

## 3. Templates

- **Templates → New** → name, content with `${code}` variable.
- Submit for provider approval.

---

## 4. Test

- **Test-send** with a phone number → verify delivery.

---

## 5. Logs

- **Logs** → track sends / failures.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Test send fails | Wrong secret | Re-enter |
| Code not received | Template not approved | Wait for approval |
| Too many sends | Rate limit | Check `sms_rate_limits` |
| International fail | Country not supported | Check `/countries` |
