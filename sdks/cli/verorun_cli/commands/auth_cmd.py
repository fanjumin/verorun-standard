"""认证相关命令：login / logout / whoami。

安全改动：新增 `--password-stdin`，并在使用 `--password` 时明确告警——
命令行参数会出现在进程列表（ps / 任务管理器）与 shell 历史中。
"""

from __future__ import annotations

import sys
from typing import Any

import click

from ..constants import _
from ..output import warn
from ._base import wants_json, show


def register(group: Any) -> None:
    @group.command("login")
    @click.option("--username", "-u", help=_("Admin username"))
    @click.option("--password", "-p",
                  help=_("Password (insecure: it is exposed in the process list and shell history; prefer --password-stdin)"))
    @click.option("--password-stdin", is_flag=True,
                  help=_("Read the password from stdin (recommended for CI / scripts)"))
    @click.option("--phone", help=_("Phone number (SMS login)"))
    @click.option("--code", help=_("SMS verification code"))
    @click.option("--api-key", help=_("agent API Key (ek-...)"))
    @click.option("--captcha", "captcha_id",
                  help=_("CAPTCHA token (used when repeated login failures are rate-limited by the server)"))
    @click.pass_context
    def login(ctx, username, password, password_stdin, phone, code, api_key,
              captcha_id):
        """登录 VeroRun（管理员密码 / 短信 / API Key）

        密码来源优先级：--password-stdin > --password > 交互式隐藏输入。
        """
        auth = ctx.obj["auth"]

        if api_key:
            auth.login_apikey(api_key)
            click.echo(_("API Key saved."))
            return

        if phone or code:
            if not (phone and code):
                raise click.UsageError(_("SMS login requires both --phone and --code"))
            auth.login_sms(phone, code)
            click.echo(_("SMS login succeeded."))
            return

        if password and password_stdin:
            raise click.UsageError(_("--password and --password-stdin are mutually exclusive; use only one"))

        if not username:
            username = click.prompt(_("Username"))

        if password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
            if not password:
                raise click.UsageError(_("--password-stdin could not read a password from stdin"))
        elif password:
            warn(_(
                "--password exposes the plaintext password in the process list and shell "
                "history. Prefer `--password-stdin` (for example `echo $PWD_VAR | verorun "
                "login -u admin --password-stdin`), or omit the option to enter the "
                "password interactively."
            ))
        else:
            password = click.prompt(_("Password"), hide_input=True)

        auth.login_password(username, password, captcha_id=captcha_id)
        state = auth.token_state()
        if state.get("expires_at_text"):
            click.echo(_("Login succeeded; the token is valid until {expires_at}.",
                         expires_at=state["expires_at_text"]))
        else:
            click.echo(_("Login succeeded."))

    @group.command("logout")
    @click.pass_context
    def logout(ctx):
        """登出并清除本地凭证"""
        ctx.obj["auth"].logout()
        click.echo(_("Logged out."))

    @group.command("whoami")
    @click.pass_context
    def whoami(ctx):
        """显示当前登录用户信息（GET /user/profile）"""
        client = ctx.obj["client"]
        auth = ctx.obj["auth"]
        data = client.request_json("GET", "/user/profile")
        if wants_json(ctx):
            show(ctx, {"profile": data, "token": auth.token_state()})
            return
        click.echo(_("Logged in as: {user}", user=_extract_user(data)))
        state = auth.token_state()
        if state.get("expires_at_text"):
            suffix = _("(expired)") if state.get("expired") else ""
            click.echo(_("Token valid until: {expires_at}{suffix}",
                         expires_at=state["expires_at_text"], suffix=suffix))


def _extract_user(payload: Any) -> Any:
    """从 /user/profile 响应中稳健地取出用户信息。

    原实现 `(data.get("data") or data).get("user", data)` 在 `data["data"]`
    为字符串（服务端返回消息文本）时会抛 AttributeError。
    """
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    if isinstance(data, dict):
        user = data.get("user")
        return user if user is not None else data
    if data is not None:
        return data
    user = payload.get("user")
    return user if user is not None else payload
