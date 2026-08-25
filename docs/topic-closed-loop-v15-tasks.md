# Topic 闭环整改任务（V15）

> **起因：** V14 实现了 start → ready → enqueue → merge 的代码路径，但独立审查发现产品路径（CLI argv、无 runtime）走不通，且非 happy path 会永久冻结 Topic。  
> **规格权威仍是** [`topic-closed-loop-plan.md`](topic-closed-loop-plan.md) **§0**。本清单只修 V14 相对 §0 的缺口。  
> **方法：** 先写穿过 `orch <project> …` argv 的失败测试，再写最少实现。禁止再只测 `topic_start(..., provision_session=False)` 这条 CLI 到不了的组合。

## 禁止项（沿用 V14）

- ALTER `tasks`；建 `topic_events`；Git 进 `BEGIN`；物理锁 worktree
- `topic-ready` 入队或合入
- 假 SHA；断言返回字典字面量而不查 DB
- `--agent` 暗含必须有 runtime session

## 依赖

```text
V15-001 CLI 产品路径（--agent 与 session 解耦）
  → V15-002 enqueue 绑定 verification SHA
  → V15-003 recover-as-merged 回写 Topic
  → V15-004 冻结谓词 + ready lifecycle 白名单
  → V15-005 禁止假 evidence-end
  → V15-006 测试加固 + Skill / architecture
```

---

## V15-001：CLI 产品路径

- **文件：** `orch/cli.py`；`orch/commands/topic.py`；`tests/test_topic_cli_loop.py`
- **做：**
  - `--agent` 只写 `topics.agent_name` 和 dest 前缀，**不** `provision_session`
  - 显式 `--start-session` 才拉 OpenCode；无 runtime 时 `topic-start --agent coder` 必须成功并保持 `proposed`
  - continue 时若行上 `agent_name` 为空且本次传了 `--agent`，补写 `agent_name`（不改已有 worktree_path）
- **完成定义：** 无 runtime 下 argv：`coordinator-bind` → `topic-start --agent` → commit → `topic-ready` → `topic-enqueue` → `merge --once`；DB 中 `tasks` 有一行且 `topics.lifecycle_state='merged'`。`SELECT COUNT(*) FROM tasks` 在 ready 之后、enqueue 之前为 0。

## V15-002：冻结 SHA = verification SHA

- **文件：** `orch/commands/topic.py`；`orch/commands/enqueue.py`
- **做：** `topic-enqueue` 读取 `verification_record_id`；`source_commit` 必须等于该记录的 `commit_sha`，且等于当前 branch tip。mismatch → 稳定 `kind`（`topic_verification_sha_mismatch`），要求先再 `topic-ready`。
- **完成定义：** ready 后另提交再 enqueue 失败；不入队。对齐后再 enqueue，`tasks.source_commit` 等于 verification SHA。

## V15-003：recover-as-merged 回写

- **文件：** `orch/merge/recover.py`（V14-006 漏了它）
- **做：** recover-as-merged 与 `finalize_success` 同一条 Topic 回写：`lifecycle_state='merged'`，检查 rowcount。`recovery_required` 本身不把 Topic 标 merged。
- **完成定义：** 入队后把 task 置 `recovery_required`、手工把 source 合进 develop、`reset-stuck` → topic `merged`。

## V15-004：冻结与终态

- **文件：** `orch/commands/topic.py`；`orch/runtime/lifecycle.py`
- **做：**
  - `topic-ready` 冻结仅当 `enqueued` **且** `tasks.status ∈ {pending, merging}`
  - `conflict` / `recovery_required` 允许同 WT 再 `topic-ready`（更新证据）；lifecycle 保持 `enqueued`
  - `topic-ready` 拒绝 `cancelled|archived|merged|rejected`（不可复活）
  - `agent-start` 的 WT 比较走 `normalize_path`
- **完成定义：** pending 时新 SHA ready → `topic_sha_frozen`；conflict 时新 SHA ready 成功；abandon 后再 ready → 稳定 kind。

## V15-005：禁止假 evidence-end

- **文件：** `orch/commands/topic.py`
- **做：** 删除把 `starting|lost|manual_required` 直接 UPDATE 成 `exited` 的路径。占用 unique 的 run 仍在时 continue 拒绝（`topic_run_active`），由 `agent-stop` / `agent-reconcile` 做真 evidence-end。
- **完成定义：** 插入 `starting` run 后 `topic-start` continue 不得把它标 `exited`，也不得再 INSERT 第二条 run。

## V15-006：测试与文档

- **文件：** `tests/test_topic_closed_loop.py`；`tests/test_skill_consistency.py`；`skills/orchestrator/SKILL.md`；`.opencode/skills/orchestrator/SKILL.md`；`docs/current-architecture.md`
- **做：**
  - 身份链断言 `len(runs) >= 1` 或显式「无 session 则 runs 为空」；abandon / ready 查 DB 列
  - Skill：`enqueue` 遇到活 Topic → `topic_enqueue_required`；`--agent` 不启 session；`merged` = 本地 develop；冻结/retry；`topic-enqueue` / `topic-abandon` 进 `REQUIRED_SNIPPETS`
  - architecture：schema **4**；§13 不再把本闭环写成「下一阶段 P0」
