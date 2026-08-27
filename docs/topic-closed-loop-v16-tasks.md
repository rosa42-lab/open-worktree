# Topic 闭环整改任务（V16）

> **起因：** V15 打通 CLI argv 闭环后，审查留下三处出口：Windows 路径变体、`topics.active_run_id` 在 run 结束后仍指向死指针、`cleanup --prune` 看不见 Topic。  
> **规格权威仍是** [`topic-closed-loop-plan.md`](topic-closed-loop-plan.md) **§0**。不重开 enqueue/ready。  
> **方法：** 先写失败测试，再写最少实现。顺序：001 → 002 → 003（001 的 canonicalize 被 003 复用）。

## 禁止项

- ALTER `tasks`；建 `topic_events`；Git 进 `BEGIN`；物理锁 worktree
- 改 UNIQUE 墓碑语义（含 archived 仍占用 name/branch/path）
- 把 `idx_agent_runs_active_worktree` 改成大小写不敏感（SQLite 做不到 NTFS 语义）
- prune 未入队的 abandoned worktree；`topic-abandon` 后的 Git 回收

## 依赖

```text
V16-001 canonical_worktree_path + 变体测试
  → V16-002 退出/归档清空 active_run_id
  → V16-003 prune 看见 Topic + 文档
```

---

## V16-001：路径 canonicalize

- **文件：** `orch/validate.py`；写入与比较落点（topic-start、enqueue、start_unlocked、冻结查询、cleanup_guard）；`tests/test_validate.py`；`tests/test_topic_closed_loop.py`
- **做：** `canonical_worktree_path` 薄封装 `str(normalize_path(...))`。所有入库和等值比较走它。不存在的路径不抛；盘符大小写仅 `win32` 测。
- **完成定义：** 同一已存在目录的 `str(p)` / posix / 尾斜杠得到同一 canonical；冻结 lookup 对每一变体命中同一 `topic_id`。

## V16-002：清空 `topics.active_run_id`

- **文件：** `orch/runtime/lifecycle.py`（`clear_topic_active_run`）；`AgentLifecycleService._set_state`（`exited`）；`archive()`；`orch/runtime/takeover.py` 释放到 `exited`
- **做：** run 真正结束时 `UPDATE topics SET active_run_id = NULL WHERE active_run_id = ?`。**不要**在 `lost` / `stopping` 清。`topic-open` 指针失效走 directory locator。
- **完成定义：** 手工挂 `running` run 后 `agent-stop` → `active_run_id IS NULL`，`topic-open` 为 directory 模式；从 `exited` `agent-archive` 同样清空。

## V16-003：`cleanup --prune` 看见 Topic

- **文件：** `orch/commands/cleanup.py` `_prune_one`；`tests/test_topic_cli_loop.py`；`docs/current-architecture.md`；Skill
- **做：** Git 之前，活 Topic（`proposed|active|ready|enqueued`）→ `ok: False`, `reason`/`kind`: `topic_prune_blocked`，保留 worktree。Git gauntlet 不变。`tasks.archived_at` 的同一短事务把 `merged|rejected` Topic 标 `archived`（`last_step='prune'`）。不删 Topic 行。
- **完成定义：** argv start → ready → enqueue → merge → backdate → `cleanup --prune`：WT 消失、Topic `archived`。漏回写为 `enqueued` 时 prune 拒绝、WT 仍在。无 Topic 的既有 prune 用例保持绿。

---

## 后续：V17

权威修正案：[topic-closed-loop-v17-amendment.md](topic-closed-loop-v17-amendment.md)。先契约，再 CLI help / id-or-name / `doctor`，再 schema 5 与 runtime fencing。
