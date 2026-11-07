#!/usr/bin/env python3
"""发行版（edition）标识归一化 —— 全站唯一事实源。

本模块**只允许依赖标准库**：不得 import DB / i18n / Web 框架。
原因：license、部署脚本、CLI、guardian 等进程需要在没有数据库上下文的
情况下判定发行版；一旦依赖重模块，判定会因 import 失败而 fail-closed 误判。
（2026-09-18：该逻辑原内联在 agent_matrix/models.py，而该模块 import 时会执行
DB 迁移，导致无 DB 环境下 is_enterprise_edition() 抛错并返回 False。）

agent_matrix/models.py 再导出本模块的全部公开符号，既有调用方无需改动。
**禁止**在其它位置重复定义归一化逻辑（第二处定义会导致口径分裂）。
"""
import os

# ── 发行版 edition 归一化（单一事实源：VR_EDITION / RELEASE_EDITION / DEPLOY_TYPE）──
# 现行发行版 ID 共 6 个（定稿后不再变更；显示名走 i18n，与 ID 解耦）：
#   enterprise 企业版 / standard 标准版 / tarka 塔卡投研 /
#   finance 金融版 / research 科研版 / minipro 小程序版
# 2026-11-07 负责人裁决：pro（专业版）、edu（教育版）、edge（边缘版）三版现行均不存在，
# 已物理删除（ID / 别名 / 桥接 / 种子 / 文案全部移除），不做兼容垫片。
# tarka（2026-11-01 用户裁决）：公开产品「塔卡投研」的发行 ID。不用 standard 命名，
# 因 standard 同时是发布仓库 fanjumin/verorun-standard 的名字，二者混用会产生误解；
# 兼容口径由下文 _EDITION_COMPAT_UNION 承接（声明 standard 的插件在 tarka 下同样放行）。
_EDITION_IDS = ('enterprise', 'standard', 'tarka', 'finance', 'research', 'minipro')

# 已下架发行版及其历史旧名（**仅**用于 fail-loud 诊断，禁止在此恢复任何语义）。
# 命中即报错：下架后若继续静默直传，<stem>.yaml 必然缺失 → include 白名单为空 →
# 全插件可见且授权判定失效（见 edition_artifact_stem 的说明）。
_RETIRED_EDITION_IDS = (
    'pro', 'pro-web',
    'edu', 'edu-desktop',
    'edge', 'vr_test_edge', 'edge-computing',
)

# 历史旧名 / 别名 → 现行 ID（大小写不敏感）
# 'official' 为 enterprise 的历史别名，过渡期保留；显式写入 enterprise 同样有效。
_EDITION_ALIASES = {
    'official': 'enterprise',
    'enterprise-web': 'enterprise',
    'standard-web': 'standard',
    'tarka-web': 'tarka',
    'finance-desktop': 'finance',
    'research-desktop': 'research',
    # 全网智能客服：旧名（omni / omni-desktop）→ 现行 ID omni-service（2026-10-21 用户裁决：
    # 桌面版废弃，改服务器 + 轻壳的 omni-service 版）
    'omni': 'omni-service',
    'omni-desktop': 'omni-service',
    'mini': 'minipro',
}


def normalize_edition(e) -> str:
    """归一化发行版标识（大小写不敏感）：旧名/别名→现行 ID，空→standard。"""
    e = (e or '').strip().lower()
    return _EDITION_ALIASES.get(e, e) or 'standard'


def current_edition() -> str:
    """全系统发行版唯一判定：VR_EDITION → RELEASE_EDITION → DEPLOY_TYPE。"""
    return normalize_edition(os.getenv('VR_EDITION') or os.getenv('RELEASE_EDITION')
                             or os.getenv('DEPLOY_TYPE') or '')


# ── 插件兼容判定的发行版并集（改名不改插件声明）──
# tarka 是 standard 的产品化命名：存量插件在 plugin.json 的 compatible_editions 里
# 登记的是 'standard'（42 个），改名时**不要求**逐个改写插件声明，改由本表在判定侧
# 把 'tarka' 展开为 {'tarka','standard'}。新增发行版若与旧 ID 同义，在此登记即可。
# 未登记的 ID 原样返回，不改变既有行为。
_EDITION_COMPAT_UNION = {
    'tarka': ('tarka', 'standard'),
}


def edition_compat_values(edition: str = None) -> tuple:
    """当前发行版在插件兼容判定时应被接受的全部 ID（含同义旧名）。"""
    e = normalize_edition(edition or current_edition())
    return _EDITION_COMPAT_UNION.get(e, (e,))


# ── canonical ID → 磁盘产物名（**唯一**桥接点）──
# canonical ID（_EDITION_IDS，6 个）与磁盘产物命名历史上分叉：
#   deploy/editions/<stem>.yaml、agent_matrix/roles/<stem>/
# 磁盘侧仍沿用旧名 / 连字符名（official / finance-desktop / research-desktop）。
# 二者必须显式桥接，**禁止**在各消费点自行字符串拼接：`f'{current_edition()}.yaml'`
# 这类写法在 ID 收敛为 finance/research 后必然落空（E-1：金融桌面版「订阅到期即锁」
# 与 include 白名单同时静默失效）。新增或改名产物只改这张表。
_EDITION_ARTIFACT_STEMS = {
    'enterprise': 'official',
    'standard': 'standard',
    'tarka': 'tarka',
    'finance': 'finance-desktop',
    'research': 'research-desktop',
    # 全网智能客服（omni-service）版（2026-10-21 用户裁决）：服务器部署 + 桌面轻壳远程接入。
    # 磁盘产物沿用连字符名 omni-service.yaml，与 finance/research 同惯例。
    'omni': 'omni-service',
    'omni-service': 'omni-service',
}


def edition_artifact_stem(edition: str = None) -> str:
    """canonical ID → 磁盘产物名主干；未登记的一律同名直传。

    已下架发行版（pro / edu / edge 及其历史旧名）**直接抛错**：下架后若继续静默
    同名直传，``deploy/editions/<stem>.yaml`` 必然缺失 → include 白名单取数落空 →
    全部插件可见、可装，且授权判定同时失效（静默放开，而非报错）。
    """
    e = edition or current_edition()
    if e in _RETIRED_EDITION_IDS:
        raise RuntimeError(
            f'发行版已下架: {e!r}（pro / edu / edge 三版已移除）。'
            f'请把 VR_EDITION / RELEASE_EDITION / DEPLOY_TYPE 改为: '
            f'{", ".join(_EDITION_IDS)}'
        )
    return _EDITION_ARTIFACT_STEMS.get(e, e)


# ── 内测通道（2026-11-07 用户裁决）──
# 内测版与正式版共用同一 edition（finance）与同一套磁盘产物（角色集 / include 白名单），
# 唯一差异是「关闭授权强制」。因此用**单一环境变量**做发行通道标识，而不是新增 edition ID：
#   VERORUN_CHANNEL=beta  → 内测版（免授权，界面标注「内测版」）
#   未设置 / 其它值        → 正式版（行为完全不变，授权 fail-closed）
# 这样 pack 脚本只需注入一个变量，roles/include/payload 校验全部沿用正式版口径。
_BETA_CHANNEL_VALUES = ('beta', 'insider')


def is_beta_channel() -> bool:
    """是否内测通道（VERORUN_CHANNEL=beta）；正式版构建不注入该变量。"""
    return (os.getenv('VERORUN_CHANNEL') or '').strip().lower() in _BETA_CHANNEL_VALUES
