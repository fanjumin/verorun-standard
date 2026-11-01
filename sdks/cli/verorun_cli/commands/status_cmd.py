"""状态 / 健康检查命令。

修复 M5（可达性语义）：
    旧逻辑用 `probe.status_code < 500` 近似"可达"，但把"拿到了任意 HTTP 响应"
    与"业务成功"混为一谈。更稳妥的判定是：**只要服务端回了 HTTP 响应（无论
    2xx/3xx/4xx/5xx），就说明服务可达**；只有连接级失败（ConnectionError 等）
    才算不可达。404 表示"路径不存在但服务活着"，应判为可达而非失败。
    另外 `/` 在 admin:8084 上未必存在业务路由，不能因为 404 就报"无法连接"。
"""

from __future__ import annotations

import click

from ..constants import _
from ..output import emit_json, render
from ._base import wants_json


def register(group):
    @group.command("status")
    @click.pass_context
    def status(ctx):
        """查看 CLI 状态：入口地址、登录态、服务端连通性、凭证保护。"""
        client = ctx.obj["client"]
        auth = ctx.obj["auth"]

        info = {
            "base_url": client.base_url,
            "verify_ssl": client.verify_ssl,
            "authenticated": auth.is_authenticated(),
            "server_reachable": None,
        }

        # 连通性探测：容忍任何非 2xx 响应（auth_required=False + allow_non_ok）
        try:
            probe = client.request(
                "GET", "/", auth_required=False, allow_non_ok=True
            )
            info["server_reachable"] = True  # 拿到了 HTTP 响应即说明服务可达
            info["probe_status"] = getattr(probe, "status_code", None)
        except Exception as e:  # 连接失败 / DNS / SSL 等才是真正不可达
            info["server_reachable"] = False
            info["probe_error"] = str(e)

        # 凭证文件真实权限保护状态（P0 修复后才有意义）
        info["credential_protection"] = auth.credential_protection()

        if wants_json(ctx):
            emit_json(info)
            return

        render(_("Base URL: {value}", value=info["base_url"]))
        render(_("SSL verify: {value}", value=info["verify_ssl"]))
        render(_("Logged in: {value}", value=info["authenticated"]))
        render(_("Server reachable: {value}", value=info["server_reachable"]))
        if "probe_status" in info:
            render(_("Probe status: {value}", value=info["probe_status"]))

        cp = info["credential_protection"]
        if cp.get("token", {}).get("exists"):
            tok = cp["token"]
            status = _("Protected") if tok.get("protected") else _("Not protected")
            render(_("Token protection: {status} ({detail})", status=status, detail=tok.get("detail")))
        else:
            render(_("Token protection: not logged in (no token file)"))

        if info["server_reachable"] is False:
            from ..output import err
            err(_("Cannot connect to the server; check --base-url or your network."))
