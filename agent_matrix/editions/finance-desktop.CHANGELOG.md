# finance-desktop — 发行版 Changelog

```yaml
edition:            finance-desktop
edition_version:    1.2.0
kernel_baseline:    0.64.1
governing_yaml:     deploy/editions/finance-desktop.yaml
constitution:       agent_matrix/kernel.AGENTS.md (L1) + agent_matrix/editions/finance-desktop.AGENTS.md (L2)
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

1. 插件 `include` 增删（附裁决来源与日期）
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

首次基线化：为 finance-desktop 建立独立的发行版宪法与变更记录，结束「发行版事实只存在于 yaml 注释与内核 CHANGELOG 零散条目中」的状态。

### Added

- `agent_matrix/editions/finance-desktop.AGENTS.md`（L2 增量宪法），共 16 条（FIN-01…FIN-16），覆盖投资类输出约束、数据许可出站、多空对辩纪律、合规角色边界、本版失败语义、基线声明
- `agent_matrix/kernel.AGENTS.md`（L1 运行期内核宪法），共 12 条不可协商条款（NT-01…NT-12），全发行版共享
- L1/L2 命名约定：必须使用 `<name>.AGENTS.md` 带前缀形式。仓库 `.gitignore` 的裸 `AGENTS.md` 规则（"内部 AI 规则，永不发布"）无路径前缀，会排除任意层级同名文件；裸名会使 L1 静默不入库且无报错（见 L1 §0.5）
- 本 CHANGELOG 及记录规范（六类必须记录的发行版事件、五类分类、独立 Semver 策略）
- 基线快照登记：插件白名单 18 项（`_base` + 17）、角色集 8 个、能力命名空间 4 项、MCP 工具 `stock`

### Changed

无（本次仅建立记录载体，不改动任何门控与白名单）。

### Fixed

- 记录一处既存不一致：`agent_matrix/prompts/master_prompt.md`（Athena 静态降级源）的 Role Roster 仍含 `Vision` / `Creative` 两个已被 v0.62.0 收敛删除的角色，且 IRON RULES 1–3 为面向开发者的施工纪律（"先出方案等确认才能生成代码"），被注入运行期 Agent 的 system prompt 会造成语义污染。**本次仅记录，未修改**。

### Security

- 登记本版 fail-closed 语义三处：`license_enforcement: true`（订阅到期即锁，不提供只读降级）、LLM 出站许可 `ma_license.allow_llm`（未授权即拒绝，缓存命中同样受约束）、`runtime_whitelist: true`（包外插件不可见、不可启用）

### Removed

无。

### Kernel Baseline

- 内核 0.63.0（`VERSION`），对应根 `CHANGELOG.md` 的 `v0.63.0 — 2026-10-08`

---

## [1.1.0] — 2026-10-20

### Changed

- 角色 `managed_modules` 调整：`rs_quant` 认领 `bayes_engine`（`roles/finance-desktop/rs_quant.yaml`，② 通道，`plugin.json` 的 `agent_role=athena` 保持不改）。裁决来源与日期：2026-10-20 用户裁决「金融版里，如果有必要，也可以添加角色」
- 同日审计结论：本版 18 个 include 插件**全部有归属**（① `agent_role` 命中核心角色集或 ② 角色侧 `managed_modules` 认领），**不需要新增角色**。唯一薄弱点为 `bayes_engine` 仅经 ① 聚合到 athena，而其金融向 Skill（`signal-calibration` / `source-reliability` / `sentiment-credibility`）的原生使用者是 `rs_quant`（FIN-14：量化口径与统计域）——不认领则 rs_quant 调用 `bayes.*` 构成 NT-03 越权，故按 ② 补认领，不扩角色集
- 角色集不变（仍 8 个），L2 §6.2 无需修改；插件白名单不变（18 项）

### Kernel Baseline

- 内核 0.64.0（`VERSION`，对应根 `CHANGELOG.md` 的 `v0.64.0 — 2026-10-20`；本版与内核 0.64.0 同批发布，故基线随之抬升，原 1.0.0 基线 0.63.0 保留于上一节不改）

---

## [1.2.0] — 2026-10-26

### Added

- 插件 `include` 增补 `neural_flow`（v1.2.1，平台级数据流可观测基座）。归属：`plugin.json` 的 `agent_role=ops` 直接命中本版 8 角色集（① 通道），未使用 `managed_modules` 认领。裁决来源：2026-10-26 用户裁决「插件已重构（UI 问题已修复），重新加入金融版」
- 下线原因（2026-10-22）与修复对照：① 事件流仅有心跳 → v1.2.0 采集器双源（`agent_token_logs` / `agent_tasks`）+ `emit_realtime`；② 页面自承未接业务埋点 → 内核 `task_id` 全链路透传 + `obs_execution_view` / `obs_attribution` 归并视图
- 插件白名单回到 `_base` + 17 项业务插件（共 18 项）；角色集不变（仍 8 个），L2 §6.2 无需修改

### Changed

- `kernel_baseline` 由 0.64.0 纠正为 0.64.1（`VERSION` 实际值；仅文档修正，不改内核代码）

### Kernel Baseline

- 内核 0.64.1（`VERSION`，对应根 `CHANGELOG.md` 的 `v0.64.1 — 2026-10-24`）

---

## 基线回填（Baseline Trace）

以下条目为**事后回填**，来源为 `deploy/editions/finance-desktop.yaml` 的设计注释、白皮书增补第 5/6 章与 git 历史，**不是当年当日的原始变更记录**。日期与结论以来源为准，未经原始发布单核验的项已标注。

| 日期 | 事件 | 来源 | 置信 |
|---|---|---|---|
| 2026-09-11 | 产品决策：桌面金融版「订阅到期即锁」，`license_enforcement` 一律 fail-closed | yaml 注释 | 已核 |
| 2026-09-21 | `currency_converter` 整插件移出（本版无结算/换算场景） | yaml 注释 | 已核 |
| 2026-10-01 | `multi_asset` 移入（多资产行情，`compatible_editions` 已含 finance）；同日 `project_workspace`、`neural_flow` 加入 | yaml 注释 | 已核 |
| 2026-10-06 | `content_factory` 加入；`runtime_whitelist` 由 `false` 改为 `true`（include 兼任运行期白名单） | yaml 注释 + 白皮书 5.2 | 已核 |
| 2026-10-12 | `bayes_engine`、`butterfly_engine` 加入（用户裁决"两个插件都放行"） | yaml 注释 | 已核 |
| 2026-10-13 | `hr_recruit`、`visitor_profile`、`coupons` 加入（104 测试机需覆盖全部插件） | yaml 注释 | 已核 |
| 2026-10-14 | `chaos_engine` 加入（描述性风险原语，非交易信号） | yaml 注释 | 已核 |
| 2026-10-18 | `search_hub` 锁入 include（commit `35da801d`） | git 历史 + 现行 yaml | 已核 |
| 未明确日期 | `health_check` 移出（单机桌面版 16 项中仅 3 项有对象，4 项恒定假告警） | 白皮书 5.2 / 项目决策记录 | 日期待补 |

**回填边界说明**：上表仅覆盖 `include` 与门控开关。角色集（8 个）与 `core_files` 的历史变更时点无可靠来源，未回填；如需追溯，应查 issue / 发布单而非 yaml 注释。

---

## 变更操作检查清单

发布新版本前逐项确认：

- [ ] `edition_version` 递增符合 Semver 策略
- [ ] `kernel_baseline` 与 `VERSION` 一致
- [ ] L2 宪法 §6 基线与 `deploy/editions/finance-desktop.yaml` 逐项一致
- [ ] 白名单变更已在 `Changed` 分类记录，附裁决来源
- [ ] 门控开关变更已在 `Security` 分类记录
- [ ] 条款废弃使用 `deprecated` 标记，编号不复用
- [ ] 根 `CHANGELOG.md` 未因本版变更而被改写（两者独立维护）
