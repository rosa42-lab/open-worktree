# Topic 闭环方案：多维审查综合

> 输入：[`topic-closed-loop-plan.md`](topic-closed-loop-plan.md)、[`topic-industry-analysis.md`](topic-industry-analysis.md)、[`current-architecture.md`](current-architecture.md)。
> 各审查维度对 KEEP / P0 结论一致（含身份图 / schema 分类 / Windows 路径 / 测试夹具审查）。本文覆盖原方案冲突处；实现者以计划 **§0** 为准。
> 日期：2026-08-24。不实现运行时代码。

---

## KEEP（不得改）

- **ready ≠ enqueue ≠ merge ≠ deployed。** 两条命令：`topic-ready` 然后 `topic-enqueue`；合入只经 `orch merge`；`merged` 不是 Done / promote。
- **不抄** Worktrunk `wt merge`、Git Town `ship`、Maestro CI 绿自动合入、Gerrit `submitWholeTopic`、Agent 自 merge。
- **不 ALTER `tasks` 列**（保住 `is_v2_complete` 对 v1 列的精确匹配）。enqueue 后关联写在 `topics.task_id` + `agent_runs.task_id` + `agent_runs.topic_id`，不是一根 `topic_id` 贯穿所有实体。
- 抽出 `worktree_add_unlocked` / `start_unlocked` / `enqueue_unlocked`，避免嵌套 `project.lock` 死锁。
- `topic-start` **供给** branch + 专用 worktree 隔离，不是给已有目录贴标签。
- `topic-ready` 要 **Git 证据**（`git worktree list`、HEAD、干净、非空 `develop..HEAD`、`--commit`==HEAD）。声明成功不够。
- 显式 `topic-enqueue` **只喂现有 merge queue**，复用五项校验并冻结 `tasks.source_commit`。
- 硬拒 `branch=develop` 与 dest=`main/`。
- **双冻结：** active `master_release` 期间仍可 enqueue，claim 被挡。Topic 不得发明第三把冻结。
- **无第三把 worktree 文件锁。** 占用靠 SQLite UNIQUE + `project.lock`。
- **身份是关联图，不是一根字符串。** canonical 只是 `topics.id`（`topic_` + 16 hex）。`session_id` / `branch_name` / `worktree_path` / `task_id` 都不是 `topic_id`。

---

## P0 规格补丁（必须先改计划再实现）

正文与测试草稿会把错误编进 TDD。计划 **§0** 写成可执行规则；摘要：

1. **冻结 vs conflict/retry。** 冻结 = `tasks.status ∈ {pending, merging}` 时禁止第二 task、禁止新 SHA `topic-ready`。`conflict`（及 merge Git 不确定的 `recovery_required`）**允许同一 WT 写者**；只有 `orch retry` 可改 `source_commit`；topic 保持 `enqueued`；verification 作废；merge 只信 `tasks.source_commit`。禁止物理锁 WT，否则 retry 无法提交。
2. **Git 是见证，SQLite 是占用权威。** `UNIQUE(project_name, name|branch_name|worktree_path)` 含 archived 仍占用。`git worktree list` fail-closed 只证明 DB 路径仍被 Git 登记，**不得覆盖 UNIQUE**。禁止宣称 Git 为占用 SoT。
3. **topic-start dest 占用。** 无 topic 行且 dest 已是注册 WT → 失败、不 INSERT。dest 在磁盘存在但未注册 → recovery，不删、不收养。continue 仅当 topic 行已在 **且** list 与 path/branch 一致（`base_commit` 只作 provenance）。禁止把裸 `worktree-add` 树升级成 Topic 供给。
4. **SQLite 事务 vs Git。** record / `git worktree add` / agent-start 之间短 COMMIT。Git 永不进 `BEGIN`。continue 不得把不确定 Git 当成功（禁止 Git Town continue-as-success）。
5. **lost/active run continue。** lost 仍占 unique 时不能 INSERT 第二条 run。必须 evidence-end 后再新 run，或 `generation++`。`topic-start` 不是 takeover，不签发 human lease。
6. **裸 enqueue vs Topic。** 与活 topic 同 branch/path → 拒绝，或在同一事务附着。禁止裂脑（队列已有 task、topic 仍 `ready`）。
7. **schema 4 列。** 不 ALTER `tasks`。最小列：`agent_name`、`task_id`，可选 `last_step` / `last_error`。能砍则砍满表 `topic_events` 与作权威的 `base_commit`。分类函数见 P0-12，不能只改 `is_v3_complete`。
8. **`provision_session=False`。** 不得把 `lifecycle_state` 标 `active`。公开 CLI：session 可选 **或** 必选但测试不许撒谎。推荐：start **必须** 建 WT；`--agent` 可选；`--worktree` 保留一周期弃用。
9. **abandon/cancel。** 要么加 `topic-abandon`（`proposed|active|ready` → `cancelled`），要么从广告状态机去掉 `cancelled`。`topic-archive` 不得代替放弃。
10. **冻结与 merge 回写同一切片。** enqueue 一旦冻写者，`merge` / `skip` / recover-as-merged 必须在同一发布回写 lifecycle，否则 WT 永久冻结。
11. **身份是关联图。** canonical id = `topics.id`（`topic_` + 16 hex）。其余实体自带前缀：`coord_` 16hex、`run_` 32hex、`tasks.id` 无前缀 32hex、`verify_` 16hex、OpenCode session 不透明、worktree = 文件系统路径。禁止把 `session_id` / `branch_name` / `worktree_path` / `task_id` 当成 `topic_id`。原方案不变量 #12「一个 `topic_id` 贯穿 …」作废。
12. **schema 4 必须改 classify + v13 测试，不只 `is_v3_complete`。** 今日 `is_v3_complete` 是 `user_version == 3`。`classify_db` 必须有 `v4` 分支，否则 version 4 会变成 ambiguous/unsupported。`migrate_to_v2` / `migrate_to_v3` 视 v4 为已迁。`tests/test_migrations_v13.py` 的 `test_empty_db_inits_schema_3` 断言 `user_version == SCHEMA_V3`，空库 init 到 4 会红——**与 schema 4 同切片改断言**。
13. **unique run 索引 ≠ 冻结，且看不见无 `topic_id` 的 `agent-start`。** `lost` / `starting` / `manual_required` 与 running 一样占用 `idx_agent_runs_active_topic`。session 失败后的 continue 必须先 evidence-end 旧 run，再新 run 或 `generation++`。不带 `topic_id` 的 `agent-start` 绕过该 unique；enqueue 后的冻结必须查 **worktree 占用 + `lifecycle_state=enqueued`**，不能只查 `topic_id`。worker 退出后 unique 释放——冻结是应用层。
14. **路径 UNIQUE 是字节比较；NTFS 大小写不敏感。** 所有入库 `worktree_path` 必须走同一个正规化（已有 `normalize_path` / `Path.resolve()`）。禁止字符串比路径。测试必须打到 `E:\` vs `E:/` vs `e:\`。隔离测试 **禁止** `assertNotIn("main", path.split("/"))`（祖先目录名叫 `main` 会误红）。比较 `Path.resolve() == (root / "main").resolve()`。硬拒 `--branch develop`。
15. **现有 Topic 测试按草稿会红。** `tests/helpers/git_fixture.py` 的 `run()` 返回 `None`——计划里的 `run(...).stdout` 是 AttributeError；扩展 helper 或用 `GitResult`。今日没有 `tests/*topic*` 文件；覆盖在 `tests/test_phase4.py`（`TopicTests`）和 `tests/test_verification_v13.py`。它们调用 `topic_start(..., worktree_path=..., 无 agent)`，ready 用假 `commit_sha="abc"` / `"abc123"`。**必须在改变 CLI/Git 证据的同一 Phase 改这些测试**，否则 Phase 2/3「全绿」是假的。
16. **Enqueue 身份链同一事务。** 同时写 `topics.task_id` **以及** 已有 `agent_runs.task_id` **以及** `agent_runs.topic_id`。lookup 测试必须断言三者。循环 FK `topics.active_run_id` ↔ `agent_runs.topic_id`：INSERT topic（run 为空）→ INSERT run → UPDATE topic。run 退出/归档时清空 `active_run_id`。merge 回写必须检查 rowcount。skip 后再裸 enqueue，topic 不得仍指向 skipped task。

---

## CUT（v1 不做）

| 项 | 原因 |
|---|---|
| OpenSpec 目录布局 / 解析 `openspec/changes/` | `--brief-file` 存真实路径即可 |
| flag 墓碑叙事当产品主线 | archive≠delete 已够；UNIQUE 不释放 |
| `topic_events` 事件源表 | `last_step` / `last_error` 足够 continue |
| `result_state` 微状态机 | CHECK 可留；v1 不实现迁移模块 |
| 强制 session / force 拉起 runtime | `--agent` 可选 |
| 立刻删除 `--worktree` | 保留一周期弃用；语义仍禁止 annotate |
| `--execute-commands` 作为默认证据 | Git 证据已是 ready 门禁 |

---

## P1（v1 可顺手，不全部写入计划 §0）

- 新指针列能加 FK 就加；ready 后 UPDATE `topics.verification_record_id`；SHA 被 supersede 时跟指针走；enqueue 不得无 `topic_id` 地 `find_passed_for_commit`。
- CLI show/enqueue：id-or-name 查找；文档写明 name 与另一条 `topics.id` 撞车时的解析顺序。
- `branch_safe_name` 把 `/` → `__`，dest 可能碰撞（`feat/a` vs `feat__a`）。
- `--execute-commands`：argv 而非单字符串；`shell=False`；Windows 上 `"python -c ..."` 不是可执行文件。
- 不要先 `add_feature_branch` 占用 dest，再 `topic-start` 同一 dest（P0-3：不升级裸树）。

---

## DEFER（本切片之后）

- `tasks.topic_id`（须先把 `is_v2_complete` 改成 v1 列 ⊆ 超集）
- 可选真跑 `--execute-commands`（默认关）
- `base_commit` 作 continue 权威；强制 `Orch-Topic:` trailer
- OpenSpec apply/archive 工具链
- stacked PR / GitLab / `candidate_pr` / `pyproject`（已是 non-goals）
- `main/` → `integration/`；放宽 lease / generation
- UNIQUE 墓碑回收（换 name，不静默复用路径）

---

## 建议 v1 切片（同一发布）

```text
1. schema 4：is_v3_complete >=3；classify_db 加 v4；migrate_to_v2/v3 视 v4 已迁；同切片改 test_migrations_v13 空库断言；不 ALTER tasks
2. topics 最小列：agent_name、task_id，可选 last_step/last_error；无 topic_events；canonical id = topics.id（topic_+16hex）
3. 抽出 *_unlocked；短 COMMIT：记行 → git worktree add → agent-start；Git 永不在 BEGIN 内
4. topic-start 必须建 branch+WT；dest 占用 fail-closed；不升级裸 worktree-add；路径一律 normalize_path
5. --agent 可选；无 session 不得标 active；--worktree 保留一周期弃用
6. topic-ready：Git 证据；不入队；不默认 execute-commands
7. topic-enqueue 喂现有队列；同 txn 写 topics.task_id + agent_runs.task_id + agent_runs.topic_id；裸 enqueue 同 path/branch 拒绝或附着
8. 冻结 = pending|merging 禁第二 task / 新 SHA ready；conflict|recovery_required 同 WT 可写；仅 retry 改 source_commit；冻检查 WT 占用+lifecycle 不只 topic_id
9. merge/skip/recover-as-merged 同切片回写（检查 rowcount）；skip 后裸 enqueue 不得指向 skipped task
10. UNIQUE 含 archived；git worktree list 只作见证；身份是关联图不是一根字符串
11. lost/starting/manual_required 占 unique；continue 先 evidence-end；start 不是 takeover；worker 退出后 unique 释放，冻结是应用层
12. 硬拒 --branch develop 与 dest=main/（Path.resolve 比较，禁止 split("/") 含 main）；master_release 双冻
13. 无第三把 WT 文件锁；无 topic_events / OpenSpec 目录 / result_state 微机 / force session
14. 补 topic-abandon；archive 不替代放弃；循环 FK：INSERT topic(null run)→INSERT run→UPDATE topic；退出清 active_run_id
15. 同 Phase 改 test_phase4 / test_verification_v13；git_fixture.run 返回 None，不得 .stdout；路径测 E:\ vs E:/ vs e:\
```

v1 推荐 **加 `topic-abandon`**，而不是广告 `cancelled` 却没有命令。

实现顺序仍可按原 Phase 1→5，但 **Phase 1 砍 Task 1.3 `topic_events`，并改 classify + v13 空库断言**；**Phase 5 与 Phase 4 冻结必须同发**；测试不得用 `provision_session=False` 断言 `active`，也不得用假 SHA 假装 Git 门禁已绿。
