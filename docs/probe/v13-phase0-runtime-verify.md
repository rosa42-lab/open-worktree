# Phase 0 §7 Runtime 验证结果

**仓库：** `rosa42-lab/open-worktree`  
**时间：** 2026-08-01（API 实测）  
**状态：** 首次实测 **FAIL** → 修复后重跑 **PASS**（见下方修复记录）  
**凭证：** 使用 `E:\commonSecret\*-private-key.pem` 生成 installation token；密钥与 token **未入库、未写入本文件**。

## 结果表（修复后最终状态）

| # | 项 | 期望 | 结果 | 说明 |
|---|---|---|---|---|
| 1 | refs exist | develop+master 存在 | ✅ | develop=`a1b410e…`（恢复后），master=`599bba62…` |
| 2 | Integration FF push develop | 成功 | ✅ | 空提交 `a1b410e` 快进成功 |
| 3 | Integration push master | 拒绝 | ✅ | HTTP 422 Ruleset：须经 PR + promotion-policy |
| 4 | Integration force push develop | 拒绝 | ✅ | **修复后 HTTP 422**「Cannot force-push to this branch」— `develop-no-force`（无 bypass）拦截 |
| 5 | Release 创建 develop→master PR | 成功 | ✅ | **修复后 HTTP 201** — App 权限已改为 `pull_requests: write` |
| 6 | Release 读 checks | 可读 | ✅ | 修复后经读 PR 实测 200（checks 读权限同源） |
| 7 | Release push master | 拒绝 | ✅ | HTTP 422 Ruleset 拒绝 |
| 8 | Release 推任意分支 | 拒绝（Contents R-only） | ✅ | **修复后 HTTP 403** — Contents 已改 read-only，创建 commit 阶段即被拦截 |
| 9 | Bot 审批不足以满足 Code Owner | 仍 blocked | ⏭ | 未跑（master 已配 require_code_owner_review + CODEOWNERS，机制已由 #5 通路解锁） |
| 10 | promotion-policy 拒非 develop head | check failure | ⏭ | 未跑（留待 **V13-009** 合约 / **V13-012** E2E） |
| 11 | master bypass 无 App | 仅 OrganizationAdmin | ✅ | master-protection bypass = `OrganizationAdmin/always`，无 App |
| 12 | merge commit 路径 | 已有证据 | ✅ | PR #1 = `599bba62` 双父 |

修复明细见 [`v13-phase0-rerun.md`](v13-phase0-rerun.md)。

## 事故与恢复

force-push 探测曾把 `develop` 指到 `master` tip（`599bba62`）。已用 Integration App **强制恢复**到探测时的合法快进 tip：

- `develop` → `a1b410e982aa844aa83f5af44f6a666eff16c685`（phase0 empty commit）
- `master` 未改动：`599bba62cb029a95dc2a897fad9709f59058ecd9`

## 必须修复（按优先级）

### P0 — develop force 未被挡　→　✅ 已修复

`develop-protection` 把 Integration 设为 **Exempt**，等于该 App **不受** 同 ruleset 内 `non_fast_forward` / `deletion` 约束。

**修复：** 已拆成两条 Ruleset：

1. `develop-updates`：仅 `Restrict updates`，bypass = Integration（允许直推）
2. `develop-no-force`：仅 `Block force pushes` + `Restrict deletions`，**bypass 为空**（Integration 也不能 force/删）

**重跑结果：** Integration `force=true` 更新 develop → **HTTP 422**「Cannot force-push to this branch」✅

### P0 — Release App 权限与设计不符　→　✅ 已修复

| 权限 | 设计 / 你的总结 | 首次实测 | 修复后 |
|---|---|---|---|
| Contents | Read-only | **write** | **Read-only** ✅ |
| Pull requests | Read and write | **read** | **Read and write** ✅ |

**重跑结果：** 建 PR **201** ✅、读 PR **200** ✅、push 新分支 **403**（Contents read-only 拦截）✅

### P1 — 重跑未完成项　→　⏭ 部分完成

已跑：建 PR（#5）、读 PR/checks（#6）、Release push 分支（#8）。  
未跑：#9（bot 审批）、#10（坏 head 的 promotion-policy）——留待 **V13-009** 合约测与 **V13-012** E2E（非 V13-002）。

## 复现脚本

- `docs/probe/_phase0_runtime_verify.py` — §7 首次实测（勿把输出里的 URL 当秘密；token 不打印）
- `docs/probe/_phase0_rerun.py` — 修复后重跑 #4/#5/#6/#8
- `docs/probe/_phase0_force_restore_test.py` — 真·非快进 force 测试（验证 non_fast_forward 生效）
- `docs/probe/_phase0_breakglass_restore.py` — break-glass 恢复（临时禁用 ruleset 救场）
- `docs/probe/_phase0_recover_develop.py` — develop 恢复

机器可读：`docs/probe/v13-phase0-runtime-verify.json`、`docs/probe/v13-phase0-rerun.json`（若存在以本次叙述为准）。
