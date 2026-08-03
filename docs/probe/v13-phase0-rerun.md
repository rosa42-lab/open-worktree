# Phase 0 §7 Rerun（修复后实测）

**仓库：** `rosa42-lab/open-worktree`
**时间：** 2026-08-01（API 实测，使用 installation token）
**修复内容：** develop Ruleset 拆分（develop-updates / develop-no-force）+ Release App 权限修正（Contents R-only、PR R+W）

## 结果表

| # | 项 | 期望 | 修复前 | 修复后 | 判定 |
|---|---|---|---|---|---|
| 4 | Integration force push develop | 拒绝（403/422） | ❌ 200 | **422**「Cannot force-push to this branch」 | ✅ 修复 |
| 5 | Release 创建 develop→master PR | 成功（201） | ❌ 403 | **201** | ✅ 修复 |
| 6 | Release 读 PR（代理 checks 读） | 可读（200） | ⏭ 未跑 | **200** | ✅ |
| 8 | Release push 新分支 | 拒绝（403/422） | ❌ 201 | **403**（Contents read-only 在创建 commit 阶段拦截） | ✅ 修复 |

## 关键说明

### #4 的测试方法

初版 rerun 脚本存在假阳性：`create_empty_commit(parent=base_sha)` 创建的是当前 tip 的子提交，把 develop 指到它是**快进**而非 force push，200 属预期行为。

真正的非快进验证：将 develop 从探测残留提交 `565166b` 强推回 `a1b410e`（回退删除提交 = 真实非快进）→ **HTTP 422「Cannot force-push to this branch」**。证明 `develop-no-force` 的 `non_fast_forward` 规则在 REST refs 更新上生效，且 bypass 列表为空时 Integration App 同样被拦截。

### #8 的拦截层级

Release App 的 Contents=Read-only 生效后，权限拦截发生在**创建 commit 阶段**（`POST /git/commits` 返回 403 Resource not accessible），早于创建 branch ref 阶段。比预期的"建分支时拦截"更严格，判定为修复生效。

## 事故与恢复

- 初版 rerun #4 假阳性测试把 develop 推到探测残留提交 `565166b`。
- 尝试直接强推回 `a1b410e` 被 ruleset 正确拦截（422）——这本身再次验证了 #4 修复。
- 走 **break-glass** 恢复：临时禁用 `develop-no-force`（admin token）→ Integration token 强推 `a1b410e`（200）→ 重新启用 `develop-no-force`。
- 恢复后：develop=`a1b410e…`，master=`599bba62…`（未改动），`develop-no-force` 重新 active（deletion + non_fast_forward）。
- 测试副产物清理：PR #1/#2 均已 closed，无残留分支。

## 结论

原 FAIL 的两条 P0 均已修复并通过实测：

| P0 | 状态 |
|---|---|
| develop force 未被挡 | ✅ 拆分为 develop-no-force（无 bypass），实测 422 |
| Release App 权限反了 | ✅ Contents R-only + PR R+W，实测建 PR 201 / push 403 |

剩余未实测项（#9 bot 审批、#10 坏 head 的 promotion-policy）：因 master-protection 已含 required_status_checks + require_code_owner_review 配置，且 #5/#6 证明 Release App 建/读 PR 通畅，可在 **V13-009** 合约 / **V13-012** E2E 中随真实 Promotion PR 一并验证。
