# Topic 闭环开发方案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 orch Topic 从「给已有 worktree 贴标签」升成 **供给隔离** 的可恢复流程：`topic-start`（branch + WT + session，钉住 develop SHA）→ 开发 → `topic-ready`（SHA+command 证据）→ `topic-enqueue`（相对当前 develop 再验、冻 SHA、喂 orch 队列）→ `merge` → `archive`。**ready 与 enqueue 保持两条命令**；merged ≠ deployed。

**Architecture:** Topic 编排器持一把 project lock，调用 **无锁内核**（`worktree-add` / `agent-start` / `enqueue` 抽出的 unlocked 函数）。记录 / `git worktree add` / agent-start 之间短 COMMIT；Git 永不在 `BEGIN` 内。占用权威是 SQLite UNIQUE（含 archived）；`git worktree list` 只作见证。`topics.task_id` 是 enqueue 后的权威关联之一（同事务还写 `agent_runs.task_id` / `agent_runs.topic_id`）；不改 `tasks` 表列。身份是关联图：canonical 只是 `topics.id`。**先读 §0，再读正文。**

**Tech Stack:** Python 3 stdlib、SQLite schema 4、现有 `orch` CLI / unittest。不引入第三方包。不实现 GitLab、`candidate_pr`、`pyproject` 打包。

**依据：** [`docs/topic-industry-analysis.md`](topic-industry-analysis.md) · [`docs/current-architecture.md`](current-architecture.md) §8 / §13 / §14 · 历史意图 [`docs/v1.2-upgrade-plan.md`](v1.2-upgrade-plan.md) §27 · 任务缺口 [`docs/tasks.md`](tasks.md) V12-015。

**基线版本：** `1.3.0`。不要重开 v1.3 promotion；`verification_records` 与 `create_from_topic_ready` 已存在，只加强 Git 证据与 SHA 对齐。

**审查综合：** [`docs/topic-closed-loop-review.md`](topic-closed-loop-review.md)。与 §1–§12 冲突时 **以本节 §0 为准**，不要按旧正文或任务草稿实现。

**可执行任务：** [`docs/topic-closed-loop-tasks.md`](topic-closed-loop-tasks.md)（V14）。§8 任务草稿（`topic_events`、`run(...).stdout`、假 SHA、`split("/")` 含 `main`）**作废**，以 V14 清单为准。

---

## 0. 审查修订（必须先改规格）

实现者不得跳过。下列 P0 已覆盖正文里会编进 TDD 的错误。§8 任务草稿中的 `run(...).stdout`、`assertNotIn("main", path.split("/"))`、`provision_session=False` 断言 `active`、假 SHA `"abc"` 均作废。

### KEEP（不得改）

ready ≠ enqueue ≠ merge ≠ deployed；不抄 `wt merge` / `ship` / Maestro / Gerrit `submitWholeTopic`；不 ALTER `tasks`；抽出 `*_unlocked`；`topic-start` 供给隔离；ready 要 Git 证据；显式 `topic-enqueue` 喂现有队列；硬拒 `--branch develop` 与 dest=`main/`；`master_release` 双冻（可 enqueue，不可 claim）；无第三把 WT 文件锁。

### P0（必须按此实现）

1. **冻结 vs conflict/retry。** 冻结 = `tasks.status ∈ {pending, merging}` 时禁止第二 task、禁止新 SHA `topic-ready`。`conflict`（及 merge Git 不确定的 `recovery_required`）**允许同一 WT 写者**；只有 `orch retry` 可改 `source_commit`；topic 保持 `enqueued`；verification 作废；merge 只信 `tasks.source_commit`。禁止物理锁 WT，否则 retry 无法提交。
2. **Git 是见证，SQLite 是占用权威。** `UNIQUE(project_name, name|branch_name|worktree_path)` 含 archived 仍占用。`git worktree list` fail-closed 只证明 DB 路径仍被 Git 登记，**不得覆盖 UNIQUE**。禁止宣称 Git 为占用 SoT。
3. **topic-start dest 占用。** 无 topic 行且 dest 已是注册 WT → 失败、不 INSERT。dest 在磁盘存在但未注册 → recovery，不删、不收养。continue 仅当 topic 行已在 **且** list 与 path/branch 一致。禁止把裸 `worktree-add` 树升级成 Topic 供给。
4. **SQLite 事务 vs Git。** record / `git worktree add` / agent-start 之间短 COMMIT。Git 永不进 `BEGIN`。continue 不得把不确定 Git 当成功（禁止 Git Town continue-as-success）。
5. **lost/active run continue。** lost 仍占 unique 时不能 INSERT 第二条 run。必须 evidence-end 后再新 run，或 `generation++`。`topic-start` 不是 takeover，不签发 human lease。
6. **裸 enqueue vs Topic。** 与活 topic 同 branch/path → 拒绝，或在同一事务附着。禁止裂脑（队列已有 task、topic 仍 `ready`）。
7. **schema 4 列。** 不 ALTER `tasks`。最小列：`agent_name`、`task_id`，可选 `last_step` / `last_error`。v1 不建满表 `topic_events`；`base_commit` 若加列只作 provenance。分类见 P0-12。
8. **`provision_session=False`。** 不得把 `lifecycle_state` 标 `active`。推荐：start **必须** 建 WT；`--agent` 可选；`--worktree` 保留一周期弃用。测试不许撒谎。
9. **abandon/cancel。** 加 `topic-abandon`（`proposed|active|ready` → `cancelled`），或从广告状态机去掉 `cancelled`。`topic-archive` 不得代替放弃。v1 推荐加 abandon。
10. **冻结与 merge 回写同一切片。** enqueue 一旦冻写者，`merge` / `skip` / recover-as-merged 必须在同一发布回写 lifecycle，否则 WT 永久冻结。
11. **身份是关联图，不是一根字符串。** canonical id = `topics.id`（`topic_` + 16 hex）。其余实体自带前缀：`coord_` 16hex、`run_` 32hex、`tasks.id` 无前缀 32hex、`verify_` 16hex、OpenCode session 不透明、worktree = 文件系统路径。`session_id` / `branch_name` / `worktree_path` / `task_id` **不是** `topic_id`。**§1.3 原第 12 条「一个 `topic_id` 贯穿 branch/path/session/run/queue」作废。**
12. **schema 4 必须改 classify + v13 测试，不只 `is_v3_complete`。** 今日 `is_v3_complete` 是 `user_version == 3`（改为 `>= 3` 或 v4 分支）。`classify_db` 必须有 `v4` 分支，否则 version 4 变成 ambiguous/unsupported。`migrate_to_v2` / `migrate_to_v3` 视 v4 为已迁。`tests/test_migrations_v13.py` `test_empty_db_inits_schema_3` 断言 `user_version == SCHEMA_V3`——空库 init 到 4 会红；**与 schema 4 同切片改该断言**（`ensure_schema` 空库 → `SCHEMA_V4`）。直接调用 `migrate_to_v3` 停在 3 的测试可留。
13. **unique run 索引 ≠ 冻结，且看不见无 `topic_id` 的 `agent-start`。** `lost` / `starting` / `manual_required` 与 running 一样占用 `idx_agent_runs_active_topic`。session 失败后 continue 必须先 evidence-end 旧 run，再新 run 或 `generation++`。不带 `topic_id` 的 `agent-start` 绕过该 unique；enqueue 后的冻结必须查 **worktree 占用 + `lifecycle_state=enqueued`**，不能只查 `topic_id`。worker 退出后 unique 释放——冻结是应用层。
14. **路径 UNIQUE 是字节比较；NTFS 大小写不敏感。** 所有入库 `worktree_path` 走已有 `normalize_path` / `Path.resolve()`。禁止字符串比路径。测试必须打到 `E:\` vs `E:/` vs `e:\`。隔离测试 **禁止** `assertNotIn("main", path.split("/"))`（祖先目录名叫 `main` 会误红）。比较 `Path.resolve() == (root / "main").resolve()`。硬拒 `--branch develop`。
15. **现有 Topic 测试按草稿会红。** `tests/helpers/git_fixture.py` 的 `run()` 返回 `None`——不得写 `run(...).stdout`；扩展 helper 或用 `orch.git._runner.GitResult`。今日没有 `tests/*topic*`；覆盖在 `tests/test_phase4.py`（`TopicTests`）和 `tests/test_verification_v13.py`。它们调用 `topic_start(..., worktree_path=..., 无 agent)`，ready 用假 `commit_sha="abc"` / `"abc123"`。**必须在改变 CLI/Git 证据的同一 Phase 改这些测试**，否则 Phase 2/3「全绿」是假的。
16. **Enqueue 身份链同一事务。** 同时写 `topics.task_id` **以及** 已有 `agent_runs.task_id` **以及** `agent_runs.topic_id`。lookup 测试必须断言三者。循环 FK `topics.active_run_id` ↔ `agent_runs.topic_id`：INSERT topic（run 为空）→ INSERT run → UPDATE topic。run 退出/归档时清空 `active_run_id`。merge 回写必须检查 rowcount。skip 后再裸 enqueue，topic 不得仍指向 skipped task。

### CUT / DEFER（v1）

CUT：OpenSpec 目录、flag 墓碑叙事、`topic_events` 事件源、`result_state` 微机、强制 session、立刻删除 `--worktree`、`--execute-commands` 作默认证据。

DEFER：`tasks.topic_id`、可选真跑 commands、`base_commit` 作 continue 权威。P1（不挡 v1）：新列 FK、`verification_record_id` 指针、id-or-name 撞车文档、`branch_safe_name` dest 碰撞、`--execute-commands` argv/`shell=False`/Windows 可执行文件、禁止 `add_feature_branch` 后再 `topic-start` 同一 dest。

---

## 1. Goal / Non-goals / 不变量

### 1.1 Goal

操作者（或根协调 Session）用 Topic 命令即可走完一条专题，而不靠脑子记住“先 worktree-add 再 agent-start 再 enqueue”：

```text
coordinator-bind
→ topic-start          # 供给 branch+WT+session；钉住 develop SHA；无隔离则失败
→ development          # 现有 agent-* / takeover；单写者 lease；本方案不改 generation 协议
→ topic-ready          # SHA+command 证据；不入队、不合入、不等于 deployed
→ topic-enqueue        # 相对当前 develop 再验；冻 SHA；只喂 orch 队列；锁该 WT
→ merge / retry / skip # 成功则 lifecycle=merged（不是 Done，不是 promote）
→ topic-archive        # flag 式墓碑；不删 Git
→ cleanup --prune      # 仅 merged + cooldown 后
```

每一步写入状态、输入摘要、输出摘要、失败 `kind`。重复命令 = Git Town **continue**（同一 `topic_id`）。禁止双开 worker、双入队、Agent 自 merge。

### 1.2 Non-goals（本方案不做）

- GitLab provider、`candidate_pr`、`pyproject.toml` 打包。
- Graphite / Gerrit stacked PR 或 submit-whole-topic。
- **Worktrunk `wt merge`、Git Town `ship` / `--ship`、Maestro CI 绿自动合入、Agent 自 merge。**
- 把 `topic-ready` 与 `topic-enqueue` 合成一命令；无默认自动入队。以后若要 one-shot，只能加显式 `--enqueue`，且必须先具备本方案幂等/恢复矩阵。
- merge 后自动 Done；`/goal complete` 当 ready；smart-commit 当 ready。
- 把 sprint / OKR / Jira Epic 做成 Topic。
- 把 OMO `/goal` 做成 Topic 状态机（`/goal` 只续跑 coord Desktop）。
- 编排器代 Agent `git commit`；盲目 `undo` 删 worktree/branch。
- 改变 target branch（仍硬编码 `develop`）；在 `main/` 或 develop checkout 写业务。
- 重开 v1.3 promotion / release（merged ≠ deployed）。
- `main/` → `integration/` 改名；放宽 lease / generation。

### 1.3 必须保持的不变量

沿用 `docs/current-architecture.md` §14，闭环特别强调：

1. local `develop` 只经 merge queue（或受锁 release-sync）。
2. `main/` 禁止 Agent worktree。
3. enqueue 冻结明确 `source_commit`。
4. Git 不得运行在 SQLite 写事务内。
5. 不确定结果 → recovery/manual，禁止猜成功。
6. 控制权变更 generation++。
7. Session idle 前不签发 human 可写 lease。
8. Python stdlib only。
9. **v1 保持 `topic-ready` 然后 `topic-enqueue` 两个命令**；merge 是第三条；merged ≠ deployed。
10. `topic-start` **必须供给** branch + 专用 worktree + session；不能隔离则失败（`topic_isolation_required`）。
11. 新 branch 从 **钉住的 develop SHA** 创建（先 `rev-parse`，再 `git worktree add -b <branch> <path> <sha>`），写入 `topics.base_commit`。
12. **身份是关联图，不是一根字符串。** canonical 只是 `topics.id`（`topic_` + 16 hex）。branch / path / session / run / queue 各有自己的 id 或路径，经外键/指针关联；`session_id` / `branch_name` / `worktree_path` / `task_id` 都不是 `topic_id`。占用权威是 SQLite UNIQUE；`git worktree list` 只作见证（见 §0 P0-2、P0-11）。
13. enqueue 后冻结 SHA 期间（**仅** `pending|merging`），orch 拒绝第二 task 与新 SHA `topic-ready`。`conflict` / `recovery_required` 允许同一 WT 写者；冻结查 worktree 占用 + `lifecycle=enqueued`，不只 `topic_id`（见 §0 P0-1、P0-13）。
14. 同时最多一个 `active_run_id`；控制权只经 generation lease。
15. archive ≠ delete；cleanup 仅在落地后。OMO `/goal` 不是 Topic。

---

## 2. 现状差距（代码 vs 文档）

代码以 `orch/commands/topic.py`、`orch/cli.py`、`orch/migrations.py` schema 2/3、`orch/commands/enqueue.py` 为准。V12-015 / §27 描述的是意图，不是现状。

| # | 能力 | 文档 / V12-015 说 | 代码做 | 状态 |
|---|---|---|---|---|
| 1 | coordinator-bind/show | 每项目一个 active coordinator | 已实现；`--replace` generation++ | ✅ |
| 2 | topic-list/show/open/archive | 产品记录 CRUD | 已实现；open 无 run 时仅 directory locator | ⚠️ 缺 run 绑定 |
| 3 | topic-start 编排 worktree/session/worker | 要编排 | 只 INSERT `topics`；要求 worktree 已存在；无 `--agent` | ❌ |
| 4 | 结构化 brief | `--brief-file` + 仓库内计划 | `plan_path = "brief:..."` 标记 | ❌ |
| 5 | lifecycle `proposed→active→…` | 完整状态机 | start=`proposed`；ready 直接 `ready`；从不写 `active`/`enqueued`/`merged` | ⚠️ |
| 6 | result_state | 成果成熟度 | brief 时 `planning`；ready 时 `ready_for_enqueue`；中间态未走 | ⚠️ |
| 7 | topic-ready Git 门禁 | clean / HEAD / 非空 diff / 命令证据 | 只检查 commands+commit 字段；**声明成功**；不看 worktree | ⚠️ 桥接 verification 已有 |
| 8 | topic-enqueue | 独立命令，复用五项校验 | **无命令**；ready 后仍用 `enqueue` | ❌ |
| 9 | Topic ↔ task | enqueue 后可定位唯一 task | `topics` 无 `task_id`；`tasks` 无 `topic_id` | ❌ |
| 10 | 一 Topic 一 active run | DB 约束 | `active_run_id` 可空；无 unique；start 不写 run | ❌ |
| 11 | merge 回写 Topic | merged 后 lifecycle=merged | `finalize_success` 只更新 `tasks` | ❌ |
| 12 | archive 守卫 | 活跃队列中不可归档 | 任意 topic_id 都可 archived | ⚠️ |
| 13 | 步骤证据 / 幂等恢复 | 每步 I/O + 失败 | 无 `topic_events`；失败即抛错无 checkpoint | ❌ |
| 14 | agent 字段 | `--agent` 供 enqueue 使用 | `topics` **无 `agent_name`** | ❌ |
| 15 | 钉住 develop SHA 再建 branch | `git worktree add -b … <sha>` | `worktree-add` 以 `develop` **名字** 为 base，不持久化 SHA | ❌ |
| 16 | 无隔离则失败 | 禁止 `main/` / develop checkout | start 接受任意 `--worktree` 字符串，不验 Git | ❌ |
| 17 | 冻 SHA 后锁 WT | enqueued 期间拒 writer | 无 `topic_sha_frozen` | ❌ |
| 18 | Git SoT + trailer | worktree list；`Orch-Topic:` | 仅 SQLite 行；Git 不一致不检测 | ❌ |
| 19 | merged ≠ deployed | Topic 停在 merged；promote 另走 | 无 deployed 态（好）；但易被当成 Done | ⚠️ |
| 20 | RFC/ADR/OpenSpec brief | `--brief-file` 真实路径 | `brief:` 标记 | ❌ |

---

## 3. 目标 CLI 与状态机

### 3.1 CLI（v1 闭环）

```text
orch <project> topic-start <name>
  --title TEXT --goal TEXT --agent AGENT --branch BRANCH
  [--brief-file PATH]
  [--prompt TEXT | --prompt-file PATH]
  --json
```

产品路径 **必须** 供给 session。`--worktree` 不作为「已有目录贴标签」入口：路径一律按 `worktrees/<agent>-<safe-branch>` 计算。测试若不能拉起 runtime，只允许 Python 调用 `topic_start(..., provision_session=False)`，**不**把 `--no-agent` 做成公开 CLI。

其余命令：

```text
orch <project> topic-list [--all] --json
orch <project> topic-show <topic-id-or-name> --json
orch <project> topic-open <topic-id-or-name> [--fork] [--launch] --json

orch <project> topic-ready <topic-id-or-name>
  --commit SHA --command CMD [--command CMD ...]
  [--execute-commands]
  --json

orch <project> topic-enqueue <topic-id-or-name> [--priority N] --json

orch <project> topic-archive <topic-id-or-name> --json
```

兼容：

- 现有 `enqueue <agent> <branch> <worktree>` **保留**（无 topic 的 v1.1 路径）。
- **没有** `--enqueue` / `--ship`。禁止 `topic-ready` 入队或合入。
- `--brief-file` 指向 RFC / ADR / OpenSpec `proposal.md`；OpenSpec change ≈ 一个 Topic。

`topic-id-or-name`：先按 `id` 匹配，否则按 `UNIQUE(project_name, name)`。

### 3.2 lifecycle_state（产品阶段）

已有 CHECK，不改闭集：

```text
proposed → active → ready → enqueued → merged → archived
                ↘ rejected | cancelled → archived
```

| 命令 | 成功后 lifecycle |
|---|---|
| topic-start 写入行 | `proposed`，worktree 就绪后 `active` |
| topic-ready | `ready` |
| topic-enqueue | `enqueued` |
| merge finalize_success / recover-as-merged | `merged`（**不是** Done / deployed / promoted） |
| skip 且 topic 仍指向该 task | `rejected`（result_state=`rejected`） |
| topic-archive | `archived`（flag 式墓碑；Git 仍在） |

### 3.3 result_state（成果成熟度）

已有 CHECK，不改闭集。worker idle **不等于** ready。

```text
none → planning → planned → implementing → verifying → ready_for_enqueue
                                                      ↘ rejected
```

| 事件 | result_state |
|---|---|
| start 无 brief | `none` |
| start 有 brief-file | `planning`，随后 `planned` |
| agent-start 绑定 run | `implementing` |
| topic-ready 开始校验 | `verifying`（校验失败则停在 `verifying` 或保持原值并记 event） |
| topic-ready 成功 | `ready_for_enqueue` |
| skip | `rejected` |

`ready_for_commit` 本阶段不单独做命令；有脏工作区时 `topic-ready` 直接失败。

### 3.4 与 runtime / queue 的边界

```text
topics.lifecycle_state     产品阶段（本方案主控）
topics.result_state        成果门禁
agent_runs.state           worker / lease（禁止混用）
tasks.status               队列（pending/merging/conflict/recovery_required/merged/skipped）
```

---

## 4. 数据模型（schema 4）

`SCHEMA_VERSION` 现为 3。闭环需要 **schema 4**（`user_version=4`）。

### 4.1 故意不改 `tasks` 表

`is_v2_complete()` 要求 `tasks` / `audit_log` / `counters` 列集 **与 v1 完全相等**。给 `tasks` 加 `topic_id` 会让所有 v2/v3 完整性检查失败。

权威关联放在 Topic 侧：

```text
topics.task_id  →  tasks.id     -- enqueue 后必填；lookup: WHERE task_id = ?
topics.active_run_id → agent_runs.id   -- 已有 FK
agent_runs.topic_id → topics.id        -- 新增；一 Topic 同时最多一个非 exited/archived run
```

从 task 反查：`SELECT * FROM topics WHERE task_id = ?`。

若未来要 `tasks.topic_id`，必须先把 `is_v2_complete` 改成“v1 列为子集”，**不在本方案做**。

### 4.2 `topics` 新列（ALTER ADD）

| 列 | 类型 | 含义 |
|---|---|---|
| `agent_name` | TEXT | enqueue / worktree 命名用；旧行可为 NULL，enqueue 时必填 |
| `base_commit` | TEXT | start 时钉住的 develop SHA |
| `task_id` | TEXT | 当前/最后一条队列任务 |
| `verification_record_id` | TEXT | 最近一次成功 ready 的 record |
| `last_step` | TEXT | `record` / `worktree` / `agent` / `ready` / `enqueue` / `merge` / `archive` |
| `last_error` | TEXT | 最近一次失败 kind+message 摘要（已脱敏） |

### 4.3 `agent_runs.topic_id`

`ALTER TABLE agent_runs ADD COLUMN topic_id TEXT;`  
`is_v2_complete` 对 agent_runs 已是列超集检查，安全。

### 4.4 `topic_events`

```sql
CREATE TABLE IF NOT EXISTS topic_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  step TEXT NOT NULL CHECK(step IN (
    'record','worktree','agent','ready','enqueue','merge','archive'
  )),
  status TEXT NOT NULL CHECK(status IN ('started','ok','failed')),
  input_json TEXT,
  output_json TEXT,
  error_kind TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(topic_id, seq),
  FOREIGN KEY(topic_id) REFERENCES topics(id)
);
```

写入规则：先 `started`，外部 I/O（Git/HTTP）在 **事务外**，再 `ok`/`failed`。失败不删除 `started` 行。

### 4.5 唯一性

```sql
-- 一个 Topic 同时最多一个未结束 run（应用层 + 索引）
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_active_topic
  ON agent_runs(topic_id)
  WHERE topic_id IS NOT NULL
    AND state NOT IN ('exited', 'archived');

-- enqueue 后一个 Topic 最多一条活跃队列任务
CREATE UNIQUE INDEX IF NOT EXISTS idx_topics_active_task
  ON topics(task_id)
  WHERE task_id IS NOT NULL
    AND lifecycle_state = 'enqueued';
```

已有：`UNIQUE(project_name, name|branch_name|worktree_path)`。

`plan_path`：存 brief 文件的规范化路径（RFC/ADR/OpenSpec proposal），**禁止** `brief:` 前缀。建议 brief 含 `Orch-Topic: <id>` 与验证命令列表。

### 4.6 迁移必须改的分类函数

`is_v3_complete()` 今日要求 `user_version == 3`。升到 4 后若不改，v3 完整性恒为假。Phase 1 必须改为 `user_version() >= SCHEMA_V3`，并增加 `SCHEMA_V4 = 4`、`is_v4_complete`、`migrate_to_v4`、`classify_db` 的 `v4` 分支。模式照抄 `migrate_to_v3`：单次 `BEGIN IMMEDIATE`，失败 rollback，二次调用 noop。

空库 init：v1+v2+v3+v4 一次到位（`_init_empty_v4`）。

---

## 5. 命令行为

### 5.1 锁与“无锁内核”（所有编排的前提）

`worktree-add`、`agent-start`、`enqueue`、`topic-*` 都抢 **同一把** `project.lock`。Topic 编排若再调这些命令会 **死锁**。

抽出 unlocked 函数（仍做全部 Git/HTTP/DB 逻辑，只是不 `acquire`）：

| 现有 | 抽出 |
|---|---|
| `cmd_worktree_add` | `worktree_add_unlocked(conn, ...)` |
| `AgentLifecycleService.start` | `start_unlocked(conn, ..., topic_id=None)` |
| `cmd_enqueue` | `enqueue_unlocked(conn, ..., topic_id=None)` |

公开 CLI 仍先 `acquire` 再调 unlocked。`topic-start` / `topic-enqueue` 只 acquire 一次。

### 5.2 `topic-start`（供给，不是 annotate）

产品语义：成功 = **新 branch + 专用 worktree + 专题 session**。Git SoT：先在事务外 `rev-parse develop` 得到 `base_sha`，再 `git worktree add -b <branch> <dest> <base_sha>`（与 Worktrunk `--create --base=<sha>` 同构）。禁止只传会移动的 `develop` 名字而不记下 SHA。

1. 校验 coordinator active；`--agent` / `--branch` 用现有校验。
2. dest = `worktrees/<agent>-<safe-branch>`。若 dest 是 `main/`、将检出 `develop`、或不属于本 bare → `topic_isolation_required`。永不在 develop checkout 写业务。
3. **幂等键是 `topic_id`**（UNIQUE name 是别名）：
   - 无行 → INSERT `proposed`，返回稳定 `topic_id`。
   - 有行且 agent/branch 一致 → Git Town **continue**：以 `git worktree list` 为准补树（沿用已记 `base_commit`），再补 session。
   - 字段冲突 → `topic_exists_mismatch`。
   - Git 与 DB 不一致 → recovery，不猜。
4. 持久化 `base_commit`、`worktree_path`、`agent_name`、`lifecycle_state='active'`。
5. **必须** `start_unlocked(..., topic_id=tid)`（一 Topic 一 `active_run_id`）。runtime 未就绪则失败。仅测试可 `provision_session=False`。
6. `--brief-file` 存 RFC/ADR/OpenSpec 真实路径；brief 建议 `Orch-Topic: <topic_id>` trailer；orch 不改写 commit。
7. 返回同一 `topic_id` + locator + `run_id`/`session_id` + `base_commit` + events。

无隔离不得降级成「只插一行」。

### 5.3 `topic-ready`（不 enqueue、不合入）

成功前置（锁内、Git 在事务外）：

1. lifecycle ∈ {`active`,`ready`}。`enqueued` → `topic_sha_frozen`。
2. `git worktree list` 可见该 path；属于 bare；不是 `main/`。
3. HEAD 分支 == `topics.branch_name`；porcelain 为空。
4. `--commit` == `rev-parse HEAD`。
5. `rev-list --count develop..HEAD` > 0（相对 **当前** develop = merge-queue retest vs base）。
6. `--command` 非空。smart-commit / 模型声称完成 **不够**。
7. 写作中的 worker → `topic_worker_still_writing`。

然后 `create_from_topic_ready`。`--execute-commands` 真跑（`shell=False`）。成功 JSON 必须 `enqueued: false`。失败写 event，不把 lifecycle 打成 ready。

### 5.4 `topic-enqueue`（喂 orch 队列，不抄 wt merge）

1. `ready` + `ready_for_enqueue`；`agent_name` 非空。
2. 有效 verification：passed、未过期、SHA == HEAD。
3. `enqueue_unlocked` 五项再验；冻结 SHA 必须等于 verification SHA。
4. 写 `topics.task_id`、`lifecycle_state='enqueued'`。此后该 WT 冻结：`agent-start` / 新 SHA `topic-ready` → `topic_sha_frozen`。
5. 幂等：同 SHA 活跃 task 则返回原 `task_id`。

**不合入、不 promote、无 `--ship`。** 合入只经 `orch merge`。

### 5.5 merge / skip 回写

在 `finalize_success` 与 `recover.py` 把 task 标 `merged` 的同一 DB 事务内：

```sql
UPDATE topics
SET lifecycle_state = 'merged', last_step = 'merge', updated_at = ?
WHERE task_id = ? AND lifecycle_state = 'enqueued';
```

`cmd_skip`：若 `topics.task_id` 指向该 task 且 lifecycle=`enqueued`，则 `rejected`。树保留。**不是**自动 Done，**不是** deployed。recovery_required 不把 topic 标 merged。

不在 merge 事务里跑 Git。

### 5.6 `topic-archive`（flag 式，≠ delete）

允许：`merged` / `rejected` / `cancelled` / `ready` / `active` / `proposed`。

拒绝：`enqueued` 且 task 仍活跃 → `topic_enqueue_active`。

已 `archived`：幂等返回 `archived: true`。

只改产品行 + event（OpenSpec dated archive 对应物）。UNIQUE name/branch/path 仍占用。**不**调 cleanup。物理删除只在落地后 `cleanup --prune`。

---

## 6. 幂等与恢复矩阵

| 场景 | 行为 |
|---|---|
| 重复 `topic-start` 同 name/agent/branch | 同一 `topic_id`；continue 补 WT/session |
| start 无隔离（`main/` / develop checkout） | `topic_isolation_required`；不 INSERT 成功态 |
| start 树成、session 失败后重试 | 复用树与 `base_commit`；再 start worker |
| Git worktree list 与 DB 冲突 | recovery；不猜 |
| `topic-ready` 脏 / SHA≠HEAD / 空 diff / 无 command | 失败；保持原 lifecycle |
| smart-commit 无 `--command` | 拒绝 |
| worker 仍 running | `topic_worker_still_writing` |
| enqueued 后再 ready 或 agent-start | `topic_sha_frozen` |
| 重复 ready 同一 SHA | supersede verification；仍 ready；`enqueued: false` |
| ready 后 HEAD 前进再 enqueue | SHA mismatch；先再 ready |
| 重复 enqueue 同 SHA | 返回已有 task |
| enqueue 后 merge 成功 | topic → merged（不是 Done/deployed） |
| merge `recovery_required` | topic 仍 enqueued；`reset-stuck`；禁止猜 merged |
| skip | topic → rejected；树保留 |
| archive 活跃 enqueued | 拒绝 |
| archive 已 archived | 幂等成功；不删 Git |
| cleanup --prune | 仅 merged+cooldown+guard+祖先 |
| 想 `undo` 已建的 WT | 不自动删；走 skip/archive+cleanup |
| Git 结果不确定 | failed event，状态不前移 |

---

## 7. 文件地图

| 路径 | 职责 |
|---|---|
| `orch/migrations.py` | schema 4、`is_v3_complete` 改为 `>=3`、`migrate_to_v4` |
| `orch/topic_state.py` | **新建** lifecycle/result 合法迁移 |
| `orch/topic_events.py` | **新建** 追加 event、seq |
| `orch/commands/worktree_add.py` | 抽出 `worktree_add_unlocked` |
| `orch/runtime/lifecycle.py` | `start_unlocked` + `topic_id` |
| `orch/commands/enqueue.py` | 抽出 `enqueue_unlocked` |
| `orch/commands/topic.py` | start 编排、ready Git 门禁、archive 守卫、id-or-name |
| `orch/commands/topic_enqueue.py` | **新建** `topic_enqueue` |
| `orch/cli.py` | 注册 `topic-enqueue`；扩展 `topic-start`/`topic-ready` 参数 |
| `orch/merge/finalize.py` | merged 回写 topics |
| `orch/merge/recover.py` | recover-as-merged 同样回写 |
| `orch/commands/skip.py` | skip 回写 rejected |
| `tests/test_migrations_v14.py` | **新建** schema 4 |
| `tests/test_topic_state.py` | **新建** 状态闭集 |
| `tests/test_topic_closed_loop.py` | **新建** start/ready/enqueue 单测 |
| `tests/test_acceptance_topic.py` | **新建** git fixture 验收 |
| `tests/test_phase4.py` | 更新 start 签名；ready 仍 `enqueued is False` |
| `tests/test_verification_v13.py` | ready 需真实 SHA 或 mock Git |
| `tests/test_skill_consistency.py` | 要求出现 `topic-enqueue` |
| `skills/orchestrator/SKILL.md` | Topic 闭环表面 |
| `docs/usage-scenarios.md` §4–5 | 补 topic-* 决策树 |
| `docs/current-architecture.md` §8 | 实现后由执行者改“已实现”列表（本方案只加 §13 指针） |

---

## 8. 分阶段任务

建议顺序：**1 → 2 → 3 → 4 → 5 → 6**。每阶段可单独合并。TDD：先测后码。提交信息由执行者按阶段撰写；本方案不替你 commit。

验证命令一律：

```text
python -m unittest <模块> -v
```

Windows 下在仓库根目录、已能 `python -m orch` 的同一解释器。

---

### Phase 1 — schema 4 与状态机（低风险，解开后续）

**目的：** 列、表、索引、分类函数。不改 CLI 行为。

#### Task 1.1: v3 完整性改为向前兼容 + schema 4 脚手架

**Files:**

- Modify: `orch/migrations.py`
- Test: `tests/test_migrations_v14.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_migrations_v14.py
"""Schema 4: topic closed-loop columns + topic_events."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orch.db import connect
from orch.migrations import (
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_VERSION,
    ensure_schema,
    is_v3_complete,
    is_v4_complete,
    migrate_to_v3,
    migrate_to_v4,
    user_version,
)


class Schema4Tests(unittest.TestCase):
    def test_empty_inits_schema_4(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "e.db")
            result = ensure_schema(conn)
            self.assertEqual(result["action"], "init")
            self.assertEqual(user_version(conn), SCHEMA_V4)
            self.assertEqual(SCHEMA_VERSION, SCHEMA_V4)
            self.assertTrue(is_v4_complete(conn))
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("topic_events", tables)
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(topics)").fetchall()
            }
            self.assertIn("agent_name", cols)
            self.assertIn("task_id", cols)
            self.assertIn("base_commit", cols)
            conn.close()

    def test_v3_complete_after_upgrade_to_v4(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "v3.db")
            migrate_to_v3(conn)
            self.assertEqual(user_version(conn), SCHEMA_V3)
            migrate_to_v4(conn)
            self.assertTrue(is_v3_complete(conn), "v4 DB must still count as v3-complete")
            self.assertTrue(is_v4_complete(conn))
            conn.close()

    def test_migrate_v4_twice_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "n.db")
            migrate_to_v4(conn)
            second = migrate_to_v4(conn)
            self.assertEqual(second["action"], "noop")
            conn.close()
```

- [ ] **Step 2: 跑测确认失败**

```text
python -m unittest tests.test_migrations_v14 -v
```

Expected: `ImportError` 或 `AssertionError`（尚无 `SCHEMA_V4`）。

- [ ] **Step 3: 实现最小迁移**

在 `orch/migrations.py`：

- `SCHEMA_V4 = 4`，`SCHEMA_VERSION = 4`。
- `is_v3_complete`：`if user_version(conn) < SCHEMA_V3: return False`（不要 `!= 3`）。
- `SCHEMA_V4_ADDITIVE_SQL`：`ALTER TABLE` 对已存在库；空库在 CREATE topics 时直接含新列 **或** 统一走 ADD COLUMN（SQLite 对已有列的 ADD 会失败——空库应 CREATE 完整 topics，v3→v4 用 `ADD COLUMN IF` 不可用）。**推荐：** v4 只用 `ALTER TABLE ... ADD COLUMN` + `CREATE TABLE topic_events` + 新 INDEX；空库 init 仍跑 v1+v2+v3 再跑同一套 ALTER（重复 ADD 会 fail）。因此 migrate 必须检查列是否已存在：

```python
def _add_column_if_missing(conn, table: str, ddl: str) -> None:
    # ddl example: "agent_name TEXT"
    name = ddl.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
```

- `classify_db`：`ver > SCHEMA_VERSION` → unsupported；`ver == 4` → v4 if complete else ambiguous。
- `ensure_schema` 最终调 `migrate_to_v4`。
- 新索引名加入 `is_v4_complete`。

**不要** ALTER `tasks`。

- [ ] **Step 4: 全量迁移回归**

```text
python -m unittest tests.test_migrations_v14 tests.test_migrations_v13 tests.test_migrations_v12 -v
```

Expected: PASS。

**DoD：** 空库 `user_version=4`；v3 库可升；二次迁移 noop；旧 tasks 行字节级不变（对照 v13 测法 snapshot `tasks`）。

---

#### Task 1.2: Topic 状态迁移模块

**Files:**

- Create: `orch/topic_state.py`
- Test: `tests/test_topic_state.py`

- [ ] **Step 1: 失败测试**

```python
# tests/test_topic_state.py
from __future__ import annotations
import unittest
from orch.errors import ValidationError
from orch.topic_state import assert_lifecycle_transition, assert_result_transition

class TopicStateTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        assert_lifecycle_transition("proposed", "active")
        assert_lifecycle_transition("active", "ready")
        assert_lifecycle_transition("ready", "enqueued")
        assert_lifecycle_transition("enqueued", "merged")
        assert_lifecycle_transition("merged", "archived")

    def test_skip_reject(self) -> None:
        assert_lifecycle_transition("enqueued", "rejected")
        assert_lifecycle_transition("active", "cancelled")

    def test_illegal(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            assert_lifecycle_transition("proposed", "enqueued")
        self.assertEqual(ctx.exception.kind, "topic_lifecycle_illegal")

    def test_result(self) -> None:
        assert_result_transition("none", "planning")
        assert_result_transition("implementing", "verifying")
        assert_result_transition("verifying", "ready_for_enqueue")
        with self.assertRaises(ValidationError):
            assert_result_transition("none", "ready_for_enqueue")
```

- [ ] **Step 2:** `python -m unittest tests.test_topic_state -v` → FAIL（模块不存在）。

- [ ] **Step 3: 实现** `orch/topic_state.py`：闭集与 DB CHECK 一致；`None → proposed` 仅用于 INSERT。`ready → ready` 允许（幂等 ready）。`enqueued → enqueued` 不允许经由本函数（enqueue 幂等走命令层短路，不调用 transition）。

**DoD：** 非法迁移 kind 稳定为 `topic_lifecycle_illegal` / `topic_result_illegal`。

---

#### Task 1.3: topic_events 帮助函数

**Files:**

- Create: `orch/topic_events.py`
- Test: 可放在 `tests/test_topic_closed_loop.py` 的第一批（Phase 1 末可只测 append seq）。

```python
def append_event(conn, topic_id: str, step: str, status: str, *,
                 input_json=None, output_json=None, error_kind=None) -> int:
    ...
```

seq = `MAX(seq)+1`。本任务不接 CLI。

**DoD：** 同一 topic 两次 append 得到 seq 1,2；JSON 可空。

---

### Phase 2 — 抽出 unlocked 内核 + `topic-start` 编排

**Depends on:** Phase 1。

#### Task 2.1: `worktree_add_unlocked`

**Files:** Modify `orch/commands/worktree_add.py`；现有 `tests/test_acceptance_enqueue.py` / 集成测不得破坏。

- [ ] 把 Git+返回值移到 `worktree_add_unlocked(project, agent, branch, *, base_sha: str)`。`base_sha` 必须是已 `rev-parse` 的 develop 提交，**不要**把活引用 `develop` 传进 `worktree add -b` 而不钉 SHA。公开 `cmd_worktree_add` 先 `rev-parse` 再调 unlocked（CLI 行为对调用方仍像从 develop 建分支）。
- [ ] `python -m unittest tests.test_acceptance_enqueue tests.test_integration_flow -v`

**DoD：** CLI `worktree-add` 行为不变；unlocked 可在已持锁时调用。

---

#### Task 2.2: `start_unlocked(..., topic_id=None)`

**Files:** Modify `orch/runtime/lifecycle.py`。

- INSERT `agent_runs` 时写入 `topic_id`。
- 若 `topic_id` 已有另一个非 exited/archived run → IntegrityError 转 `agent_worktree_busy` 或新 kind `topic_run_active`。
- 现有 agent-start 测试全绿。

```text
python -m unittest tests.test_runtime_service tests.test_control_lease tests.test_agent_state_machine -v
```

（按仓库里实际覆盖 start 的模块跑；若无单独 start 测，至少不破坏 runtime 测。）

---

#### Task 2.3: `topic-start` 编排（TDD）

**Files:**

- Modify: `orch/commands/topic.py`, `orch/cli.py`
- Test: `tests/test_topic_closed_loop.py`

CLI 变更：

- `--agent` required；路径固定 `worktrees/<agent>-<safe-branch>`
- `--brief-file` optional
- `--prompt` / `--prompt-file` 与 `agent-start` 对齐
- **无公开 `--no-agent` / `--worktree` annotate**

- [ ] **Step 1: 失败测试（fixture 用 `provision_session=False`；产品路径仍必须能供给 session）**

```python
def test_topic_start_provisions_worktree_from_pinned_develop(self) -> None:
    from orch.commands.topic import coordinator_bind, topic_start
    coordinator_bind(self.project, session_id="ses_c", directory=str(self.env.proj))
    out = topic_start(
        self.project,
        name="auth",
        title="Auth",
        goal="ship",
        agent="agentA",
        branch_name="feat/auth",
        provision_session=False,  # 仅单测；CLI 无此旗
    )
    tid = out["topic"]["id"]
    wt = Path(out["topic"]["worktree_path"])
    self.assertTrue(wt.is_dir())
    self.assertNotIn("main", str(wt).replace("\\", "/").split("/"))
    self.assertEqual(out["topic"]["lifecycle_state"], "active")
    self.assertTrue(out["topic"]["base_commit"])
    out2 = topic_start(
        self.project,
        name="auth",
        title="Auth",
        goal="ship",
        agent="agentA",
        branch_name="feat/auth",
        provision_session=False,
    )
    self.assertEqual(out2["topic"]["id"], tid)

def test_topic_start_rejects_main_isolation(self) -> None:
    from orch.errors import ValidationError
    # dest 若解析到 main/ 或 HEAD=develop → topic_isolation_required
```

- [ ] **Step 2:** `python -m unittest tests.test_topic_closed_loop -v` → FAIL。

- [ ] **Step 3: 实现** `topic_start` 按 §5.2。更新 `tests/test_phase4.py`：补 `agent=`；fixture 用 `provision_session=False`。

- [ ] **Step 4:**

```text
python -m unittest tests.test_topic_closed_loop tests.test_phase4 tests.test_cli -v
```

**DoD：** 钉住 `base_commit`；路径永不落在 `main/`；重复 start 返回同一 `topic_id`；无隔离失败；公开 CLI 无 `--no-agent`。

**嵌套锁：** `topic_start` 持锁期间只调 `*_unlocked`。

---

### Phase 3 — `topic-ready` 证据

**Depends on:** Phase 2（worktree 真实存在才测得了 Git 门禁）。

#### Task 3.1: Git 门禁 + SHA == HEAD

**Files:** Modify `orch/commands/topic.py`；`tests/test_topic_closed_loop.py`；更新 `tests/test_verification_v13.py` 与 `tests/test_phase4.py`（它们现在用 `commit_sha="abc"`，**会失败**，必须改为真实 HEAD 或 mock）。

- [ ] **脏工作区拒绝**

```python
def test_topic_ready_rejects_dirty(self) -> None:
    started = self._start_topic_fixture()  # provision_session=False
    wt = Path(started["topic"]["worktree_path"])
    (wt / "x.txt").write_text("n\n", encoding="utf-8")
    from orch.commands.topic import topic_ready
    from orch.errors import ValidationError
    with self.assertRaises(ValidationError) as ctx:
        topic_ready(
            self.project,
            started["topic"]["id"],
            verification={"commit_sha": "dead", "commands": ["pytest"]},
        )
    self.assertIn(ctx.exception.kind, {
        "topic_worktree_dirty",
        "topic_verification_sha_mismatch",
        "enqueue_validation_failed",
    })
```

kind 固定为：

- `topic_worktree_dirty`
- `topic_branch_mismatch`
- `topic_empty_change`
- `topic_verification_sha_mismatch`
- `topic_worker_still_writing`
- `topic_sha_frozen`（enqueued 之后）

- [ ] **HEAD 对齐才过**

用 `tests.helpers.git_fixture.run` 在 worktree 里 commit，把 `rev-parse HEAD` 传给 `--commit`。断言 `enqueued is False` 且 DB 有 `verification_records`。

- [ ] **空 diff 拒绝：** fixture start（`provision_session=False`）后未 commit，ready 失败 `topic_empty_change`。
- [ ] **冻结锁：** enqueue 之后 `topic-ready` 或 `agent-start` 同一 WT → `topic_sha_frozen`。

```text
python -m unittest tests.test_topic_closed_loop tests.test_verification_v13 tests.test_phase4 tests.test_promotion_v13 -v
```

promotion 测不依赖假 SHA 的 topic-ready 即可；不要改 promotion 语义。

**DoD：** 未证明 HEAD==commit 不得 `ready`；JSON 始终 `enqueued: false`。

---

#### Task 3.2: `--execute-commands`（可选增强，仍本阶段）

**Files:** `orch/cli.py` `--execute-commands`；`topic_ready(..., execute_commands=False)`。

- [ ] 测试：`--command` 用 `python -c "import sys; sys.exit(1)"` → 失败，lifecycle 不是 ready。
- [ ] 测试：`python -c "raise SystemExit(0)"` 或 `echo` 不可移植时用 `sys.executable -c "print(1)"` → 成功，results.exit_code==0，且 **不是** `declared_by_topic_ready`。

默认关闭，旧调用方仍声明成功（但必须过 Git 门禁）。

**DoD：** `shell=False`；失败不入队、不改成 ready。

---

### Phase 4 — `topic-enqueue`

**Depends on:** Phase 3。

#### Task 4.1: `enqueue_unlocked`

**Files:** `orch/commands/enqueue.py`

抽出 Git 校验 + INSERT。可选参数 `topic_id: str | None = None`（只用于 audit detail，**不写 tasks 新列**）。`cmd_enqueue` 包一层锁。

```text
python -m unittest tests.test_acceptance_enqueue -v
```

**DoD：** 无 topic 的 enqueue 行为与现在完全一致。

---

#### Task 4.2: 新命令 `topic-enqueue`

**Files:**

- Create: `orch/commands/topic_enqueue.py`
- Modify: `orch/cli.py`（`PROJECT_COMMANDS`、parser、dispatch）
- Test: `tests/test_topic_closed_loop.py`、`tests/test_acceptance_topic.py`

- [ ] **Step 1: 失败测试**

```python
def test_topic_enqueue_freezes_verification_sha(self) -> None:
    started = self._start_topic_fixture()
    wt = Path(started["topic"]["worktree_path"])
    (wt / "f.txt").write_text("1\n", encoding="utf-8")
    from tests.helpers.git_fixture import run
    run(["git", "add", "f.txt"], cwd=wt)
    run(["git", "commit", "-m", "f"], cwd=wt)
    sha = run(["git", "rev-parse", "HEAD"], cwd=wt).stdout.strip()
    from orch.commands.topic import topic_ready
    from orch.commands.topic_enqueue import topic_enqueue
    topic_ready(
        self.project,
        started["topic"]["id"],
        verification={"commit_sha": sha, "commands": ["pytest"]},
    )
    enq = topic_enqueue(self.project, started["topic"]["id"], priority=1)
    self.assertTrue(enq["enqueued"])
    self.assertEqual(enq["source_commit"], sha)
    self.assertEqual(enq["topic"]["lifecycle_state"], "enqueued")
    again = topic_enqueue(self.project, started["topic"]["id"])
    self.assertEqual(again["task_id"], enq["task_id"])

def test_topic_ready_does_not_enqueue(self) -> None:
    ...
    ready = topic_ready(...)
    self.assertFalse(ready["enqueued"])
```

- [ ] **Step 2:**

```text
python -m unittest tests.FAKESECRET_s2t3u4v5w6x7y8z9a0b1 -v
```

Expected: FAIL（无命令）。

- [ ] **Step 3: 实现** §5.4。CLI：

```python
te = add("topic-enqueue", "enqueue topic after ready (freeze SHA, no merge)")
te.add_argument("topic_id")
te.add_argument("--priority", type=int, default=1)
```

- [ ] **Step 4: 验收测** `tests/test_acceptance_topic.py`：Python fixture start（或 mock session）→ commit → `topic-ready` → `topic-enqueue` → `pending` 见到该 branch → 再 enqueue 同 task。CLI 路径不得使用 `--no-agent`。断言 `ready["enqueued"] is False`。

```text
python -m unittest tests.test_topic_closed_loop tests.test_acceptance_topic tests.test_acceptance_enqueue -v
```

**DoD：** 无 `topic-enqueue` 则无法从 Topic JSON 得到 `task_id`；ready 绝不插入 `tasks`；冻结 SHA == verification SHA。

---

### Phase 5 — merge 回写、archive 守卫、skip

**Depends on:** Phase 4。

#### Task 5.1: merge / recover → `merged`

**Files:** `orch/merge/finalize.py`、`orch/merge/recover.py`  
**Test:** `tests/test_acceptance_topic.py` 扩一条：enqueue 后 `merge --once`，`topic-show` lifecycle=`merged`。

```text
python -m unittest tests.test_acceptance_topic tests.test_acceptance_freeze_and_conflict tests.test_interrupt_reconcile -v
```

**DoD：** 无 topic 的 task 合并行为不变；有 `topics.task_id` 才 UPDATE topics。recovery_required **不**把 topic 标 merged。

---

#### Task 5.2: skip → `rejected`；archive 守卫

**Files:** `orch/commands/skip.py`、`orch/commands/topic.py` `topic_archive`

- enqueued+活跃 task → archive 失败 `topic_enqueue_active`
- skip 后可 archive
- merged 后可 archive；`cleanup --prune` 仍要 cooldown（可用 mock 时间或只断言 archive 不删目录）

```text
python -m unittest tests.test_acceptance_topic tests.test_acceptance_cleanup tests.test_phase4 -v
```

**DoD：** archive 不删 worktree；skipped topic 不被 prune（已有 `task_skipped_retained`）。

---

### Phase 6 — Skill、场景文档、一致性

**Depends on:** Phase 5 行为稳定。

#### Task 6.1: Skill + 场景 + skill 测试

**Files:**

- `skills/orchestrator/SKILL.md`（及仓库内 `.opencode/skills/orchestrator/SKILL.md` 若与测试同源则只改测试读取的那份：`tests/test_skill_consistency.py` 读 `skills/orchestrator/SKILL.md`）
- `docs/usage-scenarios.md` §4 流程图补 `topic-start` / `topic-ready` / `topic-enqueue`；§5 决策树加 Topic 节点
- `tests/test_skill_consistency.py` 增加 `"topic-enqueue"`，并保留 “topic-ready does NOT enqueue” 这句话

```text
python -m unittest tests.test_skill_consistency -v
```

**DoD：** Skill 写明：`topic-start` 供给隔离；`topic-ready` 不入队不合入；入队用 `topic-enqueue`；无 `--ship`；`/goal` 不是 Topic；删除仍用 `cleanup --prune`。

#### Task 6.2: 实现后回写架构现状（执行阶段才做）

实现完成后改 `docs/current-architecture.md` §8：删掉“没有 topic-enqueue / start 不建 worktree”。**本计划文档阶段只保留 §13 指针，不假装代码已闭环。**

---

## 9. 阶段 ↔ 测试对照表

| 阶段 | 必须通过的 unittest |
|---|---|
| 1 schema/state | `python -m unittest tests.test_migrations_v14 tests.test_migrations_v13 tests.test_migrations_v12 tests.test_topic_state -v` |
| 2 start 编排 | `python -m unittest tests.test_topic_closed_loop tests.test_phase4 tests.test_acceptance_enqueue tests.test_integration_flow -v` |
| 3 ready 证据 | `python -m unittest tests.test_topic_closed_loop tests.test_verification_v13 tests.test_phase4 -v` |
| 4 enqueue | `python -m unittest tests.test_topic_closed_loop tests.test_acceptance_topic tests.test_acceptance_enqueue -v` |
| 5 merge/archive | `python -m unittest tests.test_acceptance_topic tests.test_acceptance_cleanup tests.test_acceptance_freeze_and_conflict tests.test_interrupt_reconcile -v` |
| 6 docs/skill | `python -m unittest tests.test_skill_consistency -v` |
| 关门全量 | `python -m unittest discover -s tests -v` |

Phase 2 若 `test_topic_closed_loop` 尚无 Git 断言，至少包含幂等 start。Phase 3 起该模块必须含真实 git fixture。

---

## 10. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 嵌套 project lock 死锁 | 高 | 高 | 只抽 unlocked；单测 acquire 次数 |
| `is_v3_complete == 3` 导致升 4 后分类 ambiguous | 高 | 高 | Phase 1 先改 `>= 3` |
| ALTER `tasks` 破坏 v2 精确列集 | 高 | 高 | **不改 tasks 列**，用 `topics.task_id` |
| 旧测 `commit_sha="abc"` 全灭 | 高 | 中 | Phase 3 同步改 phase4 / verification 测 |
| `agent-start` 在无 Server 的单测里被默认调用 | 中 | 中 | 测试用 `provision_session=False`；公开 CLI 仍必须供给 session |
| 把 merge 当成 Done / deployed | 中 | 高 | Topic 停在 `merged`；promote 不在本方案 |
| 抄 wt merge / ship / Maestro | 中 | 高 | enqueue 只喂队列；无 `topic-ship` |
| ready 声明成功被当成“跑过测试” | 中 | 中 | Git 门禁强制；`--execute-commands` 可选真跑 |
| merge 回写漏掉 recover-as-merged | 中 | 高 | Task 5.1 覆盖 recover 路径 |
| UNIQUE worktree_path 阻止合法重建 | 低 | 中 | archived 行仍占 UNIQUE——归档不释放 name/branch/path；文档写明换 name 或将来再做 tombstone。**本方案不改 UNIQUE**（避免静默复用他人历史路径） |

---

## 11. 实现前检查

- [x] 行业对照已写：`docs/topic-industry-analysis.md`
- [x] 与 `1.3.0` 代码对过差距表
- [ ] 现有 `python -m unittest discover -s tests` 在改代码前为绿（执行者开工第一件事）
- [ ] 不修改 promotion / GitLab / pyproject
- [ ] 不把 `topic-ready` 改成自动 enqueue 或 merge
- [ ] 不把 `/goal`、sprint、OKR 做成 Topic

---

## 12. 规格自检

| 需求 | 任务 |
|---|---|
| start 供给 branch+WT+session；钉住 develop SHA；无隔离失败 | 2.1 + 2.3 |
| 幂等 `topic_id`；Git SoT；continue 而非 undo | 1.3 + 2.3 + §6 |
| 结构化 RFC/ADR/OpenSpec brief | 2.3 `--brief-file` |
| topic-ready：SHA+command；不入队不合入 | 3.1–3.2 |
| enqueue 相对当前 develop 再验；冻 SHA；锁 WT | 4.1–4.2 |
| 一 `active_run_id`；enqueue 后唯一 task | 2.2 + 4.2 |
| merge ≠ Done ≠ deployed；archive ≠ delete + cooldown | 5.1–5.2 |
| ready 然后 enqueue 两条命令；无 ship/Maestro | 4.2 + Skill |
| `/goal` / sprint / OKR 不是 Topic | §1.2 + Skill |
| 不改 GitLab / candidate_pr / pyproject | §1.2 |
| 映射 unittest | §9 |

无 TBD 占位。类型名：`worktree_add_unlocked` / `enqueue_unlocked` / `start_unlocked` / `topic_enqueue` / `append_event` 在后续任务中保持一致。

---

Plan complete and saved to `docs/topic-closed-loop-plan.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每任务一个新子代理，任务间审查  
2. **Inline Execution** — 本会话按 executing-plans 分批落地

实现前请先跑一遍全量 unittest，确认基线为绿。
