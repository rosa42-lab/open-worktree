# Topic 闭环开发任务（V14）

> **规格权威：** [`topic-closed-loop-plan.md`](topic-closed-loop-plan.md) **§0** 与 [`topic-closed-loop-review.md`](topic-closed-loop-review.md)。  
> 与计划 §4.4 / §8 旧草稿冲突时，以 §0 与本清单为准。  
> 基线代码：`orch` 1.3.0（schema 3）。不 ALTER `tasks`。不建 `topic_events`。  
> V14 落地后的缺口（CLI `--agent` 绑死 session、recover 不回写等）见 **[`topic-closed-loop-v15-tasks.md`](topic-closed-loop-v15-tasks.md)**。

历史 V12-015 只落地了 Topic **记录层**（贴标签），不覆盖本闭环。本清单是下一刀的可执行任务。

## 禁止项

- ALTER `tasks` 表列
- `CREATE TABLE topic_events`
- Git 放进 SQLite 写事务
- 把裸 `worktree-add` 树升级成 Topic 供给
- `provision_session=False` 却把 `lifecycle_state` 标 `active`
- 物理锁 worktree（conflict 时 `orch retry` 必须能在同 WT 提交）
- `topic-ready` 入队或合入
- 第三把 worktree 文件锁
- 用 `path.split("/")` 含 `main` 做隔离断言
- `tests.helpers.git_fixture.run(...).stdout`（今日 `run()` 返回 `None`）
- 假 SHA `"abc"` 冒充 Git 门禁已绿

## 命名（跟现码，不跟译稿）

| 概念 | 代码 |
|---|---|
| 包 / CLI | `orch` |
| 锁 | `project.lock`（`orch.constants.project_lock_path`） |
| Topic 状态列 | `topics.lifecycle_state` |
| 成果列 | `topics.result_state`（v1 不实现微状态机） |
| Run 表 | `agent_runs` |
| 协调会话 | `coordinator_sessions` |
| canonical id | `topics.id` = `topic_` + 16 hex |
| 路径正规化 | `orch.validate.normalize_path` |

## 依赖

```text
V14-001 schema 4
   → V14-002 unlocked 内核
   → V14-003 topic-start
   → V14-004 topic-ready
   → V14-005 + V14-006 必须同发（冻结与 merge 回写）
   → V14-007 topic-abandon（可与 005/006 同发）
   → V14-008 文档
```

---

## V14-001：schema 4

- **状态：** 已实现（schema 4）；产品路径缺口见 V15
- **文件：** `orch/migrations.py`；`orch/db.py`（文档字符串）；`tests/test_migrations_v14.py`；`tests/test_migrations_v13.py`；`tests/test_migrations_v12.py`
- **做：**
  - `SCHEMA_V4 = 4`，`SCHEMA_VERSION = 4`
  - `is_v3_complete`：`user_version >= 3`（今日是 `== 3`）
  - `classify_db` 增加 `v4`；`ver > 4` 才 unsupported
  - `migrate_to_v2` / `migrate_to_v3` 视 v4 为已迁
  - `migrate_to_v4`：`ALTER` 前查列是否已在；二次调用 noop
  - `ensure_schema` → `migrate_to_v4`
  - `topics` 加：`agent_name`、`task_id`、`last_step`、`last_error`、`base_commit`、`verification_record_id`
  - `agent_runs` 加：`topic_id`（`task_id` 列已存在，只填值不 ALTER）
  - 索引：`idx_agent_runs_active_topic`（`state NOT IN ('exited','archived')`）、`idx_topics_active_task`（`lifecycle_state='enqueued'`）
- **不做：** ALTER `tasks`；`topic_events` 表
- **完成定义：** 空库 `user_version=4`；v3 库可升；二次 noop；`SELECT * FROM tasks` 行不变；直接 `migrate_to_v3` 仍可停在 3

## V14-002：抽出 `*_unlocked`

- **依赖：** V14-001
- **文件：** `orch/commands/worktree_add.py`；`orch/runtime/lifecycle.py`；`orch/commands/enqueue.py`
- **做：** `worktree_add_unlocked(..., base_sha)`；`start_unlocked(..., topic_id=None)`；`enqueue_unlocked(..., topic_id=None)`。公开 CLI 先持 `project.lock` 再调。unlocked 内部不得再 `acquire`。Git 不在 `BEGIN` 内。
- **完成定义：** 现有 `worktree-add` / `agent-start` / `enqueue` 行为不变；可在已持锁时调用

## V14-003：`topic-start` 供给隔离

- **依赖：** V14-002
- **文件：** `orch/commands/topic.py`；`orch/cli.py`；`tests/test_topic_closed_loop.py`；`tests/test_phase4.py`；`tests/helpers/git_fixture.py`
- **做：** dest = `worktrees/<agent>-<safe-branch>`；钉住 develop SHA 再建 branch；硬拒 `--branch develop` 与 dest==`root/main`（`Path.resolve()`）。占用三分：无行+已注册 WT → 失败不 INSERT；磁盘有但未注册 → recovery；continue 仅当行已在且 list 与 path/branch 一致。lost 占 unique 时先 evidence-end。无 session 不得标 `active`。`--agent` 可选；`--worktree` 弃用一周期，语义仍禁止 annotate。
- **同切片：** `git_fixture.run` 返回 `CompletedProcess`；改 `TopicTests` 不再传任意 `worktree_path`
- **完成定义：** 隔离失败不留下 proposed 成功态；重复 start 同一 `topic_id`；路径测 `E:\` vs `E:/` vs `e:\`

## V14-004：`topic-ready` Git 证据

- **依赖：** V14-003
- **文件：** `orch/commands/topic.py`；`tests/test_topic_closed_loop.py`；`tests/test_verification_v13.py`
- **做：** `git worktree list`、HEAD 分支、porcelain 空、`--commit==HEAD`、`develop..HEAD` 非空。不入队。默认不跑 commands。成功 UPDATE `topics.verification_record_id`。`enqueued`+`pending|merging` → `topic_sha_frozen`。
- **完成定义：** JSON `enqueued: false`；假 SHA 不得 ready；同 Phase 改掉 `commit_sha="abc"` 测试

## V14-005：`topic-enqueue` + 应用层冻结

- **依赖：** V14-004
- **必须与 V14-006 同发**
- **文件：** `orch/commands/topic.py` 或 `topic_enqueue.py`；`orch/commands/enqueue.py`；`orch/cli.py`
- **做：** 复用五项校验；同事务写 `topics.task_id` + `agent_runs.task_id` + `agent_runs.topic_id`。裸 enqueue 与活 topic 同 branch/path → 拒绝或同 txn 附着。冻结查 **WT 占用 + `lifecycle_state=enqueued`**。不调 release freeze precheck。conflict 允许同 WT 写者；仅 `orch retry` 改 `source_commit`。
- **完成定义：** lookup 断言三列；`master_release` 下 enqueue 仍成功；worker 退出后 unique 释放但应用层仍冻

## V14-006：merge / skip / retry 回写

- **依赖：** V14-005（同发）
- **文件：** `orch/merge/finalize.py`；`orch/merge/interrupt.py`；`orch/commands/skip.py`；`orch/commands/retry.py`
- **做：** `finalize_success` 同一短事务 `UPDATE topics SET lifecycle_state='merged' WHERE task_id=? AND lifecycle_state='enqueued'`，检查 `rowcount`。skip → `rejected`；之后裸 enqueue 不得仍指向 skipped task。retry 后 topic 保持 `enqueued`，verification stale。
- **完成定义：** Git 仍在 finalize 事务外；recovery_required 不把 topic 标 merged

## V14-007：`topic-abandon`

- **依赖：** V14-001；建议与 005/006 同发
- **文件：** `orch/commands/topic.py`；`orch/cli.py`
- **做：** `proposed|active|ready` → `cancelled`。`topic-archive` 不得代替放弃。`enqueued` 且 task 活跃时拒绝 archive。
- **完成定义：** 广告状态机里的 `cancelled` 有命令可达

## V14-008：文档与 Skill

- **依赖：** V14-005、V14-006、V14-007
- **文件：** `docs/current-architecture.md` §8 / §13；`skills/orchestrator/SKILL.md`；`.opencode/skills/orchestrator/SKILL.md`
- **做：** Topic 升为供给闭环；ready ≠ enqueue ≠ merge ≠ deployed；身份是关联图；schema 4 列与冻结规则。
- **完成定义：** Skill 写明 `topic-ready` 不入队；架构文档不再说 start 只贴标签

## 验收命令

```text
python -m unittest tests.test_migrations_v14 tests.test_migrations_v13 tests.test_migrations_v12 -v
python -m unittest tests.test_topic_closed_loop tests.test_phase4 tests.test_verification_v13 -v
python -m unittest discover -s tests -v
```
