"""shim — _base 收归步骤二（2026-10-17）：实体已迁至 shared/plugin_base/pii.py。

保留本垫片保 `from plugins._base.pii import ...` 路径可用：
- 40+ 消费方零改动（含 agent_matrix 内核、deploy 脚本与全部业务插件）
- plugin_manager.SHARED_PLUGIN_MODULES 安装期校验按 plugins/_base 目录存在性放行
- deploy/editions/*.yaml include 与三个 sync workflow 的 cp -r 均按目录拷贝

步骤三（清 shim）随商店包 import 路径迁移后执行，可无限期推迟。
"""

from shared.plugin_base import pii as _impl
from shared.plugin_base.pii import *  # noqa: F401,F403 — 公开符号转发


def __getattr__(name):
    """转发实体模块全部属性（含下划线符号与后续新增）。"""
    return getattr(_impl, name)
