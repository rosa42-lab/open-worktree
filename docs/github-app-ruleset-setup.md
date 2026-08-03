# GitHub App + Ruleset 设置说明（develop → master 晋级保护）

> 依据：[`docs/remote-branch-promotion-design.md`](remote-branch-promotion-design.md) §5（角色与最小权限）、§6（远端分支保护策略）；落地任务见 [`docs/v1.3-tasks.md`](v1.3-tasks.md) V13-001（Phase 0 人工 Ruleset 落地）。
>
> 本文是 **GitHub 侧人工配置** 的操作说明；orch 侧的 `remote-config` / `remote-probe` 只读校验不依赖本文的 UI 步骤，但必须以本文配置后的真实行为作为 Phase 0 验证证据。

---

## 0. 目标架构

```
feature/* ──push──▶ 开发者 fork/分支
                        │
                   orch enqueue → merge（local develop）
                        │
  ┌─────────────────────┴──────────────────────┐
  │ Integration App：仅 fast-forward push       │
  │ origin/develop（Ruleset 只允许它更新）       │
  └─────────────────────┬──────────────────────┘
                        │
              Promotion PR（develop → master）
              Release App 创建/查询 + 读 checks
                        │
  ┌─────────────────────┴──────────────────────┐
  │ master Ruleset：PR + ≥1 人类审批 +          │
  │ promotion-policy check + **merge commit**   │
  └─────────────────────┬──────────────────────┘
                        │
                 release-sync 回纳 develop
```

| 身份 | 允许 | 明确禁止 |
|---|---|---|
| Agent / Developer | push feature 分支、读 PR/CI | push develop/master、批准自己的发布 |
| **Integration App**（orch-integration-app） | fetch；仅 fast-forward push `develop` | push master、force push、批准 PR |
| **Release App**（orch-release-app） | 创建/查询 develop→master PR、读 checks | 直接 push master、批准 PR |
| Release Approver（人类） | 审批 Promotion PR | 直接 push 核心分支 |

---

## 1. 前置条件

- 仓库或组织的 **Admin** 权限（创建 GitHub App、Ruleset）。
- 确定仓库真实分支名：`develop`、`master` 必须真实存在（不得用 default branch `main` 推断，见设计 D6/probe）。
- 计划采用的模式：**Integration App 直推 develop**（本文默认）。若 Phase 0 验证失败，降级为 candidate PR 模式（见 §3.4）。

---

## 2. 创建 GitHub App

路径：GitHub → 个人/组织 **Settings → Developer settings → GitHub Apps → New GitHub App**。

### 2.1 `orch-integration-app`（只写 develop）

| 权限项 | 值 | 说明 |
|---|---|---|
| Contents | **Read and write** | 推 develop 必需 |
| Metadata | Read-only | 强制默认 |
| Pull requests | Read-only | 可选，用于读取 PR 元数据 |
| Checks | Read-only | 可选 |
| Webhook | **不启用** | 本设计不依赖 webhook |

其他设置：
- **Install App**：安装到目标仓库（或组织内 "Only select repositories"），不要全组织默认安装。
- 私钥（Private key）下载后**加密保管**，禁止入库、禁止进 CLI argv（设计 §5 凭证规则）。
- App 名可自定义，但 Ruleset 的 bypass 列表与设计文档引用名保持一致（如 `orch-integration-app`）。

### 2.2 `orch-release-app`（建/查 PR、读 checks）

| 权限项 | 值 | 说明 |
|---|---|---|
| Contents | **Read-only** | 关键：无写权限 ⇒ 无法直推任何分支（含 master） |
| Pull requests | **Read and write** | 创建/查询 develop→master PR |
| Checks | **Read-only** | 读取 CI checks |
| Metadata | Read-only | 强制默认 |

**不得授予**：Contents write、Actions、Administration、Webhooks。

### 2.3 临时合并（两个 App 暂不细分时）

设计 §5 允许：暂时合并为一个 GitHub App（如只建 `orch-integration-app`，Release 职责复用同一 App）。合并 App 的权限为 2.1 的权限集（Contents write 用于推 develop），但**必须**遵守两条硬约束：

1. **不得直接 push master** —— 通过 §4 master Ruleset 实现：master 开启 `Require a pull request` + `Restrict updates`，且该 App **不在** master Ruleset 的 bypass 列表中；
2. **不得批准 PR** —— 通过 §4.2 的 "Code Owners 审批" 配置实现（bot 审批不计入 required approvals）。

> 临时合并只解决"权限细分"的部署成本，不改变流程语义；两个 App 就绪后应切回分离模式。

---

## 3. develop Ruleset（设计 §6.1）

路径：仓库 **Settings → Rules → Rulesets → New ruleset → Branch ruleset**。

| 设置 | 值 | 说明 |
|---|---|---|
| Ruleset name | `develop-protection` | |
| Enforcement status | Active | |
| Target branches | 选 **develop** | 精确匹配，不用通配符误伤其他分支 |
| **Block force pushes** | ✅ 开启 | |
| **Restrict deletions** | ✅ 开启 | |
| **Restrict updates** | ✅ 开启 | 只有 bypass 列表中的 actor 能 push develop |
| **Require signed commits** | 可选（设计 §6.1 可选） | 组织要求签名时开启 |
| **Bypass list** | 仅 `orch-integration-app` | 见下 |

**Bypass 配置要点**（Restrict updates 生效的前提）：

1. 打开 Ruleset → **Bypass list** → Add bypass → 搜索 `orch-integration-app` → 添加。
2. bypass 类型选择 **Exempt**（2025-09 新增类型；静默跳过规则，行为等同经典 branch protection 的 "Restrict who can push"，适合高频自动化 bot 直推）。
   - 若审计要求每次 bypass 都留痕，可选 "Always allow"（显式 bypass 并产生审计信号），但会增加噪音。
3. **不要**把任何个人（含管理员）加入 bypass：设计 §6.1 明确"禁止个人管理员默认 bypass"。管理员如需操作 develop，走 PR 或临时提权并留审计。

**规则效果**：任何人/App（除 Integration App）push `develop` 都会被拒绝（403）。

> **Phase 0 实测警告（2026-08-01）：** 若把 `Restrict updates` + `Block force pushes` + `Restrict deletions` 放在**同一** Ruleset，并对 Integration 使用 **Exempt**，则该 App 会连 force push / 删除一并绕过。  
> **正确拆分：**
> 1. `develop-updates`：仅 Restrict updates，bypass = Integration Exempt；  
> 2. `develop-no-force`：仅 Block force pushes + Restrict deletions，**bypass 为空**。  
> 详见 `docs/probe/v13-phase0-runtime-verify.md`。

### 3.4 降级路径（Phase 0 验证失败时）

若 Phase 0 无法证明"Integration App 只能更新 develop 且不能更新 master"，**禁止启用 Bot 直推**，改为（设计 §6.1 降级方案）：

```
local develop
  -> origin/orch-candidate/<promotion-id>
  -> candidate 到 develop 的受保护 PR   （GitHub 服务端校验来源与审批）
  -> origin/develop
  -> develop 到 master 的 Promotion PR
```

此时 develop Ruleset 增加：`Require a pull request`（≥1 人类审批）、candidate PR 合并必须 **merge commit**（禁止 squash/rebase）。

---

## 4. master Ruleset（设计 §6.2）

路径：仓库 **Settings → Rules → Rulesets → New ruleset → Branch ruleset**。

| GitHub 选项 | 设计值 | 说明 |
|---|---|---|
| Ruleset name | `master-protection` | |
| Target branches | 选 **master** | |
| **Require a pull request** | ✅ 开启 | 禁止直接更新 master（含所有 App） |
| Required approvals | **1**，来源选 **Code owners** | 见 4.1 |
| Dismiss stale approvals | ✅ 开启 | develop SHA 改变后必须重新审批 |
| Require conversation resolution | ✅ 开启 | 未解决评审意见不得发布 |
| Require status checks | ✅ 开启 | 必须含 `promotion-policy` + CI checks（见 §5） |
| Require branches up to date | ✅ 开启（稳定优先默认） | 发布前基于最新 master 重新验证；release 窗口内禁止 master 并发写入 |
| **Block force pushes** | ✅ 开启 | |
| **Restrict deletions** | ✅ 开启 | |
| **Linear history** | ❌ 关闭 | 保留 develop→master 的 merge commit |
| Bypass list | **最小化，不含任何 App** | Release App / 合并 App 均不在其中；管理员不默认 bypass |

### 4.1 审批必须来自人类（非 Bot）

- 创建 `.github/CODEOWNERS`，把审批团队写为 code owner：

  ```
  * @org/release-approvers
  ```

  其中 `release-approvers` 为人类团队（成员对仓库有 write 及以上权限）。
- Ruleset 中 **Required approvals 的来源选择 "Code owners"**：只有 code owner 的审批计数。
- 由于 Release App / 合并 App 不在 CODEOWNERS 中，**bot 的审批不计入**，从服务端机制上保证"生产发布审批不能由创建 PR 的 Bot 自我完成"（设计 §5）。
- 可选：开启 "Restrict who can dismiss reviews"，仅人类可 dismiss。

### 4.2 强制 merge commit（关键）

GitHub Ruleset **目前没有逐分支的 merge-method 规则**（截至 2025-12 无此能力），merge 方法由**仓库级**设置控制：

路径：仓库 **Settings → General → Pull Requests → Merge button**：

| 选项 | 值 |
|---|---|
| Allow merge commits | ✅ 开启 |
| Allow squash merging | ❌ 关闭 |
| Allow rebase merging | ❌ 关闭 |

> **为什么必须 merge commit**：release-sync 需要把 master 上的晋级 merge commit（保留 develop `source_sha` 作为父提交）fast-forward 回 origin/develop；squash/rebase 会制造无法自动同步回 develop 的新历史（设计 §6.2）。**不得把 squash/rebase 作为正常模式启用。**
>
> 该设置为仓库级：本仓库唯一的 PR 目标就是 master（develop→master Promotion PR），因此关闭 squash/rebase 不影响其他流程。若未来出现其他目标分支的 PR 需要 squash，需在设计中明确该变体（provider policy 变体，而非隐式改变默认安全模型）。

### 4.3 发布窗口行为

`Require branches up to date` 开启的代价：release 期间 master 若被并发写入，当前 release 进入 `blocked`，由 reconcile 判断已授权发布或未授权写入。**不要**勾选 GitHub 的自动 "Update branch" 功能把新 master 合入冻结的 develop（设计 §6.2）。

---

## 5. `promotion-policy` required check（设计 §6.3）

Ruleset 无法保证 PR 来源一定是 `develop`，需要 required check 校验来源策略。新增 `.github/workflows/promotion-policy.yml`：

```yaml
name: promotion-policy

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: read

jobs:
  source-policy:
    runs-on: ubuntu-latest
    steps:
      - name: Require develop to master in the same repository
        env:
          BASE_REF: ${{ github.event.pull_request.base.ref }}
          HEAD_REF: ${{ github.event.pull_request.head.ref }}
          BASE_REPO: ${{ github.event.pull_request.base.repo.full_name }}
          HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}
        run: |
          test "$BASE_REF" = "master"
          test "$HEAD_REF" = "develop"
          test "$BASE_REPO" = "$HEAD_REPO"
```

- 任何条件不满足 ⇒ check 失败 ⇒ PR 不可合并。
- 该 workflow 只读 GitHub event 元数据，不 checkout PR 分支代码、不使用 secret。
- 然后把 `promotion-policy` 加入 §4 master Ruleset 的 **Require status checks** 列表（可选：在 check 来源中锁定为 GitHub Actions 本身，避免被伪造状态）。

---

## 6. 本地 git 侧配置（orch 机器）

```powershell
# Windows: Git Credential Manager
git config --global credential.helper manager-core

# POSIX: libsecret
git config --global credential.helper libsecret
```

- App 的 installation token（1 小时有效）通过环境变量传给 orch，**禁止**出现在 CLI argv、SQLite、audit、JSON 输出或异常文本（设计 §5 凭证规则）。
- 直推示例（orch 内部使用，非手工操作）：

  ```powershell
  $env:GITHUB_TOKEN = "<installation-token>"   # 短期 token，不入库
  git push "https://x-access-token:$env:GITHUB_TOKEN@github.com/<owner>/<repo>.git" develop
  ```

- **禁止**任何 `--force` / `--force-with-lease` 语义：Ruleset 已把 force 视为违规，orch adapter 使用显式 expected-old/new SHA 的非强制 CAS fast-forward（设计 D3）。
- 本地合并保持 `--no-ff`（orch 的 `merge` 已固定 `--no-ff --no-edit`，无需额外配置）。

---

## 7. Phase 0 验证清单（V13-001）

配置完成后逐项验证，**全部通过才能启用 Bot 直推写路径**：

- [ ] `refs/heads/develop` 与 `refs/heads/master` 显式存在（`git ls-remote origin`）。
- [ ] Integration App 能 fast-forward push `develop`（成功）。
- [ ] Integration App push `master` 被拒（403 / ruleset 拒绝）。
- [ ] Integration App force push `develop` 被拒。
- [ ] Release App 能创建 `develop→master` PR、读取 checks。
- [ ] Release App push `master`（以及任何分支）被拒（Contents read-only）。
- [ ] bot（Release App）提交 approval 后，required approvals 仍未满足（Code owners 机制生效）。
- [ ] 人类审批通过 + 全部 required checks 通过后，PR 才可合并。
- [ ] PR 合并后 commit 为 **merge commit**，其父提交包含 develop `source_sha`（`git log --graph` / `gh pr view --json mergeCommit`）。
- [ ] `promotion-policy` check 对非 develop head（或 fork 来源）PR 失败。
- [ ] master Ruleset bypass 列表为空（或仅不含任何 App）。

可用命令抽查：

```bash
# 列出仓库 rulesets
gh api repos/{owner}/{repo}/rulesets --jq '.[] | {id, name, enforcement}'

# 查看指定 ruleset（含 bypass 列表、规则）
gh api repos/{owner}/{repo}/rulesets/{ruleset_id} --jq '{name, conditions, rules, bypass_actors}'

# 查看合并按钮设置（merge commit 唯一）
gh api repos/{owner}/{repo} --jq '{allow_merge_commit, allow_squash_merge, allow_rebase_merge}'
```

---

## 8. Solo Project 自锁场景与 OrganizationAdmin bypass（Phase 0 临时配置）

> **设计 §6.1 / §6.2 明文写"禁止个人管理员默认 bypass"。本节是 solo 项目 Phase 0 的必要例外，不是对设计的违反。**

### 问题：PR 作者不能 approve 自己

§6.2 要求 master PR 必须经 Code Owner 审批。Phase 0 只有一个人类（组织 owner Rosa42），同时也是 `CODEOWNERS` 唯一的 owner（`* @Rosa42`），创建 PR 时也是 author。

GitHub 硬规则：**任何人不能 approve 自己创建的 PR**（即便自己有 bypass）。结果 PR 永远 `BLOCKED — Review required`，无法合并。

这本身**是 §6.2 在生效**——bot 不能自审自批、PR 作者不能审自己。但 solo 项目没有第二个人类来打破循环。

### Phase 0 解决方案：OrganizationAdmin bypass

在 master-protection 的 bypass 列表加入：

```json
{
  "actor_id": null,
  "actor_type": "OrganizationAdmin",
  "bypass_mode": "always"
}
```

效果：
- 组织 owner 在 Ruleset UI 显示 `current_user_can_bypass: "always"`
- owner 可用 `gh pr merge <N> --admin` 或 Web UI 的 **Merge with admin privileges** 强制合并（绕过 approval 要求）
- 其他角色（普通成员、其他 App）**仍必须走 Code Owner 审批**

### 合并命令

```bash
# 标准合并（需要 approval，solo 项目下永远走不通）
gh pr merge <N> --merge

# 管理员强制合并（Phase 0 用这个）
gh pr merge <N> --merge --admin
```

### 团队化时怎么去掉

当团队至少有一个**非 PR 作者的 Code Owner** 时，应移除 OrganizationAdmin bypass：

1. 在组织内创建一个团队，例如 `rosa42-lab/release-approvers`，把**所有有权限审批的人**加进去
2. 把 `CODEOWNERS` 从 `* @Rosa42` 改为 `* @rosa42-lab/release-approvers`
3. 从 master-protection bypass 列表移除 OrganizationAdmin：

   ```bash
   gh api -X PUT repos/{owner}/{repo}/rulesets/{master_ruleset_id} \
     -f bypass_actors='[]'
   ```

4. 验证 `gh api .../rulesets/{id} --jq '.current_user_can_bypass'` 返回 `"never"`

此后再合并 Promotion PR 就要走真正的"非作者人类审批"流程，符合设计 §6.2。

### 为什么不放在 Phase 1 之后

V13-002 / V13-003 实现 `promotion-reconcile` / orch remote adapter 时，会大量创建 develop→master Promotion PR 测试 release-sync。如果 owner 不能合并这些测试 PR，Phase 1 / 1.5 都会被这条规则卡死。所以 bypass 必须在 Phase 0 就位。

---

## 9. 变更审计

- Ruleset 创建/修改均产生 GitHub **audit log** 条目（组织级 Audit log → Rules）。
- 个人管理员不默认 bypass（团队化后强制要求），任何规则变更都必须有明确操作者与理由。
- 若后续调整 `Require branches up to date` 等选项（如提高发布可用性），必须记录为 provider policy 变体，并重新走真实演练（设计 §6.2）。
