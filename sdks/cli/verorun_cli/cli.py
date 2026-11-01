"""verorun 命令行入口（click 命令树组装）。

所有命令均指向 VeroRun 服务端现有 REST 端点；CLI 本身不实现任何 Agent 逻辑。
严格遵守服务端门禁：非 2xx（含 3xx 续费/登录重定向）一律按错误处理，绝不忽略。

异常处理策略（本轮修复）：
    各命令模块**不再**各自 try/except + SystemExit——那样既重复，
    又让这里的统一处理沦为死代码。现在所有 CLI 异常统一在 `main()` 收敛，
    由 `_handle_error()` 决定文案与退出码，`--debug` 时打印完整堆栈。

退出码约定：
    0   成功
    1   CLI 已知错误（认证 / API / SSE 等）
    2   参数用法错误（由 click 抛出 UsageError）
    130 用户 Ctrl-C 中断
"""

from __future__ import annotations

import click

from . import __version__
from .auth import AuthManager
from .client import VeroRunClient
from .commands import register_all
from .config import Config
from .constants import DEFAULT_TIMEOUT, _
from .exceptions import APIError, AuthError, SSEError, VeroRunError

_DEBUG = False


def _build_cli() -> click.Group:
    """在运行时构建命令树。

    click 的 `help` 文案在装饰器求值时就固定下来；若在模块导入期求值翻译，
    语言会被定死在进程启动时的环境。因此把命令树构建收进函数，由 `main()` 调用。
    """

    @click.group()
    @click.option("--base-url",
                  help=_("Server endpoint (overrides config and VERORUN_BASE_URL)"))
    @click.option("--insecure", is_flag=True,
                  help=_("Disable SSL certificate verification (dev self-signed certificates only)"))
    @click.option("--json", "as_json", is_flag=True,
                  help=_("Output as JSON for script integration"))
    @click.option("--timeout", type=int, default=None,
                  help=_("Request timeout in seconds (default {default})", default=DEFAULT_TIMEOUT))
    @click.option("--debug", is_flag=True, help=_("Print the full traceback on errors"))
    @click.pass_context
    def cli(ctx: click.Context, base_url, insecure, as_json, timeout, debug) -> None:
        """VeroRun AI 命令行客户端。"""
        global _DEBUG
        _DEBUG = debug

        config = Config()
        client = VeroRunClient(
            config,
            base_url=base_url,
            verify_ssl=(False if insecure else None),
            timeout=timeout or DEFAULT_TIMEOUT,
        )
        auth = AuthManager(client, config)
        auth.bind(client)

        ctx.ensure_object(dict)
        ctx.obj["config"] = config
        ctx.obj["client"] = client
        ctx.obj["auth"] = auth
        ctx.obj["as_json"] = as_json
        ctx.obj["debug"] = debug
        ctx.call_on_close(client.close)

    @cli.command()
    def version() -> None:
        """显示版本号。"""
        click.echo(f"verorun {__version__}")

    return cli


def _handle_error(exc: BaseException) -> None:
    if isinstance(exc, AuthError):
        click.echo(_("Authentication error: {error}", error=exc), err=True)
    elif isinstance(exc, APIError):
        click.echo(_("API error: {error}", error=exc), err=True)
    elif isinstance(exc, SSEError):
        click.echo(_("Streaming error: {error}", error=exc), err=True)
    elif isinstance(exc, VeroRunError):
        click.echo(_("Error: {error}", error=exc), err=True)
    else:
        click.echo(_("Unexpected error: {error}", error=exc), err=True)
    if _DEBUG:
        import traceback

        traceback.print_exc()
    raise SystemExit(1)


def main() -> None:
    cli = _build_cli()
    register_all(cli)
    try:
        cli()
    except VeroRunError as exc:
        # AuthError / APIError / SSEError 均继承自 VeroRunError
        _handle_error(exc)
    except KeyboardInterrupt:
        click.echo("\n" + _("Interrupted."), err=True)
        raise SystemExit(130)
    except BrokenPipeError:
        # 下游管道提前关闭（如 `verorun agents list | head`），静默正常退出
        raise SystemExit(0)


if __name__ == "__main__":
    main()
