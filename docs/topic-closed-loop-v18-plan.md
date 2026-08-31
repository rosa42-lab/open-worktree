# Topic 闭环 V18 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 schema 升到 5，落地身份图 CAS、Saga 列、ready 时钉 develop SHA，enqueue 校验 `topic_base_drift`，doctor 填充 conflicts/orphans/`provision_needs_recovery`。

**Architecture:** 加性迁移，不 ALTER `tasks`、不建 `topic_events`。`assert_topic_graph` 是应用层唯一图门；写路径 CAS `rowcount=1`。产品路径无 Server 仍可走完。V19/V20 不在本计划。

**Tech Stack:** Python 3 stdlib、SQLite、unittest。权威：[topic-closed-loop-v17-amendment.md](topic-closed-loop-v17-amendment.md) C1/C1b/C2/C4。

**Files:**
- Create: `tests/test_migrations_v18.py`, `tests/test_topic_graph_v18.py`, `orch/topic_graph.py`
- Modify: `orch/migrations.py`, `orch/db.py`, `orch/commands/topic.py`, `orch/commands/doctor.py`, `orch/commands/skip.py`, `orch/merge/finalize.py`, `tests/test_migrations_v14.py`, `tests/test_migrations_v12.py`, `tests/test_migrations_v13.py`, `tests/test_topic_cli_loop.py`, `docs/current-architecture.md`

---

### Task 1: schema 5 迁移（TDD）

- Test: `tests/test_migrations_v18.py`
- Modify: `orch/migrations.py`, `orch/db.py`

- [x] 空库 `ensure_schema` → `user_version=5`，`classify=v5`，`is_v4_complete` 仍真
- [x] `idx_topics_task_id` 的 `sqlite_master.sql` 含 UNIQUE 与 `task_id IS NOT NULL`；无 `idx_topics_active_task`
- [x] `migrate_to_v5` 二次 noop `{from:5,to:5,action:noop}`；`migrate_to_v2/v3/v4` 在 v5 上 noop 且 version 仍为 5
- [x] v4 → v5 保留 tasks 行；列含 `provision_op_id`/`provision_phase`/`verified_against_base` 与 binding 列
- [x] `dry_run=True` 不改 `user_version` 与 sqlite_master
- [x] 更新 v12/v13/v14 里 `ensure_schema` 期望的头版本

### Task 2: `assert_topic_graph` + enqueue/ready/skip/merge

- Test: `tests/test_topic_graph_v18.py`
- Create: `orch/topic_graph.py`

- [x] proposed 带 `task_id` → `topic_graph_conflict`
- [x] 裸 `agent-start`（`topic_id` 空的活 run）不是图冲突
- [x] `topic-ready` 写入 `verified_against_base` = 当时 develop SHA
- [x] enqueue 时 develop 已前进 → `topic_base_drift`
- [x] skip 清空 `active_run_id` 并摘 `agent_runs.topic_id`（非终态）
- [x] merge writeback 后图失败 → task `recovery_required`（CLI kind `merge_aborted_recovery_required`；内层 `topic_graph_conflict`；Git 已成功不回滚）
- [x] abandon 摘活 run 后图合法

### Task 3: Saga 列与 doctor

- [x] 新建 Topic：`provision_phase` 经 record → worktree → `done`；失败路径可标 `needs_recovery`
- [x] `doctor` 列出 `needs_recovery`；conflicts 来自图失败；orphans 不含「无 topic 的活 run」
- [x] CLI E2E：`test_topic_cli_loop` 原闭环仍绿 + 一条 drift

### Task 4: 文档与全量测试

- [x] architecture：schema **5**、§12.3 缺口、doctor 不再空列表
- [x] `python -m unittest discover -s tests -v`
