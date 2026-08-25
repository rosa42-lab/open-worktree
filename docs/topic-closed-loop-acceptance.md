# Topic 闭环验收方案（给 OpenCode 执行）

> **范围：** V14 供给闭环 + V15 产品路径整改。规格权威是 [`topic-closed-loop-plan.md`](topic-closed-loop-plan.md) **§0**。整改任务见 [`topic-closed-loop-v15-tasks.md`](topic-closed-loop-v15-tasks.md)。  
> **基线：** `orch` 1.3.0 + schema 4。目标集成分支永远是 `develop`。  
> **给执行者：** 每条命令加 `--json`。成功看 `ok=true` 与 `data`；失败看 `error.kind`（不要只看 stderr 文案）。不要手改 SQLite / 锁 / `main/` / `develop`。

## 0. 验收结论怎么判

本轮 **通过** 当且仅当：

1. 主路径能从 `topic-start` 走到 `merge` 成功，且 Topic `lifecycle_state=merged`。
2. `topic-ready` 的 JSON 里 **`enqueued` 必须是 `false`**，且 `pending` 在 ready 之后、enqueue 之前为空（或没有该 branch）。
3. 负向用例的 `error.kind` 与下表一致（不是「随便失败」）。
4. 合入后 **不得** 宣称 deployed / origin/develop / release 已完成。

本轮 **不测：** GitLab、`candidate_pr`、`--execute-commands` 真跑测试、自动 `ship`、物理锁 worktree、把 `/goal` 当成 Topic。

---

## 1. 环境与命名

把 `<PROJECT>` 换成已 `orch project add` 且 `init` 过的项目名。下文用例用：

| 符号 | 建议值 | 说明 |
|---|---|---|
| `<PROJECT>` | 现有项目名 | 必须已有 `.bare.git/` 与 `main/` |
| `<NAME>` | `accept-auth` | Topic `name`，同项目 UNIQUE（含 archived） |
| `<BRANCH>` | `feat/accept-auth` | 不得是 `develop` |
| `<AGENT>` | `coder` | dest 公式要用 |
| dest | `worktrees/coder-feat__accept-auth` | `/` → `__`；无 `--agent` 时前缀是 `topic` |

Coordinator 目录必须是 **项目根**（含 `.bare.git/` 的那层），不是 `main/`，也不是某个 worktree。

绑定用的 `--session` 可以是任意非空字符串（无 runtime 时不必是真实 OpenCode session）。有 runtime 时再用 Server 里真实 session。

```text
orch runtime status --json          # 可选；主路径可不启动 runtime
orch project list --json
orch <PROJECT> coordinator-bind --session ses_accept --directory <项目根> --json
orch <PROJECT> coordinator-show --json
```

`coordinator-bind` 若已有 active 绑定，用 `--replace`。

---

## 2. 主路径（必须做）

### TC-01 供给隔离（无 session）

**目的：** `topic-start --agent` 建 branch+WT 并写入 `agent_name`；**不要** 传 `--start-session`，Topic 保持 `proposed`（无 runtime 也能走完全程）。

```text
orch <PROJECT> topic-start accept-auth --title "Accept auth" --goal "prove closed loop" --branch feat/accept-auth --agent coder --json
```

本步 **不要** 传 `--worktree`。

**期望 `data`：**

- `topic.id` 形如 `topic_` + 16 hex
- `topic.lifecycle_state` = `proposed`（**不是** `active`）
- `topic.worktree_path` resolve 后等于 `<root>/worktrees/coder-feat__accept-auth`
- 该路径是目录，且 **不等于** `<root>/main`
- `topic.base_commit` 非空（钉住当时 develop SHA）
- `created_worktree` = true（首次）

**立即再跑同一命令（continue）：**

- 同一 `topic.id`
- 不新建第二条 Topic
- 不把已有裸 `worktree-add` 树收养进来（见 TC-11）

记录：`TOPIC_ID`、`WT`、`BASE`。

### TC-02 开发提交（只在 WT 里）

在 `WT` 里改文件、commit。**禁止** 在 `main/` 提交。

```text
# 在 WT 内
git status
git add ...
git commit -m "accept: closed-loop proof"
git rev-parse HEAD
git rev-parse --abbrev-ref HEAD          # 必须是 feat/accept-auth
git status --porcelain                   # 必须空
```

记录：`HEAD`（完整 SHA）。`develop..HEAD` 必须 ≥ 1 个 commit。

### TC-03 `topic-ready` 不入队

```text
orch <PROJECT> topic-ready <TOPIC_ID> --commit <HEAD> --command pytest --json
orch <PROJECT> pending --json
orch <PROJECT> topic-show <TOPIC_ID> --json
```

**期望：**

- `ok=true`，`data.enqueued` = **false**
- `data.lifecycle_state` = `ready`
- `data.result_state` = `ready_for_enqueue`
- `data.verification.commit_sha` == `HEAD`
- `data.verification_record_id` 以 `verify_` 开头
- `pending` **没有** 该 branch 的新 task

假 SHA 或脏树不得 ready（TC-12 / TC-13）。

### TC-03b ready 后再提交不得入队

在 WT 里再 commit 一次（不要再 `topic-ready`），然后：

```text
orch <PROJECT> topic-enqueue <TOPIC_ID> --json
```

**期望：** `ok=false`，`error.kind` = `topic_verification_sha_mismatch`，`pending` 仍为空。先再 `topic-ready --commit <新HEAD>` 才能入队。本条测完后用新 HEAD 继续 TC-04，或另开 Topic。

### TC-04 `topic-enqueue` 身份链

```text
orch <PROJECT> topic-enqueue <TOPIC_ID> --json
orch <PROJECT> pending --json
orch <PROJECT> topic-show <TOPIC_ID> --json
```

**期望：**

- `data.enqueued` = true，给出 `task_id`（32 hex，无 `topic_` 前缀）
- `data.source_commit` == `HEAD`
- Topic `lifecycle_state` = `enqueued`，`topic.task_id` == 该 `task_id`
- `pending` 能看到该 `branch`

**身份图（用 sqlite 或 `topic-show` 交叉核对）：**

- canonical 只是 `topics.id`
- `task_id` / `session_id` / 路径 **不是** `topic_id`
- 若没有 agent run：`agent_runs` 相关列可空；lookup 以 `topics.task_id` 为准
- 若有 active run：同事务应有 `agent_runs.task_id` 与 `agent_runs.topic_id`

再执行一次 `topic-enqueue`：应幂等（同一 `task_id`，`idempotent=true` 或等价「仍是那条 pending/conflict 任务」）。

### TC-05 裸 `enqueue` 被拒

```text
orch <PROJECT> enqueue coder feat/accept-auth <WT> --json
```

**期望：** `ok=false`，`error.kind` = `topic_enqueue_required`。

### TC-06 冻结：ready 不能换 SHA

入队后再 commit 一次（或仍用旧 SHA）：

```text
orch <PROJECT> topic-ready <TOPIC_ID> --commit <任意SHA> --command pytest --json
```

**期望：** `error.kind` = `topic_sha_frozen`。Topic 仍是 `enqueued`。

### TC-07 merge 回写

```text
orch <PROJECT> merge --once --json
orch <PROJECT> topic-show <TOPIC_ID> --json
```

**期望：**

- 任务 `status=merged`（若无冲突）
- Topic `lifecycle_state=merged`
- **不要** 跑 `promote-develop --execute`；merged ≠ deployed

若出现 `conflict`：不要当失败结束。见 TC-20（可选）。本主路径优先选无冲突的小改动。

---

## 3. 负向 / 隔离用例（必须做）

每条独立准备数据；不要复用已 `merged` 的 `accept-auth` 名（name UNIQUE 含 archived）。换 `name` / `branch`。

| ID | 操作 | 期望 `error.kind` |
|---|---|---|
| TC-10 | `topic-start ... --branch develop` | `topic_isolation_required` |
| TC-11 | 先 `worktree-add coder feat/occupied`，再 `topic-start` 同 agent+branch | `topic_dest_occupied`；原 WT 仍在；不得 INSERT 成功 Topic |
| TC-12 | ready 传假 SHA（如 `abc123`） | `topic_verification_sha_mismatch` |
| TC-13 | WT 有未提交文件时 ready | `topic_worktree_dirty` |
| TC-14 | 刚 `topic-start`、相对 develop 0 commit 就 ready（`--commit` 用当前 HEAD） | `topic_empty_diff` |
| TC-15 | 无 `coordinator-bind` 时 `topic-start` | `coordinator_missing` |
| TC-16 | `topic-start` 带与公式不符的 `--worktree` | `topic_annotate_forbidden` |
| TC-17 | `proposed` Topic 直接 `topic-enqueue` | `topic_not_ready` |
| TC-18 | `topic-abandon` 一个 `ready` Topic，再 enqueue | abandon 后 `lifecycle_state=cancelled`；enqueue 失败（`topic_not_ready` 或等价） |
| TC-19 | `enqueued` 且 task 为 `pending` 时 `topic-archive` | `topic_enqueue_active` |

### TC-21 skip 后裂脑不得残留

对一条 **新的** ready Topic：`topic-enqueue` → `skip <task_id> --reason accept-drop`。

**期望：**

- Topic `lifecycle_state=rejected`
- `topics.task_id` 为 NULL（不再指向 skipped task）
- 此后对该 WT **允许** 裸 `enqueue`（活 Topic 检查排除 `rejected`）

---

## 4. 可选：OpenCode runtime（有 Server 再做）

前置：`orch runtime start --json`，`runtime status` 健康。

### TC-30 start 带 `--agent` 才允许 `active`

```text
orch <PROJECT> topic-start accept-rt --title "rt" --goal "session" --branch feat/accept-rt --agent coder --json
```

**期望：** 成功创建 session/run 时 `lifecycle_state=active`；session 失败不得谎称 `active`。

### TC-31 入队后 `agent-start` 冻结

该 Topic `topic-enqueue` 且 task=`pending` 时：

```text
orch <PROJECT> agent-start coder feat/accept-rt <WT> --json
```

**期望：** `error.kind` = `topic_sha_frozen`。

`conflict` 时 **允许** 同 WT 提交；只有 `orch retry <task_id>` 能改 `source_commit`。Topic 保持 `enqueued`。不要用文件锁锁死 WT。

---

## 5. 明确失败（出现即本轮不通过）

- `topic-ready` 返回 `enqueued=true` 或插入了 `tasks`
- Agent / Topic 命令去 `main/` 提交或直接改 `develop`
- 把裸 `worktree-add` 目录升级成 Topic 供给
- `--worktree` 指向已有路径并成功 annotate
- 无 session 却 `lifecycle_state=active`
- skip 后 Topic 仍 `enqueued` 且 `task_id` 指向 skipped 行
- 验收报告把 `merged` 写成已部署 / 已 promote

---

## 6. 机器核对（可选）

项目库：`%USERPROFILE%\.orchestrator\data\<PROJECT>\orchestrator.db`

```sql
PRAGMA user_version;                    -- 应为 4
SELECT id, lifecycle_state, result_state, task_id, agent_name, worktree_path, verification_record_id
FROM topics WHERE name = 'accept-auth';
SELECT id, status, source_commit, branch_name FROM tasks WHERE id = '<task_id>';
SELECT id, topic_id, task_id, state FROM agent_runs WHERE topic_id = '<TOPIC_ID>';
```

`user_version=4`。不要 `ALTER tasks`。不要存在 `topic_events` 表。

回归单元测试（不代替本手册，可作第一刀）：

```text
python -m unittest tests.test_topic_closed_loop tests.test_phase4 tests.test_verification_v13 -v
```

---

## 7. 给 OpenCode 的启动提示（可整段粘贴）

```text
你在仓库 open-worktree 里做 Topic 闭环人工验收。只读 docs/topic-closed-loop-acceptance.md 并按其中 TC-01…TC-07、TC-10…TC-19、TC-21 执行。

规则：
- 所有 orch 命令加 --json。断言 ok 与 error.kind，不要只看人话。
- 规格：docs/topic-closed-loop-plan.md §0。ready ≠ enqueue ≠ merge ≠ deployed。
- 不要手改 SQLite、锁、main/、develop。不要 promote-develop --execute。
- topic-start 不要传 --worktree。dest = worktrees/<agent>-<branch 的 / 换成 __>。
- 无 --agent 时 lifecycle_state 必须是 proposed，禁止 active。
- topic-ready 后 pending 不得出现该任务；JSON enqueued 必须 false。
- 每个失败用例记录：命令、JSON error.kind、是否符合手册。
- 最后交一份验收报告：通过/失败表 + 实际 TOPIC_ID/HEAD/task_id + 任何偏差。

先问我 <PROJECT> 名和项目根路径；没有就 orch project list --json。
```
