#!/usr/bin/env python3
"""
Mail Provider Registry — 邮件服务商预置清单（单一数据源）
=========================================================
预置国内外常用邮件服务商的 SMTP/IMAP 服务器参数，实现"用户只填写
邮箱账号（+密码）即可完成邮件服务器配置"的快捷设置。

设计约定：
- 本文件是服务商数据的唯一来源；routes.py 的 GET /providers 接口与
  services.py 的域名推导均从此处读取，避免重复定义造成漂移。
- 域名推导只填充「空配置字段」，绝不覆盖用户在设置页显式填写的值。
- Proton Mail 无公网 SMTP/IMAP 服务，必须通过官方 Proton Bridge 提供
  的本机端口收发邮件，故其服务器地址指向 127.0.0.1 并附说明。
- name / note 为展示文案，以英文源串存放；模块级常量不可求值翻译，
  由 localized_providers() 在使用处按当前语言翻译。
"""

from i18n import _

# each item: id, name, domains, smtp_host, smtp_port, smtp_ssl,
#            imap_host, imap_port, imap_ssl, note (optional)
MAIL_PROVIDERS = [
    # ── 国内邮箱 ──────────────────────────────────────────────
    {
        "id": "aliyun",
        "name": "Alibaba Mail",
        "domains": ["aliyun.com", "aliyunmail.com"],
        "smtp_host": "smtp.aliyun.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.aliyun.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "aliyun-qiye",
        "name": "Alibaba Cloud Enterprise Mail",
        "domains": ["qiye.aliyun.com"],
        "smtp_host": "smtp.qiye.aliyun.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.qiye.aliyun.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "tencent-ex",
        "name": "Tencent Exmail",
        "domains": ["exmail.qq.com"],
        "smtp_host": "smtp.exmail.qq.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.exmail.qq.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "qq",
        "name": "QQ Mail",
        "domains": ["qq.com"],
        "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.qq.com", "imap_port": 993, "imap_ssl": True,
        "note": "Enable SMTP/IMAP in QQ Mail web settings and use the authorization code",
    },
    {
        "id": "foxmail",
        "name": "Foxmail",
        "domains": ["foxmail.com"],
        "smtp_host": "smtp.foxmail.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.foxmail.com", "imap_port": 993, "imap_ssl": True,
        "note": "Use the Foxmail authorization code instead of your login password",
    },
    {
        "id": "netease-163",
        "name": "NetEase 163 Mail",
        "domains": ["163.com"],
        "smtp_host": "smtp.163.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.163.com", "imap_port": 993, "imap_ssl": True,
        "note": "Enable SMTP/IMAP and use the authorization code",
    },
    {
        "id": "netease-126",
        "name": "NetEase 126 Mail",
        "domains": ["126.com"],
        "smtp_host": "smtp.126.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.126.com", "imap_port": 993, "imap_ssl": True,
        "note": "Enable SMTP/IMAP and use the authorization code",
    },
    {
        "id": "netease-188",
        "name": "NetEase 188 Mail",
        "domains": ["188.com"],
        "smtp_host": "smtp.188.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.188.com", "imap_port": 993, "imap_ssl": True,
        "note": "Enable SMTP/IMAP and use the authorization code",
    },
    {
        "id": "sina",
        "name": "Sina Mail",
        "domains": ["sina.com", "sina.cn"],
        "smtp_host": "smtp.sina.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.sina.com", "imap_port": 993, "imap_ssl": True,
        "note": "Enable SMTP/IMAP; a separate password is required for client login",
    },
    {
        "id": "sohu",
        "name": "Sohu Mail",
        "domains": ["sohu.com"],
        "smtp_host": "smtp.sohu.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.sohu.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "cmcc-139",
        "name": "China Mobile 139 Mail",
        "domains": ["139.com"],
        "smtp_host": "smtp.139.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.139.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "net263",
        "name": "263 Enterprise Mail",
        "domains": ["263.net", "263xmail.com"],
        "smtp_host": "smtp.263.net", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.263.net", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "cn-21cn",
        "name": "21CN Mail",
        "domains": ["21cn.com"],
        "smtp_host": "smtp.21cn.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.21cn.com", "imap_port": 993, "imap_ssl": True,
    },
    # ── 国外邮箱 ──────────────────────────────────────────────
    {
        "id": "gmail",
        "name": "Gmail",
        "domains": ["gmail.com", "googlemail.com"],
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_ssl": False,
        "imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True,
        "note": "Password sign-in was disabled in May 2025; use OAuth or a Google app password",
    },
    {
        "id": "outlook",
        "name": "Outlook / Hotmail",
        "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        "smtp_host": "smtp-mail.outlook.com", "smtp_port": 587, "smtp_ssl": False,
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True,
        "note": "Requires an app password after enabling two-step verification; IMAP basic auth is deprecated since October 2022",
    },
    {
        "id": "yahoo",
        "name": "Yahoo Mail",
        "domains": ["yahoo.com"],
        "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_ssl": True,
        "note": "Requires two-step verification and an app password",
    },
    {
        "id": "icloud",
        "name": "iCloud Mail",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_ssl": False,
        "imap_host": "imap.mail.me.com", "imap_port": 993, "imap_ssl": True,
        "note": "Requires an Apple ID app-specific password",
    },
    {
        "id": "aol",
        "name": "AOL Mail",
        "domains": ["aol.com"],
        "smtp_host": "smtp.aol.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.aol.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "zoho",
        "name": "Zoho Mail",
        "domains": ["zoho.com", "zohomail.com"],
        "smtp_host": "smtp.zoho.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.zoho.com", "imap_port": 993, "imap_ssl": True,
    },
    {
        "id": "yandex",
        "name": "Yandex Mail",
        "domains": ["yandex.com", "yandex.ru"],
        "smtp_host": "smtp.yandex.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.yandex.com", "imap_port": 993, "imap_ssl": True,
        "note": "Requires generating an app password",
    },
    {
        "id": "fastmail",
        "name": "Fastmail",
        "domains": ["fastmail.com", "fastmail.fm"],
        "smtp_host": "smtp.fastmail.com", "smtp_port": 465, "smtp_ssl": True,
        "imap_host": "imap.fastmail.com", "imap_port": 993, "imap_ssl": True,
        "note": "Requires an app password",
    },
    {
        "id": "proton",
        "name": "Proton Mail (Bridge Required)",
        "domains": ["proton.me", "protonmail.com", "protonmail.ch"],
        "smtp_host": "127.0.0.1", "smtp_port": 1025, "smtp_ssl": False,
        "imap_host": "127.0.0.1", "imap_port": 1143, "imap_ssl": False,
        "note": "Proton has no public SMTP/IMAP; run the official Proton Bridge and connect through local ports 1025/1143",
    },
]


def localized_providers(locale=None):
    """返回展示用服务商清单：name / note 按当前语言翻译，其余字段原样。"""
    out = []
    for p in MAIL_PROVIDERS:
        item = dict(p)
        item['name'] = _(p['name'], locale=locale)
        if p.get('note'):
            item['note'] = _(p['note'], locale=locale)
        out.append(item)
    return out


def get_provider_by_domain(email_or_domain):
    """按域名匹配预置服务商。

    支持两种输入：完整邮箱地址（自动提取 @ 后域名）或裸域名。
    匹配规则：先精确匹配（如 qq.com），未命中再做最长后缀匹配
    （如 user@mail.qq.com → qq.com）。返回 provider dict 或 None。
    """
    if not email_or_domain:
        return None
    domain = str(email_or_domain).strip().lower()
    if '@' in domain:
        domain = domain.rsplit('@', 1)[1].strip()
    domain = domain.lstrip('.').strip()
    if not domain:
        return None

    # 1) 精确匹配
    for p in MAIL_PROVIDERS:
        if domain in p['domains']:
            return p

    # 2) 最长后缀匹配
    best, best_len = None, 0
    for p in MAIL_PROVIDERS:
        for d in p['domains']:
            if domain.endswith('.' + d) and len(d) > best_len:
                best, best_len = p, len(d)
    return best
