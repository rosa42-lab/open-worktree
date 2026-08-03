# orch v1.3 Phase 0 — Capability Matrix

**任务：** V13-001（Phase 0）+ V13-009（provider 合约关闭项）  
**日期：** 2026-08-01 / 更新 2026-08-01（V13-009）  
**状态：** Phase 0 **PASS**；V13-009 合约测 **PASS**（`tests/test_github_provider_contract.py`）

> `unknown` 不得当作通过。写路径（promote / release）在 orch 实现完成前仍由代码禁用未完成路径；平台侧 `direct_ff` 模式已冻结。  
> **Phase 0 边界：** 致命写边界 / 禁 force / merge-commit 父提交已实测；远端 `release-sync` FF 归 Phase 3 / V13-011。  
> **§16 / §17 口径：** Phase 0 硬退出 ≠ 方案 §16 表内全部「致命」行。

## 模式决策

| 项 | 值 |
|---|---|
| `mode_decision` | **`direct_ff`** |
| 决策依据 | Integration App 可 CAS/FF 更新 `develop`；`develop-no-force` 拒绝 force；不能直推 `master`；Release App Contents R-only + PR R+W |
| 签署人 / 日期 | 独立抽测确认 2026-08-01 |

## §8.2 五类检查

| 类别 | 检查 | 状态 | 证据 |
|---|---|---|---|
| Git | remote 可达 | pass | `gh` / App API |
| Git | develop/master refs 存在（非 default branch 推断） | pass | develop=`a1b410e…` master=`599bba62…` |
| Git | 默认 fetch 行为 | pass（本地 probe） | `remote-probe` |
| 身份 | Bot 身份 / 可见仓库范围 | pass | App installation；V13-009 `probe_capabilities` |
| develop policy | 禁 force/delete；仅 Integration Bot | pass | Ruleset + V13-009 `branch_policy` |
| master policy | PR-only、审批、checks、stale、OrganizationAdmin temp bypass | pass | Ruleset；stale **观测**仍可 E2E 补 |
| provider | 创建/查询 PR、checks；非 force | pass（合约）/ E2E 待补 | V13-009 mock 全绿；真实仓烟测可选 |

## §16 假设

| 风险 | 假设 | 结论 |
|---|---|---|
| 致命（P0 硬退出） | Ruleset 禁 master 直推 | **pass** |
| 致命（P0 硬退出） | Integration App 可写 develop、不可写 master | **pass** |
| 致命（P0 硬退出） | Integration 不能 force develop | **pass**（拆 ruleset 后） |
| 致命（§16 表；非 P0 硬退出） | promotion-policy 拒非 develop→master | **合约 mock pass**（#10 字段形状）；真实拒合并 → **V13-012** E2E |
| 高 | 非 force CAS 拒绝旧 SHA 竞态 | **pass**（`RemoteGitAdapter` / V13-007 测）；probe 不重推 |
| 高 | merge commit + release-sync FF 回 develop | pending（PR#1 证明 merge commit；远端 sync → **V13-011**） |
| 高 | stale approval 失效 | Ruleset 已配；**观测** → **V13-012** |
| 高 | credential 不进 argv/log | **pass**（token 仅内存；HTTP 脱敏单测） |

## 交付物对照

| 交付物 | 状态 |
|---|---|
| Ruleset / App | ✅ |
| §7 Runtime 实测 | ✅ PASS |
| `mode_decision=direct_ff` | ✅ 冻结 |
| #9 bot 审批不计 code owner | ✅ **V13-009** mock（`get_reviews`） |
| #10 坏 head / promotion-policy required check 形状 | ✅ **V13-009** mock；真实拒合并 → V13-012 |
| `GitHubProviderAdapter` + `remote-probe` 接入 | ✅ V13-009 |
| 字段契约 | ✅ `docs/probe/v13-provider-field-contract.md` |
