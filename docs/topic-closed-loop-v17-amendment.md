# Topic 闭环 V17 修正案（权威）

> **优先级：** 本文与 [topic-closed-loop-plan.md](topic-closed-loop-plan.md) **§0 KEEP** 冲突时，**以本文为准**。KEEP 中未点名的产品语义（ready ≠ enqueue ≠ merge ≠ deployed；不 ALTER `tasks`；不建 `topic_events` 事件源；Git 不进 `BEGIN`；UNIQUE 墓碑；无第三把 WT 文件锁；`merged` = 本地 `develop`）继续有效。  
> **状态：** Phase 0 契约冻结。实现顺序：本文合入 → V17 CLI → V18 schema 5 / 图 / Saga 列 → V19 runtime。禁止跳过本文直接编码 V18/V19。  
> **基线：** schema **4** 已落地（V14–V16）。头版本目标 **schema 5**。CLI 闭环命令已存在，不重做。

实现者：先读本文，再读 §0，再读 [`.cursor/plans` V17 方案](../../.cursor/plans/) 或仓库任务清单。

---

## 被取代的旧句

| 旧权威 | 本文 |
|---|---|
| schema 4 为闭环头；§0 P0-7「schema 4 列」 | 4 是已落地基线；头版本 **5** |
| `last_step` 足够 continue | 供给是 Saga：`provision_op_id` + `provision_phase` + 补偿（V18 落列） |
| `create_from_topic_ready` 可合成 `exit_code=0` | **attestation**；禁止伪造命令执行结果 |
| capability 默认 True / `required_pass` 一刀切 | 未知能力 = **false**；按是否发 HTTP 分 operation |
| 文档放到最后一刀 | **修正案即 Phase 0** |

---

## C1 身份图

`assert_topic_graph(conn, topic_id)`（V18）须核：同 `project_name`、canonical `worktree_path`、branch、enqueue 后 `agent_name`、task status、run state、`active_run_id` 双向、非终态 `agent_runs.topic_id` 必须等于指针。状态更新 **CAS + rowcount=1**。

| lifecycle | task_id | 允许的 tasks.status | 活 run |
|---|---|---|---|
| proposed | NULL | — | 无 |
| active | NULL | — | 非 `exited\|archived` |
| ready | NULL | — | 无或活 |
| enqueued | 非空 | `pending\|merging\|conflict\|recovery_required` | 无或活 |
| merged | 非空 | 仅 `merged` | 无 |
| rejected | NULL | 原 task 仅 `skipped` | 无 |
| cancelled | NULL | — | 无 |
| archived | NULL 或终态 task | 仅 `merged\|skipped`；禁止 pending/merging/conflict/recovery_required | 无 |

**裸 `agent-start`（`topic_id` 空）合法**，不是图冲突，不是 v5 迁移冲突。

写路径（均须图校验，V18）：start continue、ready、enqueue、abandon、archive、裸 enqueue、merge/finalize/recover、retry、skip、reset-stuck、cleanup --prune、agent-start/stop/archive/register/**reconcile**、takeover、release、fork / `topic-open --fork`。skip 必须摘活 run。

Git 已成功而 DB 图失败 → `recovery_required` / `manual_required`，kind `topic_graph_conflict`。enqueue 校验在同一 `BEGIN IMMEDIATE` 提交前。

---

## C1b `topic-start` Saga（V18 落列，V17 先冻 dest / preflight）

不新增 lifecycle 枚举、不建 `topic_events`。schema 5 增加 `provision_op_id`、`provision_phase ∈ {preflight,record,worktree,session,done,compensating,needs_recovery}`。

纯检查（coordinator、dest 占用、隔离、`--start-session` 的 agent + health gate）在 **第一次 INSERT 之前**。Continue 使用 **DB 已存 canonical path**，禁止按新 `--agent` 重算 dest。

补偿：record 失败可删无 WT 行；Git WT 已登记则不自动 `worktree remove`，标 `needs_recovery`，continue 禁止二次 `worktree add` 到已登记 dest。session 失败 evidence-end run，保留 WT。补偿失败 → `needs_recovery` + `doctor`。

---

## C2 schema 5（V18）

加性：`idx_topics_task_id` UNIQUE WHERE `task_id IS NOT NULL`；**删除** `idx_topics_active_task`。`agent_runs` / `coordinator_sessions` / `inspection_forks` 绑定列（generation、nonce、digest、version）。`is_v5_complete` 读 `sqlite_master.sql` 断言 UNIQUE 与 WHERE，不只查索引名。

`migrate_to_v5` 完整 v5 → `{from:5,to:5,action:noop}`。`migrate_to_v2/v3/v4` 在 v5 上同样 noop。dry-run 不写盘。诊断 **不报** 无 topic 的活 run。备份：WAL 下 `VACUUM INTO` 或 checkpoint 后 copy `db+wal+shm`。只读路径 `init=False`。

---

## C3 Runtime（V19）

复合身份 `{server_id, generation, nonce, capability_digest, version}`。`assert_runtime_gate(operation, expected) -> snapshot`。

- `agent-list` / `show` / `topic-list` / `show`：无 HTTP，无 gate。
- `agent-stop` 本地 worker：不把 `abort` 当前提。
- `agent-start` 无 prompt：不要 `prompt_async`。
- fork：POST 要 `session_fork_api`；仅 `--launch` 要 attach fork flag。

Probe 仅两档：health/read-only（默认，含 external）；full 需 `--probe-full` 且 managed 或显式授权。未知能力 = false。禁止同时持有 `project.lock` 与 `runtime.lock`。`runtime stop`：runtime.lock 置 `draining` → 释放 → 按项目名排序 fencing → 再 kill。

---

## C4 Verification 信任

Topic ready/enqueue 是 **Git attestation**，不是命令执行门禁。禁止合成 `exit_code=0`。results 为空或 `{trust_model: attestation, executed: false}`。记录 `verified_against_base`（ready 时 develop SHA）。enqueue 时当前 develop 必须等于该值，否则 `topic_base_drift`。promote/release 仍要真实 aggregate results。

---

## V17 CLI 合同（本文合入后即可 TDD）

- 声明式命令表生成 `PROJECT_COMMANDS` / parser 名 / help；**`doctor` 必须在表内**。
- `lock_for(args)`：`--fork` / `--prune` 拿 `project.lock`；只读无锁。
- `resolve_topic_ref`：trim、拒控制字符、project 内字节匹配、id 先于 name、禁止模糊；返回 `{input_ref, resolved_by, canonical_id}`；写命令持锁后解析。
- metavar `TOPIC_ID_OR_NAME`。`--agent` 不宣称启 session。`--brief-file` 相对 **project root**，存 canonical 路径 + digest。
- 禁止同时双锁（实现时遵守；V17 测试锁表即可）。
