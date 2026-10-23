"""egress_policy.py --- 出站治理的单一事实源（来源→许可映射）。

为什么单独成模块（而不是留在 data_link.py）：同一张映射表被两条链路消费——

  * 入站打标：``data_link._governance_tags`` / ``_register_source`` 用它给
    ``ma_bars`` 行写 license_id / origin / dataset_id，并登记 ``ma_license``；
  * 出站闸门：stock_analysis 的 LLM 出口（``llm_egress.guard_llm_egress``）需要把
    "本次回答用到了哪些源"翻译成 license_id，才能去查 ``ma_license.allow_llm``。

若把映射留在任一侧，另一侧就必须反向 import，形成 stock_analysis <-> multi_asset
的循环依赖。本模块**不 import 任何插件**（仅标准库），因此谁都可以安全引用。

为什么住在 ``plugins/_base/`` 而不是 ``plugins/multi_asset/``（2026-10-13 迁移）：
"只依赖标准库"并不足以免疫循环——导入 ``plugins.multi_asset.任何子模块`` 都会先执行
``multi_asset/__init__.py``（:19 ``from .routes import ...`` → routes:24 ``data_link``
→ data_link:18 ``adapters`` → adapters 模块级复用 stock_analysis），环照样成立。
``plugins/_base`` 是平台钦定的跨插件共享库（``plugin_manager/manager.py:113-115
SHARED_PLUGIN_MODULES``）且为**无 __init__.py 的命名空间包**，导入它不会触发任何插件
包的初始化，因此这里才是跨插件共用件的唯一安全落点。
``plugins/multi_asset/egress_policy.py`` 保留为再导出垫片，既有引用零改动。

数据仍属 multi_asset 的治理域（GB/T 42775-2023）：来源缺席即"免费源"→ 不打标，
而不是编造一个来源。

治理域边界（v1.1 P0 审核 C-2）：本域只覆盖 vendor 行情/财务数据经 LLM 的转发；
embedding 路径（``plugins/_base/embeddings.py``）仅承载论文/画像/简历/项目文档等
非 vendor 数据，不在本域内，故不设闸门——详见该模块 docstring。
"""
from __future__ import annotations

__all__ = ["VENDOR_SOURCES", "vendor_of", "license_ids_for"]

# 商业源的治理标签（许可继承 / GB/T 42775-2023）：
# 入站永不放松提供方许可，scope 默认最严（ma_license.allow_* 默认 0）。
VENDOR_SOURCES = {
    "tushare": {"license_id": "vendor:tushare", "provider": "tushare",
                "origin": "vendor"},
}


def vendor_of(source):
    """把 provider 名映射为治理标签；免费源返回 None（行不打标 == NULL 列）。"""
    return VENDOR_SOURCES.get(str(source or "").strip().lower())


def license_ids_for(sources) -> list:
    """把一组来源名翻译成需要校验的许可 id（保持首次出现顺序，去重）。

    未知来源 / 免费源不产生任何许可 id（无标签的行不承载限制）。
    """
    out = []
    for src in sources or ():
        vendor = vendor_of(src)
        if not vendor:
            continue
        license_id = vendor["license_id"]
        if license_id not in out:
            out.append(license_id)
    return out
