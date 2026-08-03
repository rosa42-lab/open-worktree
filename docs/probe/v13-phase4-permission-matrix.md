# orch v1.3 Phase 4 — Permission / Credential Matrix

**任务：** V13-012-2  
**日期：** 2026-08-01

| 检查 | 结果 | 证据 |
|---|---|---|
| Integration 不能 push master | **pass**（Phase 0） | `v13-phase0-runtime-verify.md` #3 |
| Integration 可 CAS/FF develop；不能 force | **pass** | Phase 0 + `develop-no-force` |
| Release Contents read-only；可建/读 PR | **pass** | Phase 0 #5/#6 |
| Agent/个人不能直推 develop/master | **pass** | Ruleset 422 |
| Bot 审批不计 code owner | **pass**（合约） | `test_get_reviews_bot_not_code_owner` |
| promotion-policy required check 形状 | **pass**（合约） | `test_bad_head_policy_shape`；真实拒合并可再 E2E |
| Token 不进异常/JSON details | **pass** | `TestHttpRedaction` |
| Config 拒绝 secret 键 | **pass** | `test_rejects_secret_keys` |
| Windows：PEM/token 仅环境变量 / 内存 | **pass** | `orch/remote/auth.py`；不写 SQLite |

**复测命令（有凭证时）：**

```powershell
$env:ORCH_GITHUB_TOKEN = "<installation-or-pat>"
orch <project> remote-probe --json
```
