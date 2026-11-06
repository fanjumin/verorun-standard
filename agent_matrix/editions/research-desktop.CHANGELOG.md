# research-desktop — 发行版 Changelog

```yaml
edition:            research-desktop
edition_version:    2.0.0
kernel_baseline:    0.66.2
governing_yaml:     deploy/editions/research-desktop.yaml
constitution:       agent_matrix/kernel.AGENTS.md (L1) + agent_matrix/editions/research-desktop.AGENTS.md (L2)
```

本文件记录**发行版维度**的变更，与仓库根 `CHANGELOG.md`（内核版本流水）解耦但保持基线映射。
`kernel_baseline` 指该发行版版本所基于的内核版本；缺失此项视为不合规条目。

---

## 记录规范

### 分类（五类，无条目则省略该节）

| 分类 | 含义 |
|---|---|
| `Added` | 新增插件、角色、能力、条款 |
| `Changed` | 门控开关变更、白名单调整、行为语义变更 |
| `Fixed` | 修复与声明不符的实现 |
| `Security` | 许可、出站、密钥、合规相关变更 |
| `Removed` | 插件移出、角色下线、条款废弃 |

### 必须记录的六类发行版事件

1. 插件 `include` / `exclude` 增删（附裁决来源与日期）
2. 角色集变化（增删角色、`managed_modules` 调整）
3. `compatible_editions` 变化导致的可见性变化
4. `license_enforcement` / `runtime_whitelist` 开关变更
5. L2 宪法条款新增、修改、废弃
6. 桌面壳版本与打包 profile 变更

### 版本策略

发行版独立 Semver，与内核版本解耦：

- **MAJOR**：白名单或角色集发生破坏性重组、许可语义变更
- **MINOR**：新增插件 / 角色 / 能力 / 宪法条款
- **PATCH**：修复、文案、配置注释

---

## [Unreleased]

无。

---

## [1.0.0] — 2026-10-19

首次基线化：为 research-desktop 建立独立的发行版宪法与变更记录。此前该版的事实分散在 `deploy/editions/research-desktop.yaml` 的注释、白皮书增补第 6 章与 `veroscholar` 插件 CHANGELOG 中，无法回答"科研版某次交付包含哪些插件与角色"。

### Added

- `agent_matrix/editions/research-desktop.AGENTS.md`（L2 增量宪法），共 15 条（RES-01…RES-15），覆盖引用真实性与核验、学术诚信、角色分离、检索可复现、记忆后端约束、本版失败语义、基线声明
- `agent_matrix/kernel.AGENTS.md`（L1 运行期内核宪法），共 12 条不可协商条款（NT-01…NT-12），全发行版共享
- L1/L2 命名约定：必须使用 `<name>.AGENTS.md` 带前缀形式。仓库 `.gitignore` 的裸 `AGENTS.md` 规则（"内部 AI 规则，永不发布"）无路径前缀，会排除任意层级同名文件；裸名会使 L1 静默不入库且无报错（见 L1 §0.5）
- 本 CHANGELOG 及记录规范
- 基线快照登记：插件 `include` 8 项、`exclude` 17 项、角色集 7 个、`veroscholar` 能力命名空间 23 项（`lit.*` 6 / `citation.*` 3 / `exp.*` 3 / `writing.*` 4 / `discovery.*` 7）

### Changed

无（本次仅建立记录载体，不改动任何门控与白名单）。

### Fixed

- 记录一处既存不一致：`agent_matrix/prompts/master_prompt.md` 的 Role Roster 仍含 `Vision` / `Creative` 两个已被 v0.62.0 收敛删除的角色，且 IRON RULES 1–3 为面向开发者的施工纪律，被注入运行期 Agent 会造成语义污染；该文件同时引用 `prompts/system-core-rules.md` 等 5 个在 `agent_matrix/prompts/` 下**不存在**的规则文件，降级路径实际指向空引用。**本次仅记录，未修改**。

### Security

- **登记一处显式缺口**：本版**未启用** `license_enforcement`（该键为 opt-in 语义，不写即不启用），与 finance-desktop 的 fail-closed 策略不一致。此为现状登记，非缺陷判定——是否为本版启用需产品裁决，启用须同步 `deploy/editions/research-desktop.yaml`、L2 §6.4 与本文件。
- 登记记忆后端约束：`cogevolution_substrate` 已在 `exclude` 显式排除，认知进化唯一后端为 `memory_engine`
- 登记引用诚信门禁：RES-02 引用强制核验、RES-05 学术不端禁止清单、RES-08 评审与写作强制分离

### Removed

无。

### Kernel Baseline

- 内核 0.63.0（`VERSION`），对应根 `CHANGELOG.md` 的 `v0.63.0 — 2026-10-08`

---

## [1.1.0] — 2026-10-20

### Added

- 新增 `ops` 角色（`agent_matrix/roles/research-desktop/ops.yaml`，`editions: [research-desktop]`，`role_type: sub`）：系统运维 lane，`managed_modules` 认领 `email`（通知外发通道）与 `vault`（备份 / 还原 / 审计）。裁决来源与日期：2026-10-20 用户裁决「email 与 vault 使用 ops，在科研版新增 ops 即可」
- 本角色补齐一处结构性缺口：原 `include` 中 `email` / `vault` / `sms` / `two_factor_auth` 四个插件的 `agent_role`（service / ops）均不在科研版核心角色集，亦无任何角色的 `managed_modules` 认领，`resolve_agent_roles` ③ 判空 → `enable()` 硬拒绝（`PluginStateError('missing_agent_role')`，`plugin_manager/manager.py` 归属校验段）——即"包在内、不可用"。ops 入列后 email / vault 归属闭合；sms / two_factor_auth 移出（见 Removed）
- `method_architect` 认领 `bayes_engine`（角色侧 `managed_modules` ② 通道，`plugin.json` 的 `agent_role=athena` 保持不改）

### Changed

- 插件 `include` 新增 `bayes_engine`（贝叶斯推理插件 v1.0.1：`agent_role=athena`、`compatible_editions=[]` 全版本可见、零运行时依赖、3 表 / 5 测试文件、`min_app_version 0.63.0`）。裁决来源与日期：2026-10-20 用户裁决「科研版新增一个 bayes_engine 插件」
- 裁决依据（同日评估结论）：① 方法论同构——`skills/research-belief-revision` 服务「竞争假设 → 实验证据 → 贝叶斯因子 → 信念修正」，补 `method_architect`（`exp.hypothesis`/`exp.methodology`/`exp.validation`）缺的定量信念更新一环，与 RES-09（方法学与结论边界）/ RES-10（检索可复现）同向；② 归属——`agent_role=athena` ① 命中本版核心角色集（兜底），并按下方「bayes_engine 跨版认领惯例」由 `method_architect` ② 认领为功能性归属；③ 同日对照评估：`butterfly_engine` / `chaos_engine`（原生市场脆弱性 / 混沌动力学语义，科研版无时序数据入口）与 `search_hub`（检索覆盖与 veroscholar 重叠、web 结果非学术元数据与 RES-01/02 存在张力）**不纳入**，按挂起登记，触发条件见 L2 宪法 §7 变更纪律
- L2 宪法基线同步：§6.1 `include` 7 项（`_base` + `bayes_engine` + `captcha_embedded` + `email` + `memory_engine` + `vault` + `veroscholar`）/ `exclude` 19 项；§6.2 角色集 8 个（新增 `ops`）
- **`bayes_engine` 跨版认领惯例（本次定稿，两版对称）**：① `plugin.json` 的 `agent_role=athena` 为全版本兜底归属，任何版本不得更改插件侧契约；② 桌面封装版按「定量 / 方法学」功能区由角色侧 `managed_modules` 认领（② 通道）——金融版 `rs_quant`（依据 FIN-14 量化口径与统计域），科研版 `method_architect`（依据 RES-09/10 方法学与可复现域）；③ 认领注释统一采用既有惯例格式：日期 + 裁决来源 + 依据条款 + ② 通道声明（模板见 `roles/finance-desktop/ops.yaml`、`roles/finance-desktop/rs_quant.yaml` 内联注释）

### Removed

- `sms`、`two_factor_auth` 移出 `include` 转入 `exclude`。裁决来源与日期：2026-10-20 用户裁决「科研版不需要 sms、two_factor_auth」。依据：科研单机版 `user_login: false`，无短信验证 / 二次因子场景；`auth-center/services/session_service.py` 对 `two_factor_auth unavailable` 有显式降级路径（无硬依赖）；且二者在原 `include` 内即无归属角色（enable 必拒），属"包在内、不可用"的死重——移出不破坏任何既有可用能力

### Kernel Baseline

- 内核 0.64.0（`VERSION`，对应根 `CHANGELOG.md` 的 `v0.64.0 — 2026-10-20`；本版与内核 0.64.0 同批发布，故基线随之抬升，原 1.0.0 基线 0.63.0 保留于上一节不改）

---

## [2.0.0] — 2026-11-05

与金融桌面版（`finance-desktop`）做发行接线对齐，范围**仅限非业务插件层**：门控开关、通用能力插件、角色认领与基线声明。业务插件（`veroscholar` 及科研角色集）不变。

### Added

- 插件 `include` 新增 `net_proxy`（统一出站网络代理 v1.0.3）。裁决来源与日期：2026-11-05 用户裁决「net_proxy 必须接，否则可能会遇到其他区的文献找不到」。依据：境内部署访问 arXiv / PubMed / Crossref / Web of Science 等境外文献源需经统一出站路由，`search_hub` 与 `veroscholar` 的 `discovery.*` 均受益。归属零成本：`net_proxy.agent_role=ops` ① 命中本版核心角色集，无需角色侧认领
- 插件 `include` 新增 `im_gateway`（统一即时消息通道网关 v3.3.0）。裁决来源与日期：2026-11-05 用户裁决「im_gateway 这个必须」。依据：科研长任务（批量文献检索 / 综述生成 / 实验批处理）的完成与异常需 IM 即时通知，与 `email` 同级通道
- `ops` 角色 `managed_modules` 认领 `im_gateway`（② 通道）。其 `plugin.json` 的 `agent_role=service` 保持官方版语义不改，与金融版 ops 认领 `email` / `im_gateway` 同一惯例

### Changed

- 插件 `include` 由 10 项增至 12 项（`_base` + 11 项业务插件）
- **`runtime_whitelist: true`（2026-11-05 对齐金融版）**。此前未声明（按 `false` 语义运行）。影响：标准安装包因发包已物理裁剪而不发作；但开发模式（壳层 `codeDir` 直指核心仓源码，`plugins/` 为全量目录）与整盒部署切 `VR_EDITION=research` 时，插件中心会列出磁盘上全部插件（金融版 2026-09-21 实测 ~39 个）。声明后插件中心 / discover / enable / unified 四处门控同源，只放行 `include`
- `plugins/net_proxy/plugin.json`、`plugins/im_gateway/plugin.json` 的 `compatible_editions` 追加 `research`。**前置条件**：不改此项则 discovery 按 `compatible_editions` 硬拒绝，加进 `include` 也不可用

### Fixed

- **基线漂移修正**：L2 宪法 §6.1 此前声明 `include` 7 项 / `exclude` 19 项（含 `project_workspace`），而 yaml 自 2026-10-27 起已是 `include` 10 项 / `exclude` 18 项且 `project_workspace` 在 `include` 内。文档落后实际 9 天且违反 §7.2「只改 yaml 不改本文件视为基线漂移」。本次一并对齐为 `include` 12 / `exclude` 18，并补齐 §6.2 `ops` 认领范围（`search_hub` 2026-10-22 已加入但未记录）
- `agent_matrix/editions/finance-desktop.AGENTS.md` §6.1 同样漂移（仍列 2026-10-26 已移除的 `hr_recruit` / `visitor_profile`、2026-10-29 已移除的 `coupons`，且写「17 项业务插件」而实际为 14 项），同批修正

### Security

- **启用 `license_enforcement: true`（fail-closed）**。裁决来源与日期：2026-11-05 用户裁决「肯定是商用分发」。按 L2 §7.5 四步顺序执行：① `deploy/editions/research-desktop.yaml` 声明 → ② 本条目 → ③ §6.4 更新（已完成）→ ④ 本 `Security` 分类记录（本处）
- 消费点与后果：`admin/app.py` 的 `_license_enforced()` 会锁 `/admin` 页面（跳 `/admin/renew`）与插件业务端点（403 `subscription_expired`）；`agent_matrix/routes.py` 的 `_ai_license_enforced()` 会锁 AI / Agent 能力（403）。**首次安装未配置部署码即全锁**，用户须经 `/admin/renew` 写入 `system_config.deployment_code` 并联网心跳激活
- 开发机自测：需 `VERORUN_DEV_UNLICENSED=1` + 官方签名凭证（`_official_dev_exempt()` 的 `_verify_official_token()` Ed25519 验签）。客户仓库无 `official_token.json(.sig)`，该豁免不成立，不构成商业绕过
- 登记记忆后端约束不变：`cogevolution_substrate` 仍在 `exclude`，认知进化唯一后端为 `memory_engine`

### Removed

无（本次未移出任何插件或角色）。

### Kernel Baseline

- 内核 0.66.2（`VERSION`）。本次对齐基于该内核构建载荷；1.1.0 的 0.64.0 基线保留于上一节不改

---

## 基线回填（Baseline Trace）

以下条目为**事后回填**，来源为 `deploy/editions/research-desktop.yaml` 的设计注释与白皮书增补第 5/6 章，**不是当年当日的原始变更记录**。未经原始发布单核验的项已标注。

| 日期 | 事件 | 来源 | 置信 |
|---|---|---|---|
| 2026-09-07 | 用户拍板：认知进化后端采用 `memory_engine` 全量版，不使用 `cogevolution_substrate`；`project_workspace` 移出科研版；`vault` 保留 | yaml 注释 | 已核 |
| 2026-09-08 | 用户拍板：`content_factory` / `site_domains` 对科研版无价值，移入 `exclude` | yaml 注释 | 已核 |
| 日期待补 | `enterprise_verify` 进入 `exclude` | 现行 yaml（注释未载明日期） | 待核 |
| 日期待补 | `veroscholar` 升至 v1.11.4，能力命名空间 23 项 | 白皮书增补第 6 章 6.2 | 版本已核 / 时点待补 |

**回填边界说明**：`include` 的定稿时点、角色集（7 个）的历史变更过程无可靠来源，未回填。`educational` / verorun-edu 路径已退役，学术场景统一由本版承接——该迁移的日期与决策记录不在本仓库内，如需追溯应查分发仓库历史。

---

## 变更操作检查清单

发布新版本前逐项确认：

- [ ] `edition_version` 递增符合 Semver 策略
- [ ] `kernel_baseline` 与 `VERSION` 一致
- [ ] L2 宪法 §6 基线与 `deploy/editions/research-desktop.yaml` 逐项一致（`include` 12 / `exclude` 18 / `roles` 8）
- [ ] 白名单变更已在 `Changed` 分类记录，附裁决来源
- [ ] 门控开关变更已在 `Security` 分类记录
- [ ] 条款废弃使用 `deprecated` 标记，编号不复用
- [ ] 根 `CHANGELOG.md` 未因本版变更而被改写（两者独立维护）
- [ ] 若启用 `license_enforcement`，已按 L2 §7.5 的四步顺序执行
