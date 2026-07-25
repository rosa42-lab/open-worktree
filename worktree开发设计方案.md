# 完整开发设计方案：多 Agent Worktree 编排系统（Orchestrator）

**版本**：v1.1（实现候选版，须通过第 17 章验收后方可标记可交付）  
**目标**：一次全局安装，支持任意数量项目，多个 Agent 并行开发同一仓库；系统强制按确定性顺序 `priority` 升序 → `submitted_at` 升序 → `queue_seq` 升序安全合入 `develop`，并提供 Agent 主动读取待合入代码的能力。  
**交付物**：一个可执行的全局 CLI 工具 `orch`（Python 3.10+ 标准库）+ 一个标准 Skill 文件。

---

## 1. 范围与核心原则

### 1.1 v1.1 范围声明（必须遵守）

- **运行模型**：单台主机上的多个本地进程。不涉及网络服务、远程节点、分布式协调。
- **实现语言**：Python 3.10+。
- **依赖**：仅 Python 标准库（`sqlite3`、`subprocess`、`argparse`、`pathlib`、`json`、`datetime`、`uuid`、`os`、`sys`、`socket`、`time`）。**禁止**新增第三方依赖。
- **目标分支**：`develop`。v1.1 硬编码为 `develop`，不可通过命令行或配置文件覆盖。
- **支持平台**：POSIX（Linux、macOS）与 Windows 10/11。两端的路径分隔符、文件锁、原子创建语义必须经过本设计的明确约束。
- **Git 模型**：每个项目使用 bare 仓库 `.bare.git` + linked worktrees。所有 Agent 共享同一裸仓库，避免重复克隆。
- **不引入 Web 服务、看板、Dashboard**；仅 CLI + 本地数据库。

### 1.2 核心原则

1. 每个项目拥有独立的 SQLite 数据库文件（物理隔离）。
2. 所有合入必须经过统一队列，按 `priority` 升序 → `submitted_at` 升序 → `queue_seq` 升序 的确定性顺序执行。
3. **禁止**任何 Agent 直接在 `main/` 中 `merge` 或 `commit`。所有合入操作只能通过 `orch <project> merge`。
4. **禁止**在 `main/` 中进行任何形式的冲突手动解决、人工 commit、`git add`、`git commit`。冲突的唯一恢复路径是 Agent 更新其源分支后调用 `retry`。
5. 所有检查命令（`pending` / `diff` / `changes` / `log`）只读，不得修改数据库或 Git 状态。
6. Agent 可主动读取所有待合入任务的真实代码变更（diff、文件列表、提交历史）。

### 1.3 强制边界

`orch` 对**通过 orch 发起的操作**提供队列、锁、状态机和审计约束，但它不是操作系统级沙箱。拥有项目文件系统写权限的进程仍可能绕过 orch，直接调用 `git update-ref`、删除锁文件或修改 SQLite。生产使用必须配合最小文件权限、独立运行账户或外部沙箱；文档中的“强制”不得解释为可抵御同权限恶意进程。

### 1.4 修订说明（v1.0 → v1.1）

- 移除"零侵入项目目录"的措辞。v1.1 的项目目录布局包含 `main/`、`worktrees/`、`.bare.git` 这些**项目根目录下的约定子目录**；用户级配置与数据库放在用户目录，项目内的目录是 Git 工作目录的一部分，并非侵入用户数据。`test/` 不再由 orch 创建。
- 修正"配置与 DB 全部放在用户目录"为：**配置与注册表**放用户目录，**每个项目的 SQLite 数据库**放用户目录 `~/.orchestrator/data/<project>/orchestrator.db`，**裸仓库与 worktrees** 必须在项目根目录内（Git 模型本身要求）。
- 引入跨平台项目锁、UUID 任务 ID、显式状态机、合并认领/执行/终结协议、保守的 `cleanup --prune`、原子 `config.json` 更新。
- 删除 `test` 子命令；列入未来工作。
- `Skill` 交付物的正文内容由本设计第 16 章完整定义，不依赖历史版本。

---

## 2. 项目目录布局

### 2.1 用户级目录（所有项目共享）

```
~/.orchestrator/
├── config.json                     # 全局项目注册表（原子更新）
├── config.json.lock                # 配置写入锁（详见 §6.4）
└── data/
    └── <project-name>/
        ├── orchestrator.db         # 项目级 SQLite 数据库
        ├── orchestrator.db-wal     # WAL 文件
        ├── orchestrator.db-shm     # WAL 共享内存文件
        └── project.lock            # 项目级互斥锁（详见 §6.3）
```

### 2.2 项目根目录（用户项目仓库内，由 `init` 建立）

```
<project-root>/
├── .bare.git/                      # 共享裸仓库（必须）
├── main/                           # 合入专用 worktree，强制常驻 develop
└── worktrees/                      # Agent 工作区根目录
    └── <agent>-<branch-safe>/      # 每个 Agent 的工作 worktree
```

`test/` 目录由 orch 创建的命令已从 v1.1 移除。测试功能列入未来工作（§18），由独立 Skill 接管。

### 2.3 命名约束

- `<project-name>`：匹配正则 `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`；必须唯一；大小写敏感。**任何不匹配的命令行参数必须被拒绝**（非 warning）。
- `<agent>`：匹配正则 `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`。
- `<branch-safe>`：来自源分支名，把 `/` 替换为 `__`，把 `..`、`@{`、控制字符、空格等不安全字符替换或拒绝。源分支名本身在入队时必须通过 `git check-ref-format --branch` 校验，校验失败必须拒绝入队。

---

## 3. 数据模型（SQLite）

### 3.1 数据库与连接配置

- 每个项目一个独立数据库文件：`~/.orchestrator/data/<project>/orchestrator.db`。
- 每次打开连接必须执行：
  ```python
  PRAGMA foreign_keys = ON;
  PRAGMA journal_mode = WAL;
  PRAGMA synchronous = NORMAL;
  PRAGMA busy_timeout = 5000;          # 5 秒
  PRAGMA temp_store = MEMORY;
  ```
- 写事务统一使用 **`BEGIN IMMEDIATE`**（立即获取写锁），避免 `SQLITE_BUSY` 升级失败。
- **禁止**在任何 `BEGIN ... COMMIT` 事务内执行 Git 子进程。事务必须短，仅包含 DB 写。

### 3.2 表 `tasks`

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | TEXT | PRIMARY KEY | UUID v4（`uuid.uuid4().hex`），不带前缀。 |
| `agent_name` | TEXT | NOT NULL | 提交 Agent 名称。 |
| `branch_name` | TEXT | NOT NULL | 源分支名（Git 引用全名）。 |
| `worktree_path` | TEXT | NOT NULL | 绝对路径，规范化后存储。 |
| `priority` | INTEGER | NOT NULL DEFAULT 1 | 数字越小优先级越高，必须 `>= 0`。 |
| `status` | TEXT | NOT NULL | 见 §4。 |
| `submitted_at` | TEXT | NOT NULL | ISO 8601 UTC。 |
| `source_commit` | TEXT | NOT NULL | 入队时记录的源分支 HEAD commit SHA。任务处于 `pending` / `merging` 时**冻结**，仅可由成功的 `retry` 更新为新 SHA。 |
| `target_head_before` | TEXT | NOT NULL | 入队时的 `develop` HEAD commit SHA。任务生命周期内**不可变**，是"原始合入目标"的事实来源。 |
| `target_commit_at_claim` | TEXT | NULL | `merge` 认领时刻的 `develop` HEAD commit SHA，用于崩溃恢复。失败认领、precheck 拒绝、终态完成时不写入。 |
| `queue_seq` | INTEGER | NOT NULL UNIQUE | 单调递增确定性序号（由 §3.5 计数器机制生成），`merge` 取队首的最终 tie-breaker。 |
| `claimed_at` | TEXT | NULL | merge 认领时间。 |
| `finished_at` | TEXT | NULL | merge 终结时间。 |
| `merged_commit` | TEXT | NULL | 合入后的 commit SHA（仅 merged）。 |
| `last_error` | TEXT | NULL | 最后一次失败的可读错误摘要。 |
| `conflict_files` | TEXT | NULL | 冲突时受影响文件列表（JSON 数组字符串，可空）。 |
| `attempts` | INTEGER | NOT NULL DEFAULT 0 | merge 尝试次数。 |
| `archived_at` | TEXT | NULL | `cleanup --prune` 归档时刻。`cleanup` 永不删除 task 行，仅写 `archived_at`。 |

`source_commit` 语义：
- `enqueue` 写入初始值 `git rev-parse <branch>`。
- 任务处于 `pending` 或 `merging` 时**冻结**：任何 DB 写入路径（包括 `merge`、`reset-stuck` 误用、并发冲突）不得修改它。`merge` 命令读取的源 SHA 直接来自该字段，绝不使用可变分支名。
- 唯一允许更新的路径是成功的 `retry`（§7.9）：旧 SHA 由 `retry` 路径读取并在 `audit_log.detail` 中以 `{old_source_commit, new_source_commit}` 记录。

`target_head_before` 语义：
- `enqueue` 写入 `git rev-parse develop`。
- **永久不可变**。即使 `retry` 也不修改。审计与回溯的唯一事实来源。

`target_commit_at_claim` 语义：
- 仅 `merge` 在 §5.2 Claim 阶段 DB 写入 `merging` 时同时写入。
- `reset-stuck` 用其与 `develop` HEAD 比对以区分"未开始 Do"、"Do 中崩溃"、"Finalize 前崩溃"。

### 3.3 表 `audit_log`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `task_id` | TEXT | 可空（项目级事件可空）。 |
| `action` | TEXT NOT NULL | 枚举：`enqueued`、`merge_claimed`、`merge_started`、`merge_succeeded`、`merge_aborted_conflict`、`merge_aborted_precheck`、`merge_aborted_recovery_required`、`retried`、`skipped`、`reset_stuck`、`cleanup_pruned`、`project_locked`、`project_unlocked`、`config_updated`、`invalid_transition`。 |
| `detail` | TEXT | JSON 字符串，可包含 commit SHA、错误摘要、路径、操作者。 |
| `created_at` | TEXT NOT NULL | ISO 8601 UTC。 |

`action` 必须使用上述枚举字面量之一；新增动作必须同时扩展枚举与文档。

### 3.4 索引

```sql
CREATE INDEX idx_tasks_status_priority
  ON tasks(status, priority, submitted_at);

CREATE INDEX idx_tasks_status_seq
  ON tasks(status, queue_seq);

CREATE INDEX idx_tasks_branch
  ON tasks(branch_name);

-- 保证同一分支至多一个未完成任务；仅终态 'skipped'/'merged' 不受限
CREATE UNIQUE INDEX idx_tasks_branch_active
  ON tasks(branch_name)
  WHERE status IN ('pending', 'merging', 'conflict', 'recovery_required');
```

`merge` 取队首的最终查询固定使用排序键 `(priority ASC, submitted_at ASC, queue_seq ASC)`，命中 `idx_tasks_status_priority` 与 `idx_tasks_status_seq`。

### 3.5 计数器表 `counters`

`queue_seq` 必须由 DB 端单调计数器生成，避免 `MAX(queue_seq)+1` 在并发入队下的竞争漏洞。

```sql
CREATE TABLE counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

-- 启动时确保存在
INSERT OR IGNORE INTO counters(name, value) VALUES ('queue_seq', 0);
```

`enqueue` 写入 `queue_seq` 的路径（在短 `BEGIN IMMEDIATE` 事务内）：

```sql
UPDATE counters SET value = value + 1 WHERE name = 'queue_seq';
INSERT INTO tasks(..., queue_seq, ...) VALUES (..., (SELECT value FROM counters WHERE name = 'queue_seq'), ...);
```

`queue_seq` 是项目级单调递增，不回收、不重用，跨任务生命周期保留。

---

## 4. 状态机

### 4.1 状态定义

| 状态 | 含义 | 是否终态 | 是否阻塞队列 |
|------|------|----------|--------------|
| `pending` | 已入队，等待 merge。 | 否 | 否 |
| `merging` | 已被 `merge` 认领，正在执行。 | 否 | 否 |
| `merged` | 已成功合入 `develop`。 | 是 | 否 |
| `conflict` | Git `merge` 返回冲突；任务被冻结。 | 否（需 `retry` 或安全 `skip`） | 是 |
| `recovery_required` | Git 状态无法安全恢复（post-check 失败、`merge --abort` 失败、`main/` 残留合并状态等）。需要操作员介入。 | 否（需 `reset-stuck` 或人工修复） | 是 |
| `skipped` | 被 `skip` 命令显式跳过。 | 是 | 否 |

阻塞语义：
- `conflict` 与 `recovery_required` **都是阻塞状态**。
- 存在任何 `conflict` 或 `recovery_required` 任务时，`merge` 必须**拒绝**执行并返回退出码 5，提示先 `retry`（冲突）或人工检查（recovery）。
- `merge` 在 §5.2 Claim 步骤 3 中跳过 `conflict`、`recovery_required` 与所有终态。

### 4.2 合法迁移

| 从 | 到 | 触发动作 | 守卫 |
|----|----|----------|------|
| — | `pending` | `enqueue` | §7.6 四项校验 |
| `pending` | `merging` | `merge` Claim 成功（precheck 通过） | `conflict`/`recovery_required` 不存在 |
| `merging` | `merged` | `merge` Do 成功且 post-check 通过 | `source_commit` 是 `develop` 祖先；`main/` 干净 |
| `merging` | `conflict` | `merge` Do 返回冲突；冲突文件已捕获 | `git merge --abort` 成功 |
| `merging` | `recovery_required` | post-check 失败；或 `merge --abort` 失败；或 `main/` 残留合并状态无法恢复 | 见 §5.5、§5.6 |
| `pending` | `skipped` | `skip` 命令 | 任务当前 `pending` |
| `conflict` | `skipped` | `skip` 命令 | merge 已成功 abort，`main/` 在 `develop` 且干净、无 `MERGE_HEAD` |
| `conflict` | `pending` | `retry` 成功 | §7.9 全部 Git 校验通过 |
| `merging` | `pending` | `reset-stuck` 证据化恢复 | §7.11 全部分支判定为"未开始 Do" |
| `merging` | `merged` | `reset-stuck` 证据化恢复 | §7.11 判定为"Do 已成功未终结" |
| `merging` | `recovery_required` | `reset-stuck` 保守路径 | §7.11 判定为"Git 状态不可信" |
| `recovery_required` | `pending` | `reset-stuck` 修复后恢复 | 操作员先修复 Git 后再跑 `reset-stuck` |

任何其他迁移必须被代码层拒绝并写入 `audit_log(action='invalid_transition')`。

### 4.3 冲突策略（与 §5.4 一致）

- Git `merge` 返回非零且 stderr 表明冲突：`main/` 仍处于合并中，**先**捕获 `git diff --name-only --diff-filter=U` 输出到 `conflict_files`，**再**尝试 `git merge --abort`（§5.5），成功后任务状态置 `conflict`；`merge --abort` 失败则置 `recovery_required`。
- **只要存在任何 `conflict` 或 `recovery_required` 任务，`merge` 必须拒绝执行并返回退出码 5**，直到 `retry` 把 `conflict` 转回 `pending` 或 `reset-stuck` 把 `recovery_required` 转回 `pending`/`merged`。
- **禁止**在 `main/` 中手动 `git add` / `git commit` 解决冲突。人工 commit 仅允许在 Agent 自己的源 worktree 中进行。

---

## 5. 合并协议（认领 / 执行 / 终结）

### 5.1 协议总览

`merge` 命令实现一个三段协议：

1. **Precheck（前检）**：项目锁内，仅 Git 读；不修改 DB，不修改任务状态。
2. **Claim（认领）**：项目锁内、单个 `BEGIN IMMEDIATE` 事务里选取下一任务并标记 `merging`，记录 `target_commit_at_claim`。
3. **Do（执行）**：仍在项目锁内，事务已提交，在 `main/` 中执行 `git merge --no-ff --no-edit <source_commit>`。
4. **Finalize（终结）**：在新的 `BEGIN IMMEDIATE` 事务里根据结果写回 `merged`、`conflict` 或 `recovery_required`。

`merge` 在 Do 阶段操作的源对象**始终是 DB 中存储的 `source_commit`**，**绝不**使用分支名或当前 HEAD。分支名仅用于 §7.6 入队校验与 §7.9 retry 校验。

### 5.2 Precheck 阶段

1. 项目锁已获取（详见 §6）。
2. 仅 Git 读，**不写 DB**：
   - `main/` 存在。
   - `git -C main/ rev-parse --git-common-dir` 指向项目根的 `.bare.git`。
   - `git -C main/ rev-parse --abbrev-ref HEAD` == `develop`。
   - `git -C main/ status --porcelain` 为空。
   - `git -C main/ rev-parse --git-path MERGE_HEAD` 指向的文件不存在（即不在合并中）。
   - 读取 `git -C main/ rev-parse develop`，保存为内存变量 `target_commit_for_claim`。该 Git 命令必须在 DB 事务外完成。
3. 任一不满足：
   - 写入 `audit_log(action='merge_aborted_precheck', detail={reason})`。
   - **不修改任何任务状态**（任务仍为 `pending`）。
   - 释放项目锁，退出码 4。

### 5.3 Claim 阶段

1. Precheck 通过后，在 `BEGIN IMMEDIATE` 事务内：
   - 检查是否存在任何 `status='conflict'` 或 `status='recovery_required'` 任务；若存在，写入审计，退出码 5。
   - 选取 `status='pending'` 中 `priority` 升序 → `submitted_at` 升序 → `queue_seq` 升序 的第一条；更新为 `status='merging'`，写入 `claimed_at`，将 Precheck 已读取的 `target_commit_for_claim` 写入 `target_commit_at_claim`，`attempts += 1`。
   - 写入 `audit_log(action='merge_claimed')`。
   - 提交事务。
2. 若无 pending 任务，退出码 0 并报告 "no pending tasks"。
3. Claim 成功后，DB 仍记录 `source_commit`（冻结），后续 Do/Finalize/Recovery 都**只读**该字段。

### 5.4 Do 阶段

1. 项目锁保持持有。
2. **写入** `audit_log(action='merge_started')`（短 DB 写，独立事务）。
3. 在 `main/` 中执行：
   ```
   git -C main/ merge --no-ff --no-edit <source_commit>
   ```
   `<source_commit>` 取自 `tasks.source_commit`（§3.2 冻结字段）。**绝不**传入 `<branch>`。
4. 必须等待子进程返回。检测返回码：
   - **0**：进入 §5.5。
   - **非 0**：进入 §5.6。

### 5.5 成功路径（post-check + Finalize）

1. 读取 `git -C main/ rev-parse HEAD` 作为候选 `merged_commit`。
2. Post-merge verification：
   - `git -C main/ status --porcelain` 为空。
   - `git -C main/ rev-parse --git-path MERGE_HEAD` 不存在。
   - `git --git-dir=.bare.git merge-base --is-ancestor <source_commit> develop` 返回 0。
   - 任一不满足 → 进入 §5.7（recovery_required）。
3. 在新 `BEGIN IMMEDIATE` 事务中：状态置 `merged`，写入 `merged_commit`、`finished_at`，清空 `last_error`、`conflict_files`，写入 `audit_log(action='merge_succeeded')`。

### 5.6 失败路径：Git 冲突（conflict）

1. 仅当 stderr/stdout 表明"merge conflict"（含 `CONFLICT` 字样且返回码非 0 且 `MERGE_HEAD` 存在）时走此路径。其他非零返回（pre-merge hook 失败、ref 缺失等）走 §5.7。
2. **先**捕获 `git -C main/ diff --name-only --diff-filter=U` 的输出到内存。失败也不中断流程。
3. 尝试 `git -C main/ merge --abort`。该命令**仅在 `MERGE_HEAD` 存在时调用一次**；失败（返回非 0 或抛错）立即进入 §5.7。
4. `merge --abort` 成功：
   - 在新 `BEGIN IMMEDIATE` 事务中：状态置 `conflict`，写入 `last_error`、`conflict_files`（步骤 2 捕获的列表，若不可用则为空数组）、`finished_at`，`attempts` 保持递增；写入 `audit_log(action='merge_aborted_conflict', detail={conflict_files: [...]})`。

### 5.7 失败路径：不可安全恢复（recovery_required）

触发条件（任一）：
- `merge` 返回非零但不属于冲突（pre-merge hook 拒绝、ref 不存在等）。
- `merge --abort` 返回非零或抛错。
- §5.5 post-check 失败。
- `main/` 处于合并状态且无法 `merge --abort` 退出（`MERGE_HEAD` 残留、`index.lock` 存在、`status --porcelain` 非空且不可恢复）。

行为：
1. **保留项目锁**直到本命令退出；不重试，不切换任务。
2. 在 `BEGIN IMMEDIATE` 事务中：
   - 状态置 `recovery_required`（仅限 `merging` → `recovery_required`）。
   - 写入 `last_error`、`finished_at`，**保留** `conflict_files`（若已捕获）。
   - 写入 `audit_log(action='merge_aborted_recovery_required', detail={reason, conflict_files?})`。
3. 命令退出码 8。
4. **禁止**本命令自动调用 `git reset --hard`、`git checkout --` 等破坏性恢复动作。

### 5.8 跨进程并发语义

- 项目锁保证同一项目**至多一个** `merge` 实例在 Do/Finalize 阶段运行。
- 同一项目内只读命令（`pending` / `diff` / `changes` / `log` / `list`）可并发运行。
- **任何变更类命令**（`enqueue` / `merge` / `retry` / `skip` / `reset-stuck` / `cleanup --prune` / `worktree-add` / `init`）都需获取项目锁，相互**串行**。
- 不同项目之间无互斥关系。

### 5.9 崩溃点与恢复

| 崩溃点 | DB 残留状态 | Git 状态 | 恢复策略 |
|--------|-------------|----------|----------|
| Precheck 失败 | `pending` 不变 | 不变 | 修复后重跑 `merge`。 |
| Claim 写 `merging` 后、Do 前 | `merging` | `main/` 不变 | §7.11 reset-stuck：`main/` 干净 + HEAD == develop → 转 `pending`。 |
| Do 中（子进程挂起） | `merging` | `main/` 可能处于合并中 | §7.11：若 `MERGE_HEAD` 存在，尝试 `merge --abort` 后按 §5.6/§5.7 决定 `conflict` / `recovery_required`；若不存在则按上面一行处理。 |
| Do 成功、Finalize 前 | `merging` | `develop` 已含 `source_commit` | §7.11：post-check 通过 → 转 `merged`；否则 → `recovery_required`。 |
| Finalize 中 | `merging` | 同上 | 同上。 |
| SIGINT/KeyboardInterrupt（§11） | 可能 `merging` | 可能合并中 | 见 §11 三步清理。 |

`reset-stuck` 不能盲改 DB 状态：必须先查 Git HEAD 是否实际包含 `source_commit` 与 `main/` 状态，再决定写回 `merged`、`pending` 或 `recovery_required`。

---

## 6. 跨平台项目锁与配置锁

### 6.1 锁文件位置

- 项目锁：`~/.orchestrator/data/<project>/project.lock`
- 配置锁：`~/.orchestrator/config.json.lock`

### 6.2 协议：单一可移植锁文件

v1.1 的锁协议**仅**依赖 `os.open` 的原子独占创建与文件 unlink。**不**使用 `fcntl`、`msvcrt`、`OpenProcess` 等平台特定原语。配置锁与项目锁使用同一协议。

```
acquire(lock_path, owner_payload):
    1. fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    2. on FileExistsError:
           existing = read_json_or_null(lock_path)
           # 所有持锁命令均禁止重入；阻塞重试（含抖动），不删除、不覆盖
           sleep_with_jitter(); goto 1
    3. write_json(fd, owner_payload)        # owner_payload 见 §6.3
    4. os.fsync(fd)                          # 元数据持久化
    5. close(fd) if dup'd; keep path alive   # 持有的是路径 + owner_token，不是 fd
    return owner_token
```

```
release(lock_path, owner_token):
    # 仅当文件仍存在且 owner_token 匹配时才 unlink
    if not exists(lock_path): return
    existing = read_json_or_null(lock_path)
    if existing and existing["token"] == owner_token:
        os.unlink(lock_path)
    # 否则：保留文件（异常终止场景，下次 acquire 会处理）
```

要点：
- **永不**对锁文件调用 `os.remove` 作为常规路径。仅 `lock-break --force`（§6.6）允许。
- **永不**依赖 `fcntl.flock` / `msvcrt.locking`。
- 进程异常崩溃：文件保留；下次 acquire 由 `lock-status` 提示，必要时 `lock-break`。

### 6.3 owner 元数据

```json
{
  "token": "<uuid4-hex>",
  "pid": 12345,
  "hostname": "<gethostname()>",
  "started_at": "2026-07-25T08:00:00Z",
  "command": "merge",
  "project": "alpha"
}
```

`token` 用于 release 校验；`pid`+`hostname` 仅供 `lock-status` 显示与 `lock-break` PID 活性判断。

### 6.4 PID 活性判断（lock-break 用）

`lock-break` 需要判断锁文件记录的进程是否仍在运行。v1.1 使用**同主机可移植**方法：

- POSIX：`os.kill(pid, 0)` 返回 0 表示进程存在（不论权限如何）；PermissionError 视为存在；`ProcessLookupError` 视为不存在。
- Windows：通过解析 `tasklist`（POSIX `ps` 不存在）输出匹配 PID；或调用 `subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], ...)` 并匹配 stdout。
- 跨主机/容器/CI：若 `hostname` 与当前不一致，直接视为不可判断。

约束：
- **若 PID 活性无法确定**（`tasklist` 不可用、查询失败、平台限制、`hostname` 不匹配），`lock-break` 必须**拒绝**并退出码 6，要求操作员显式人工检查。**禁止**在此情况下假设"已退出"。

### 6.5 stale 与自动删除

**v1.1 不实现自动 stale 检测与删除**。所有锁文件仅在以下情况被删除：
1. 正常 `release`（§6.2）。
2. 显式 `lock-break --force` 且 PID 活性可证明为不存在（§6.4）。

### 6.6 `lock-status` / `lock-break`

- `orch <project> lock-status [--json]`：打印 token、pid、hostname、started_at、命令名。不获取锁。
- `orch <project> lock-break [--force]`：
  1. **不**走配置锁协议（**修订 §6.7**：lock-break 不使用配置锁）。
  2. 读取锁文件内容。
  3. 若锁文件不存在：返回 ok（已无锁）。
  4. 比对 `hostname`：不匹配则拒绝并退出码 6（不在本机）。
  5. 用 §6.4 方法判定 PID 活性：
     - 进程存在 → 拒绝，退出码 6。
     - 进程不存在 → 删除锁文件，写 `audit_log(action='project_unlocked', detail={token, pid, reason: 'pid_dead'})`，返回 ok。
      - **活性无法确定** → 拒绝，退出码 6，要求操作员检查。要求 `operator_inspection: true` 的 stderr 信息。

所有常规持锁命令等待项目锁或配置锁的最长时间固定为 30 秒；超时后返回退出码 6，并提示运行 `lock-status`。禁止无限等待，也禁止常规命令自行删除锁文件。

### 6.7 配置 JSON 原子更新

`config.json` 使用**同一**锁协议（§6.2）获取 `config.json.lock`。`lock-break` 本身不获取配置锁（§6.6）。

写入流程：
1. 获取 `config.json.lock`（§6.2）。
2. 写临时文件 `config.json.tmp`。
3. `os.replace(config.json.tmp, config.json)`（POSIX 与 Windows 均为原子）。
4. 释放 `config.json.lock`。

---

## 7. 命令规范（CLI）

### 7.1 顶层

- 全局：`orch project list|add|remove`
- 项目内：`orch <project> <command> [...]`
- 通用标志：`--json`（除已明确支持命令外，所有命令必须支持 `--json`，输出符合 §10）。

### 7.2 项目管理

```
orch project list [--json]
orch project add <name> <path>      # 配置锁内：路径规范化 + 唯一性校验 + 原子写入
orch project remove <name>          # 配置锁内：拒绝项目 DB 仍处于 locked 状态
```

`add` 必须：
1. 解析 `path` 为绝对路径并 `Path.resolve()`。
2. 拒绝 `.bare.git` 不存在的项目。
3. 拒绝同名项目。
4. 写入 `config.json`（原子）。

`remove` 必须：
1. 拒绝项目 DB 存在锁文件。
2. 仅从 `config.json` 删除映射；**不删除**用户级 `data/<project>/`。v1.1 不提供 `--purge`，数据清除必须由操作员在确认无锁且完成备份后手动执行。

### 7.3 项目操作

```
orch <project> init
orch <project> worktree-add <agent> <branch> [--base develop]
orch <project> enqueue <agent> <branch> <worktree_path> [--priority N]
orch <project> list [--all] [--json]
orch <project> pending [--json]
orch <project> diff <task_id|branch>
orch <project> changes <task_id|branch>
orch <project> log <task_id|branch>
orch <project> merge [--once]
orch <project> retry <task_id>
orch <project> skip <task_id> [--reason ...]
orch <project> reset-stuck
orch <project> cleanup [--prune]
orch <project> lock-status [--json]
orch <project> lock-break [--force]
```

`merge --once` 仅处理一个任务后退出（用于 CI）。

### 7.4 `init`

- 前置条件：项目已通过 `orch project add` 注册；项目根中的 `.bare.git` 已存在且 `git --git-dir=.bare.git rev-parse --is-bare-repository` 返回 `true`；`refs/heads/develop` 已存在且指向有效 commit。任一不满足均拒绝，`init` 不负责创建裸仓库或首个 commit。
- 创建 `main` worktree（若不存在）：`git --git-dir=.bare.git worktree add main develop`。若已存在，必须验证其属于当前 `.bare.git`、当前分支为 `develop` 且工作区干净；验证失败拒绝。
- 创建 `worktrees/` 目录。
- 在用户级目录创建 `data/<project>/orchestrator.db` 并初始化 schema。
- `init` 不接受目标分支参数；`develop` 在 v1.1 中硬编码。

### 7.5 `worktree-add`

- 校验 `<agent>`、`<branch>` 命名；`<branch>` 必须通过 `git check-ref-format --branch`。
- 若 `<branch>` 不存在，执行 `git --git-dir=.bare.git worktree add -b <branch> worktrees/<agent>-<branch-safe> <base>`，其中 `<base>` 默认且在 v1.1 只允许 `develop`。
- 若 `<branch>` 已存在，执行 `git --git-dir=.bare.git worktree add worktrees/<agent>-<branch-safe> <branch>`；分支已被其他 worktree 检出时拒绝，不使用 `--force`。
- 记录 `worktree_path` 绝对路径。

### 7.6 `enqueue`

必须执行以下**五项**校验（顺序固定，任一失败立即拒绝并退出码 7）：

1. **存在性 + 有效性**：`<worktree_path>` 存在且 `git -C <worktree_path> rev-parse --git-common-dir` 解析到项目 `.bare.git`；`git -C <worktree_path> rev-parse --abbrev-ref HEAD` 等于 `<branch>`。
2. **干净性**：`git -C <worktree_path> status --porcelain` 为空。
3. **分支存在**：`git --git-dir=.bare.git rev-parse --verify <branch>` 返回 0。
4. **唯一性**：`UNIQUE INDEX idx_tasks_branch_active` 保证同一分支至多一个 `pending|merging|conflict|recovery_required` 任务。
5. **非空变更**：`git --git-dir=.bare.git rev-list --count develop..<source_commit>` 必须大于 0；没有待合入 commit 的分支拒绝入队。

附加：
- 所有 Git 校验及 `source_commit = git rev-parse <branch>`、`target_head_before = git rev-parse develop` 的读取必须在 DB 事务外完成。
- 随后在一个短 `BEGIN IMMEDIATE` 事务内按 §3.5 递增计数器、插入任务并写 `enqueued` 审计。唯一索引冲突返回退出码 7。
- 禁止通过 `--force` 绕过唯一性或所有权检查；v1.1 的 `enqueue` 不提供该参数。

### 7.7 只读命令

- `pending [--json]`：列出 `status='pending'` 任务；人类可读输出对每个任务使用其存储的 `source_commit`，包含 `git diff --stat develop...<source_commit>` 与 `git log --oneline develop..<source_commit> -n 5`。
- `diff <task_id|branch>`：`git diff develop...source_commit`（使用入库时的 source_commit 而非当前 HEAD，确保 diff 反映**入队时**的代码）。
- `changes <task_id|branch>`：文件列表（`git diff --name-status develop...source_commit`）+ stat + log。
- `log <task_id|branch>`：`git log develop..source_commit`（注意是两点语法）。

`task_id|branch` 解析规则：先按参数原文执行 task 主键精确查询；命中即使用该任务。未命中时，参数必须通过 `git check-ref-format --branch`，然后按 `branch_name` 查询。若同一分支存在多个历史任务，优先选择唯一的未完成任务；若只有多个终态任务且调用方未给 task ID，则拒绝歧义并列出候选 task ID。禁止按字符串形状猜测 UUID，也禁止在未查库时直接把输入交给 Git。

### 7.8 `merge`

详见 §5。

### 7.9 `retry`

- 仅允许 `status='conflict'` 任务。
- Agent 必须先在自己的 worktree 中自行把当前 `develop` 合入或 rebase 到源分支、解决冲突并 commit。`retry` **只验证和登记，不执行任何 Git 写命令**。
- 获取项目锁后，在 DB 事务外依次验证：worktree 所有权正确；worktree 干净；worktree 当前分支等于任务的 `branch_name`；裸仓库中的分支 HEAD 等于 worktree HEAD；新 HEAD `new_source_commit` 不等于旧 `source_commit`；`git --git-dir=.bare.git merge-base --is-ancestor develop <new_source_commit>` 返回 0。
- 任一验证失败：状态保持 `conflict`，不修改任何任务字段，返回退出码 7。
- 验证通过后，在短 `BEGIN IMMEDIATE` 事务中再次确认任务仍为 `conflict`，更新 `source_commit = new_source_commit`，清空 `target_commit_at_claim`、`claimed_at`、`finished_at`、`last_error`、`conflict_files`，`attempts` 重置为 0，状态置 `pending`；`target_head_before` 保持不变；写入 `audit_log(action='retried', detail={old_source_commit, new_source_commit})`。

### 7.10 `skip`

- 允许 `pending` 或 `conflict` 任务；禁止跳过 `merging`、`recovery_required` 或终态任务。
- 跳过 `conflict` 前必须再次验证 `main/` 位于 `develop`、工作区干净且不存在 `MERGE_HEAD`。任一不满足时拒绝，并要求先走 `reset-stuck` 恢复 Git 状态。
- 状态置 `skipped`，写入 `audit_log(action='skipped', detail={reason})`。
- 不释放 worktree（Agent 自行清理）。

### 7.11 `reset-stuck`

**证据化恢复**，禁止盲改：

1. 获取项目锁，列出所有 `status IN ('merging', 'recovery_required')` 的任务；同一项目正常情况下至多一条。
2. 对每个任务读取 `source_commit`、`target_commit_at_claim` 与当前 `develop` HEAD，并检查 `main/` 分支、工作区、`MERGE_HEAD`。
3. 判定顺序固定：
   - 当前 `develop` 包含 `source_commit`，且 `main/` 在 `develop`、干净、无 `MERGE_HEAD`：转 `merged`，把当前 HEAD 写入 `merged_commit`，写 `reset_stuck{recovered_as:'merged'}`。
   - 当前 HEAD 等于 `target_commit_at_claim`，且 `main/` 在 `develop`、干净、无 `MERGE_HEAD`：说明 Do 尚未生效或已完整回滚，转 `pending`，清空 claim 字段，写 `reset_stuck{recovered_as:'pending'}`。
   - 存在 `MERGE_HEAD`：先采集冲突文件，只允许尝试一次 `git -C main/ merge --abort`。abort 后恢复到 `target_commit_at_claim` 且干净时，若已确认是冲突则转 `conflict`，否则转 `pending`；abort 失败则保持/转为 `recovery_required`。
   - 其他情况：状态置或保持 `recovery_required`，写 `reset_stuck{recovered_as:'manual_required'}`，输出 HEAD、MERGE_HEAD、status 和预期 target SHA；禁止自动 reset/checkout。
4. `reset-stuck` 不处理 `conflict` 任务。冲突只能由 `retry` 恢复。

### 7.12 `cleanup [--prune]`

不带 `--prune`：列出可清理的 worktree（merged 后 24h 冷却期，v1.1 冷却期取保守值；v1.1 实际仅显示清单，不执行删除）。

带 `--prune`：

1. 项目锁内。
2. 对每个 merged 任务对应的 worktree（路径来自 DB）：
   - 校验 `git rev-parse --git-common-dir` 仍属于本项目 `.bare.git`。
   - 校验 `git status --porcelain` 为空。
   - 解析 `git --git-dir=.bare.git worktree list --porcelain`：该路径必须恰好注册一次，且该分支不得被任何其他 worktree 引用。
   - 若 Git worktree 处于 locked 状态，拒绝并提示操作员确认后运行 `git worktree unlock <path>`；不得将 Git worktree lock 与 orch 的 `project.lock` 混为一谈。
3. 在移除 worktree **之前**读取分支当前 SHA 为 `branch_tip`，执行 `git --git-dir=.bare.git merge-base --is-ancestor <branch_tip> develop`；非 0 时拒绝清理并保持 worktree 与任务不变。
4. ancestry 校验通过后，执行 `git --git-dir=.bare.git worktree remove <worktree>`，**不使用 `--force`**。失败则保留该任务，不执行后续分支或 DB 操作。
5. 执行 `git --git-dir=.bare.git update-ref -d refs/heads/<branch> <branch_tip>`，用预期旧 SHA 防止校验后分支被并发改变。禁止直接使用无 ancestry 保护的 `branch -D`。若 compare-and-delete 失败，记录 `last_error`；下次 cleanup 在确认 worktree 已不存在、分支仍等于已记录的 `branch_tip` 且 ancestry 校验通过后，只重试此删除步骤。
6. 执行 `git --git-dir=.bare.git worktree prune`。
7. Git 步骤全部成功后，短事务更新 `archived_at` 并写 `cleanup_pruned`。task 行和 audit_log 永久保留；不删除 DB 行。
8. 任一校验或 Git 步骤失败，当前任务保持未归档并报告原因；不得影响其他候选任务。**禁止**任何"先 DB 后 Git"的顺序。

### 7.13 `lock-status` / `lock-break`

见 §6.6。

---

## 8. Git 子进程封装

### 8.1 拆分原则

- **bare-repo/ref-only 命令**：仅操作 `.bare.git` 内部对象与引用，可用 `git --git-dir=<bare> ...`。例：`rev-parse --verify`、`branch -d`、`worktree list --porcelain`、`worktree prune`、`check-ref-format`、`update-ref`。
- **linked-worktree/index 命令**：操作工作区或索引，**必须**通过 `cwd=<worktree_path>` 让 Git 读取 `.git` 文件解析。**禁止**对这些命令使用 `--git-dir=<bare>`。
  - 例外：`worktree add/remove/list` 因为 Git 设计上要求 `--git-dir=<bare>`，属于第一类。
- 实现上至少两个封装：
  - `run_git_ref(args, bare_path)`：bare 仓库类。
  - `run_git_worktree(args, worktree_path)`：worktree 内。

`git check-ref-format --branch` 不依赖仓库，使用独立 `run_git_global(args)`；不得为了执行它伪造 `--git-dir`。所有 `git -C` 示例在 Python 中等价为 `cwd=<path>` + 不带 `--git-dir` 的 argv。

### 8.2 子进程调用约束

- `subprocess.run(args, cwd=..., shell=False, check=False, text=True, capture_output=True, timeout=...)`。
- `args` 必须是 list；**禁止** `shell=True`。
- 参数中含用户输入的（如分支名、路径）必须先经过命名/路径校验再拼入 `args`。
- 超时默认 60 秒，可由常量调整；超时即视为失败。
- 合并子进程必须单独保存进程句柄以响应 SIGINT/KeyboardInterrupt，不得使用无法终止子进程的 fire-and-forget 调用。

### 8.3 路径规范化

- 所有用户输入的路径（项目路径、worktree 路径、Agent 名）必须 `Path(...).expanduser().resolve()` 后再使用。
- 拒绝包含 `..`、空字节、控制字符的路径。

### 8.4 worktree 所有权校验

- 任何写命令使用 worktree 前：
  - `canonical = git -C <worktree_path> rev-parse --git-common-dir`（返回绝对路径）。
  - 与本项目 `bare` 路径 `resolve()` 后严格相等。
- 注册表中 worktree 必须出现在 `git --git-dir=<bare> worktree list --porcelain` 输出中；否则视为孤儿。

---

## 9. JSON 与退出码

### 9.1 退出码（必须遵守）

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 一般失败 |
| 2 | 使用错误（参数缺失或非法） |
| 3 | 项目未注册 |
| 4 | precheck 失败（如 main 不在 develop、不干净） |
| 5 | 队列阻塞（存在 `conflict` 或 `recovery_required`） |
| 6 | 锁仍存活，拒绝 lock-break |
| 7 | 入队校验失败 |
| 8 | Git 子进程失败 |
| 9 | DB 错误 |

### 9.2 JSON 信封

所有 `--json` 输出与所有错误输出统一信封：

```json
{
  "schema_version": 1,
  "ok": true,
  "command": "alpha.merge",
  "data": { ... },
  "error": null
}
```

错误：

```json
{
  "schema_version": 1,
  "ok": false,
  "command": "alpha.enqueue",
  "error": {
    "code": 7,
    "kind": "enqueue_validation_failed",
    "message": "worktree is not clean",
    "details": { "porcelain": "?? temp.txt" }
  },
  "data": null
}
```

`schema_version` 是 `INTEGER`，v1.1 取 `1`；未来破坏性变更递增。

### 9.3 强制审计事件（P0）

以下事件必须写入 `audit_log`：

- `enqueued`、`merge_claimed`、`merge_started`（在 Do 阶段开始时）、`merge_succeeded`、`merge_aborted_conflict`、`merge_aborted_precheck`、`merge_aborted_recovery_required`、`retried`、`skipped`、`reset_stuck`、`cleanup_pruned`、`project_locked`、`project_unlocked`、`config_updated`、`invalid_transition`。

每个事件必须包含 `task_id`（项目级事件可空）与 JSON 化 `detail`。

---

## 10. 错误处理矩阵

| 场景 | 退出码 | 行为 |
|------|--------|------|
| 项目未注册 | 3 | stderr 输出 `orch project add <name> <path>` 提示 |
| `.bare.git` 缺失 | 1 | 拒绝并要求 `init` |
| `main/` 缺失或不干净 | 4 | merge 拒绝 |
| 存在 `conflict` 或 `recovery_required` 任务 | 5 | merge 拒绝，分别提示 retry 或 reset-stuck/人工检查 |
| 入队校验失败（4 项任一） | 7 | 详细列出失败项 |
| 锁仍存活，lock-break | 6 | 拒绝 |
| Git 子进程非零退出 | 8 | 若任务已处于 merging，按 §5.6/§5.7 终结状态；否则写审计且任务保持不变 |
| DB 错误 | 9 | 写入 audit_log（如可能），回滚事务 |

所有错误路径必须：
1. 在 DB 层完成事务回滚或终结。
2. 释放所有已获取的项目锁（`finally`）。
3. 输出 JSON 或人类可读错误（取决于是否 `--json`）。
4. 尽力把 `main/` 恢复为干净 `develop`；若无法证明恢复成功，必须把任务置为 `recovery_required` 并输出可核对证据，不得谎报已清理。

---

## 11. 安全规则

- 不引入网络服务；所有数据本地。
- 锁文件权限 `0o600`（POSIX）。
- 路径校验拒绝 `..`、空字节、控制字符。
- 子进程 `shell=False`，参数 list 化。
- 项目名、Agent 名、分支名、worktree 路径均经过正则校验。
- 拒绝任何形式的 `--unsafe-bypass` / `--force-conflict-resolve` 等隐式开关。
- **禁止**在 `main/` 中执行任何 `git add` / `git commit` / `git merge`（除 §5.4 中明确由 merge 命令发起的 `merge --no-ff --no-edit <source_commit>` 与 §5.6/§11 中受控的 `merge --abort`）。
- **禁止**任何命令隐式调用 `push` 或远端操作。

### 11.1 SIGINT / KeyboardInterrupt

`merge` 收到 SIGINT 或 Python `KeyboardInterrupt` 时必须按以下顺序处理：

1. 若 Git 子进程仍运行，先向子进程发送终止信号并等待；超时后强制终止，再等待进程退出。禁止在子进程仍写 index 时执行恢复。
2. 检查 `MERGE_HEAD`。若存在，先采集冲突文件，再尝试一次 `git merge --abort`；若不存在，执行与 §7.11 相同的 HEAD/status 对账。
3. abort 后已恢复到 `target_commit_at_claim` 且干净时，任务回到 `pending`；已包含 `source_commit` 且 post-check 通过时转 `merged`；其他情况转 `recovery_required`。
4. 状态和审计写入完成或 DB 写入失败已明确报告后，才在 `finally` 中释放项目锁。进程退出码为 130。

---

## 12. 安装与初始化

### 12.1 POSIX（Linux/macOS）

```bash
mkdir -p ~/.local/bin
# 将 orch 脚本写入 ~/.local/bin/orch
chmod 700 ~/.local/bin/orch
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc   # 或 ~/.zshrc
. ~/.bashrc

# 前置准备：项目根必须已有 .bare.git，且其中已有 develop commit
cd /path/to/project
git clone --bare /path/to/existing/source .bare.git
git --git-dir=.bare.git show-ref --verify refs/heads/develop

orch project add alpha /path/to/project
orch alpha init
```

对于全新空仓库，必须先在临时普通仓库创建首个 `develop` commit，再将其克隆或推送到 `.bare.git`。v1.1 的 `project add` 和 `init` 都不会隐式创建首个 commit。

### 12.2 Windows（PowerShell 5.1+）

```powershell
New-Item -ItemType Directory -Path "$env:USERPROFILE\.local\bin" -Force
# 将 orch 脚本写入 $env:USERPROFILE\.local\bin\orch.ps1 或 orch.cmd
# 推荐：单文件 Python 脚本，shebang 由 Python launcher 解析
[Environment]::SetEnvironmentVariable(
    "Path",
    "$env:USERPROFILE\.local\bin;$env:Path",
    "User"
)
# 重新打开 shell
cd C:\path\to\project
git clone --bare C:\path\to\existing\source .bare.git
git --git-dir=.bare.git show-ref --verify refs/heads/develop
orch project add alpha C:\path\to\project
orch alpha init
```

对于全新空仓库，同样先在临时普通仓库创建首个 `develop` commit，再建立 `.bare.git`。

Windows 路径长度限制：`MAX_PATH`（260）可能在深层 worktree 中触发，需启用长路径支持或缩短项目根路径。文档必须提示此限制。

### 12.3 升级与降级

v1.1 不提供迁移工具；若数据库 schema 变更需要 `orch upgrade`，必须显式版本字段 `schema_version` 并编写迁移脚本（列入未来工作）。

---

## 13. 实现模块边界

```
orch/
├── __main__.py              # 入口
├── cli.py                   # argparse，--json 包装
├── config.py                # config.json 原子读写 + 配置锁
├── registry.py              # 项目注册表查询
├── db.py                    # SQLite 连接管理 + 事务上下文
├── locks.py                 # 跨平台项目锁、配置锁
├── git/
│   ├── ref.py               # bare-repo 命令封装
│   ├── worktree.py          # linked-worktree 命令封装
│   └── parser.py            # 分支名/路径校验、check-ref-format
├── state_machine.py         # 状态迁移合法性
├── merge/
│   ├── claim.py
│   ├── do.py
│   ├── finalize.py
│   └── recover.py           # reset-stuck 证据化恢复
├── commands/
│   ├── project.py           # add/remove/list
│   ├── init.py
│   ├── worktree_add.py
│   ├── enqueue.py
│   ├── readonly.py          # pending/diff/changes/log/list
│   ├── merge.py
│   ├── retry.py
│   ├── skip.py
│   ├── reset_stuck.py
│   ├── cleanup.py
│   └── lock.py              # lock-status/lock-break
├── jsonio.py                # 信封、schema_version
├── errors.py                # 错误类型 → 退出码映射
└── audit.py                 # 写入 audit_log
```

约束：
- `commands/*` 不直接调用 `subprocess`，统一经 `git/ref.py` 或 `git/worktree.py`。
- `merge/` 之外的命令**不实现**任何事务内 Git 调用。
- `locks.py` 是唯一允许删除锁文件的位置（仅 `lock-break` 路径）。

---

## 14. P0 失败不变量

见 §10。本节仅强调三个 P0 不变量：

1. 任何失败路径必须在 `finally` 中释放项目锁。
2. 任何失败路径必须尝试清理 `main/`；不能证明已恢复时，任务必须进入 `recovery_required`，并禁止后续 merge。
3. 任何失败路径不得在 DB 中留下未终结事务。

---

## 15. 实现优先级

### 15.1 P0（v1.1 必须完整实现）

1. 项目管理（`project add/list/remove`，含配置锁与原子写入）。
2. 数据库初始化与 schema，含 WAL/busy_timeout/foreign_keys 与 `BEGIN IMMEDIATE`。
3. 项目锁（`lock-status`、`lock-break`）。
4. `init`。
5. `worktree-add`。
6. `enqueue`（4 项校验 + UUID + 单次尝试冻结的 source SHA + 永久不可变的原始 target SHA）。
7. `pending` / `diff` / `changes` / `log`（含 task_id 与 branch 解析，使用入库时 source_commit）。
8. `merge`（三段协议 + post-check + 冲突时阻塞队列）。
9. `retry` / `skip` / `reset-stuck`（证据化恢复）。
10. `cleanup --prune`（保守路径）。
11. `--json`、版本化信封、稳定错误形态、退出码、强制审计事件。
12. Skill 文件（§16）。

### 15.2 P1（建议实现）

- 详细审计日志（包含操作者、上下文）。
- 锁 stale 自动检测（仅检测，不自动删除）。
- 项目级配置（自定义默认分支——v1.1 仍硬编码 `develop`，仅放架构）。

### 15.3 未来工作（v1.1 不实现）

- `test` 命令：从 v1.1 CLI 中**移除**；列入未来工作，由独立 Skill 接管。
- 项目级自定义默认分支。
- 超时自动 `reset-stuck`。
- 简单 Web 看板 / 远程仪表盘。
- 多主机/分布式协调。
- 第三方依赖引入（如 `rich`、`click`）。

---

## 16. Skill 交付物

文件路径：`~/.orchestrator/skills/orchestrator/SKILL.md`（同时建议用户在其 Agent 框架 skills 目录中创建软链或副本）。

### 16.1 YAML frontmatter

```yaml
---
name: orchestrator
description: Multi-agent worktree orchestration CLI (orch). Use when working on a project managed by orch, when inspecting pending merge tasks, reading actual code waiting to merge, enqueueing finished work, or triggering sequential merges. Triggers: orch, pending, worktree, multi-agent, sequential merge, develop branch, queue.
---
```

### 16.2 完整正文（实现时原样交付）

以下代码块是 `SKILL.md` 的完整文件内容，不依赖本设计之外的历史对话：

```markdown
---
name: orchestrator
description: Multi-agent worktree orchestration CLI (orch). Use when inspecting pending merge tasks, reading queued code, creating managed worktrees, enqueueing completed work, resolving a blocked task, or triggering sequential merges in an orch-managed project.
---

# Orchestrator

Use `orch` for every managed Git write that affects the merge queue or `develop`. This skill applies to a single host with multiple local processes. The v1.1 target branch is always `develop`.

## Required Workflow

1. Start by running `orch <project> pending --json`.
2. Inspect relevant queued work with `diff`, `changes`, and `log` before editing related files.
3. Create your worktree with `orch <project> worktree-add <agent> <branch>`.
4. Develop and commit only in your own worktree.
5. Confirm your worktree is clean, then enqueue it with `orch <project> enqueue <agent> <branch> <worktree_path> --priority <N>`.
6. Use `orch <project> merge` or `merge --once` only when acting as the coordinator.

## Conflict Workflow

When a task becomes `conflict`, orch has already aborted the merge in `main/` and blocked the queue. In the task's source worktree, merge or rebase the current local `develop`, resolve conflicts, commit, and leave the worktree clean. Then run `orch <project> retry <task_id>`. `retry` validates the new commit and updates the queued SHA; it never edits Git state. To abandon a conflict task after `main/` is verified clean, run `orch <project> skip <task_id> --reason <text>`.

Never resolve conflicts or commit in `main/`. A `recovery_required` task is not a normal conflict; stop and run `lock-status` plus `reset-stuck`, then follow the evidence printed by orch.

## CLI Reference

| Command | Mode | Project lock |
|---|---|---|
| `orch project list [--json]` | read | no |
| `orch project add <name> <path>` | write | config lock |
| `orch project remove <name>` | write | config lock |
| `orch <project> init` | write | yes |
| `orch <project> worktree-add <agent> <branch> [--base develop]` | write | yes |
| `orch <project> enqueue <agent> <branch> <path> [--priority N]` | write | yes |
| `orch <project> list [--all] [--json]` | read | no |
| `orch <project> pending [--json]` | read | no |
| `orch <project> diff <task_id-or-branch>` | read | no |
| `orch <project> changes <task_id-or-branch>` | read | no |
| `orch <project> log <task_id-or-branch>` | read | no |
| `orch <project> merge [--once]` | write | yes |
| `orch <project> retry <task_id>` | DB write; Git validation only | yes |
| `orch <project> skip <task_id> [--reason text]` | write | yes |
| `orch <project> reset-stuck` | recovery | yes |
| `orch <project> cleanup [--prune]` | read/write | write mode: yes |
| `orch <project> lock-status [--json]` | read | no |
| `orch <project> lock-break --force` | exceptional recovery | special lock protocol |

Use `--json` whenever a command supports it and another program or Agent consumes the result. JSON responses contain `schema_version`, `ok`, `command`, `data`, and `error`. Do not parse human-readable output in automation.

## Project Layout

The project root contains `.bare.git/`, the merge-only `main/` worktree, and Agent worktrees under `worktrees/`. The user directory `~/.orchestrator/` contains the project registry, per-project SQLite databases, and lock files.

## Exit Codes

`0` success; `1` general failure; `2` usage error; `3` unregistered project; `4` merge precheck failure; `5` blocked queue; `6` lock error; `7` enqueue/retry validation failure; `8` Git or recovery failure; `9` database failure; `130` interrupted merge.

## Prohibited Actions

- Do not run `git add`, `git commit`, `git merge`, `git reset`, or `git checkout` in `main/`.
- Do not directly update, merge, or push `develop` outside orch.
- Do not edit the orchestrator SQLite database or lock files.
- Do not delete a lock file manually. Use `lock-status` and the guarded `lock-break --force` flow.
- Do not assume orch is a security sandbox. Processes with the same filesystem permissions can bypass it; obey the project permission boundary.
```

### 16.3 一致性要求

- 实现发布时必须用自动测试比对交付的 `SKILL.md` 与 §16.2 代码块内容。
- CLI 名称、参数、状态、退出码或 JSON schema 发生变化时，必须在同一变更中同步更新本节。
- 禁止用“上一版内容”或外部对话引用替代正文。

---

## 17. 验收标准（Given / When / Then）

### 17.1 并发 merge

- **Given** 项目 alpha 已注册，`main/` 干净，两个 pending 任务（priority 相同，时间不同）。
- **When** 同时在两个 shell 中执行 `orch alpha merge --once --json`。
- **Then** 仅一个进程进入 Do 阶段；另一个等待项目锁；先进入者处理一个任务并释放锁，后进入者再处理另一个任务；两个任务各被认领一次，顺序严格按 `(priority, submitted_at, queue_seq)`，两进程均输出 JSON 信封且退出码 0。

### 17.2 并发 enqueue

- **Given** 项目 alpha，worktree-A 在 branch `feat/a`，干净。
- **When** 同时执行 `orch alpha enqueue agentA feat/a /path/A` 两次。
- **Then** 仅一个成功（退出码 0），另一个因 `idx_tasks_branch_active` 冲突失败（退出码 7）；DB 中恰好一条 `pending` 任务，`counters.queue_seq` 只分配了对应事务提交的序号。

### 17.3 入队后分支变更

- **Given** 任务 T1 已 enqueue，`source_commit = S0`，`worktree_path = WT1`。
- **When** Agent 在 WT1 提交新 commit S1，但未重跑 enqueue。
- **Then** `orch alpha diff T1` 仍展示 `develop...S0`（使用冻结的 source_commit）；`orch alpha log T1` 展示 `develop..S0`；`merge` 时按 `S0` 合入。若尚未合入且要改为 S1，Agent 必须先 `skip T1`，再创建新任务 enqueue；`retry` 仅适用于 `conflict`。

### 17.4 冲突持久化

- **Given** 任务 T1 入队后 `merge` 触发冲突并标记 `conflict`。
- **When** 关闭 CLI，重新启动 `orch alpha merge`。
- **Then** 命令立即退出码 5，stderr 提示存在 conflict 任务并要求 `retry`；不进入 Do 阶段。

### 17.5 retry 流程

- **Given** T1 处于 `conflict`。
- **When** Agent 在其 worktree 中 `git merge develop` 成功并 commit，然后执行 `orch alpha retry T1`。
- **Then** `source_commit` 更新为新 HEAD；`target_head_before` 保持不变；状态置 `pending`；`attempts` 重置；`conflict_files` 与 `last_error` 清空；后续 `merge` 能正常处理 T1。

### 17.6 崩溃点：Claim 后 Do 前

- **Given** 项目锁已获取，任务 T1 已置 `merging`。
- **When** 进程在 Do 前被 SIGKILL。
- **Then** `main/` 状态未变；任务保持 `merging`；`reset-stuck` 经证据化检查（develop 未含 source_commit，main 在 develop 且干净）将任务置 `pending` 并记录 `reset_stuck{recovered_as:pending}`。

### 17.7 崩溃点：Do 中

- **Given** `git merge` 进行中，进程被 SIGKILL。
- **When** 运行 `orch alpha reset-stuck`。
- **Then** 若 `MERGE_HEAD` 存在，先采集冲突文件并尝试一次 abort；恢复到 `target_commit_at_claim` 且干净时按证据转 `conflict` 或 `pending`；若 abort 失败或状态无法对账则转 `recovery_required`。不得无限保留无说明的 `merging`。

### 17.8 崩溃点：Finalize 前

- **Given** merge 已成功，DB 仍 `merging`，Finalize 未执行。
- **When** 运行 `orch alpha reset-stuck`。
- **Then** 检测 `develop` 包含 `source_commit`（或 `merged_commit == HEAD`），将任务置 `merged`，写入 `merged_commit` 与 `finished_at`，记录 `reset_stuck{recovered_as:merged}`。

### 17.9 cleanup 安全

- **Given** T1、T2 已 merged；T1 的预期 worktree 干净、正常注册且其分支没有其他 worktree 引用；T2 的同名分支还被另一个非预期 worktree 引用。
- **When** 执行 `orch alpha cleanup --prune`。
- **Then** 仅 T1 的预期 worktree 被正常 remove、分支经 ancestry 校验和带预期旧 SHA 的 `update-ref -d` 删除并写入 `archived_at`；T2 被保留并报告“分支被其他 worktree 引用”；DB 中 T1、T2 行和全部 audit_log 均保留。

### 17.10 错误命名与路径

- **Given** 用户传入项目名 `../etc` 或路径含空字节。
- **When** 执行 `orch project add` 或 `orch alpha enqueue`。
- **Then** 立即退出码 2，stderr 提示命名/路径非法；DB 与 Git 均未被修改。

### 17.11 JSON 契约

- **Given** 任意子命令。
- **When** 添加 `--json`。
- **Then** 输出符合 §9.2 信封；`schema_version == 1`；`ok` 布尔；`error.code` ∈ §9.1 集合；`error.kind` 是稳定 snake_case 字符串。

### 17.12 冲突手动解决禁止

- **Given** T1 `conflict`。
- **When** 操作员在 `main/` 中 `git add` + `git commit`。
- **Then** 此操作**不被 orch 承认**：`reset-stuck` 不处理 `conflict`，DB 中 T1 保持 `conflict`；下次 `merge` 在 Claim 前因阻塞状态退出码 5。操作员必须撤销未经 orch 管理的 main 修改，再按源 worktree + `retry` 流程恢复。  
- **注**：此约束是 §1.2 与 §5.6 的最终落点。**禁止**提供任何"自动信任 main HEAD"的开关。

### 17.13 锁语义

- **Given** 项目锁文件存在，owner 为 `host:1234`。
- **When** 在另一进程执行 `orch alpha merge`。
- **Then** 阻塞等待；锁释放后继续。
- **When** 锁 owner PID 仍存活且执行 `orch alpha lock-break`。
- **Then** 退出码 6，拒绝删除。
- **When** 锁 owner PID 已退出且执行 `orch alpha lock-break --force`。
- **Then** 删除锁文件并写 `audit_log`。

### 17.14 目标分支硬编码

- **Given** orch 命令行尝试传入 `--target main`。
- **When** 执行 `orch alpha merge --target main`。
- **Then** argparse 拒绝该参数（CLI schema 中不存在 `--target`）；所有 merge 永远针对 `develop`。

### 17.15 SIGINT 恢复

- **Given** T1 已进入 `merging`，Git 子进程正在运行。
- **When** 向 orch 进程发送 SIGINT。
- **Then** orch 先终止并等待 Git 子进程，再按 §11.1 对账；可证明回滚时 T1 回到 `pending`，可证明已完成时转 `merged`，其他情况转 `recovery_required`；最后释放项目锁并以 130 退出。

### 17.16 retry 不修改 Git

- **Given** T1 为 `conflict`，源 worktree 尚未由 Agent 更新，HEAD 仍为旧 `source_commit`。
- **When** 执行 `orch alpha retry T1`。
- **Then** 命令退出码 7，源 worktree、裸仓库 refs 与任务字段均保持不变；只有 Agent 自行完成新 commit 且新 SHA 包含当前 `develop` 后，retry 才更新 DB。

### 17.17 空变更拒绝

- **Given** `feat/empty` 与当前 `develop` 指向同一 commit，或 `develop..feat/empty` 的 commit 数为 0。
- **When** 执行 `orch alpha enqueue agentA feat/empty <worktree>`。
- **Then** 退出码 7，JSON `error.kind = "enqueue_validation_failed"`，DB 不新增任务且计数器事务回滚。

### 17.18 放弃冲突任务

- **Given** T1 为 `conflict`，先前 merge 已成功 abort，`main/` 位于 `develop`、干净且无 `MERGE_HEAD`。
- **When** 执行 `orch alpha skip T1 --reason "superseded"`。
- **Then** T1 转为 `skipped` 并写审计，队列解除阻塞；若 `main/` 不满足守卫，skip 必须拒绝且 T1 保持 `conflict`。

### 17.19 Skill 完整性

- **Given** 构建产物包含 `SKILL.md`。
- **When** 将其标准化换行后与 §16.2 代码块比较。
- **Then** 内容完全一致；CLI 命令、退出码、冲突流程和禁止项均可在文件自身找到，不依赖历史对话。

### 17.20 同权限绕过边界

- **Given** 一个拥有项目目录写权限、但不通过 orch 的进程直接修改 `develop` ref。
- **When** 下一次执行 `orch alpha merge --once`。
- **Then** Claim 记录实际的 `target_commit_at_claim` 并继续按当前 Git 事实运行或因 precheck 拒绝；审计不得声称 orch 阻止了外部写入。部署文档必须明确 §1.3 的非沙箱边界。

---

## 18. 未决项与未来工作

明确不在 v1.1 范围：

- `test` 命令已从 v1.1 CLI 移除；功能与工作流列入未来工作，由独立 Skill 接管。
- 项目级配置（自定义默认分支、TTL、自动归档策略）。
- 远程仪表盘 / Web UI。
- 跨主机协调。
- 第三方依赖。
- `audit_log` 远程导出。
- 更细粒度的跨平台进度报告与可配置取消超时；基础 SIGINT/KeyboardInterrupt 正确性已在 §11.1 定义为 P0。

---

## 19. 实现期声明

本设计**未经过端到端经验性验证**。任何"已测试""已验证"的措辞在实现完成前**禁止使用**。实现完成后必须跑通第 17 章全部 Given/When/Then，再宣称 v1.1 ready。
