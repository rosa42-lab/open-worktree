# orch v1.1 实施任务清单

**源设计**：`worktree开发设计方案.md`（**v1.1 实现候选版**；以工作区当前文件为准，非仅已提交的 v1.0）  
**目标交付物**：全局 CLI `orch`（Python 3.10+，仅标准库）+ `SKILL.md`  
**当前实现进度**：核心实现已落地（Phase 0–6 主体）；§17 全量验收（Phase 7）**未完成**，版本仍为 **实现候选**。  
**测试**：`python -m unittest discover -s tests -v`（约 20 项，含 e2e happy path）。

---

## 0. 元信息

### 0.1 硬约束（实现时不得违反）

- 仅 Python 标准库；**禁止**第三方依赖。
- 目标分支硬编码 `develop`；CLI **无** `--target`。
- 单机多进程；无网络服务 / Dashboard。
- Git 模型：`.bare.git` + linked worktrees；合入仅经 `orch <project> merge`。
- 写事务短、**禁止**在 `BEGIN…COMMIT` 内跑 Git 子进程。
- 失败路径：`finally` 释锁；不能证明 `main/` 已恢复则进 `recovery_required`。
- orch **不是** OS 沙箱（§1.3）；文档不得伪称可抵御同权限恶意进程。
- **§13**：`commands/*` 不直接调用 `subprocess`，统一经 `git/*`；**仅** `locks.py` 允许删除锁文件（常规 `release` 与 `lock-break --force`）。
- 所有对外子命令必须支持 `--json`，输出符合 §9.2 信封（§7.1、§15.1#11）。

### 0.4 与设计文档冲突时的权威节（实现/本 task 已钉死）

设计 v1.1 正文偶有前后不一致；**本 task 与实现以下列为准**（不另开“猜设计”）：

| 主题 | 权威 | 说明 |
|------|------|------|
| enqueue 校验条数 | **§7.6 五项** | 覆盖 §4.2/§10/§15.1 中过时的“四项”表述 |
| 退出码 7 / 130 | **§7.9 + §11.1 + §16.2 Skill** | §9.1 表未列 retry/130，实现须补全 |
| 退出码 6 | **§6.6 全语义** | 含等锁超时、hostname 不匹配、PID 存活/不可判定，不仅 lock-break |
| JSON 章节 | **§9** | §7.1 误写“符合 §10”时按 §9 执行 |
| Skill 全文 | **§16.2 代码块** | 交付与一致性测试只对 §16.2；不以 §16.1 单独 description 为准 |
| `recovery_required` 恢复 | **§7.11 判定序** | 证据充分时可 → `merged` 或 `pending`（§4.2 表未写全） |
| 无 `--once` 的 merge | **本 task T-0407** | 默认排空 pending，遇阻塞/空队列停止；`--once` 只处理一个 |

### 0.2 状态图例

| 标记 | 含义 |
|------|------|
| `[ ]` | 未开始 |
| `[~]` | 进行中 |
| `[x]` | 已完成（DoD 满足） |
| `[-]` | 取消 / 移出范围 |

### 0.3 任务条目字段

每个任务含：状态、优先级、依赖、设计锚点、产出文件、做/不做、完成定义 (DoD)、对应验收。

---

## 1. 依赖总览

```text
Phase 0  脚手架
   ↓
Phase 1  横切基础（errors / jsonio / naming / git / db / locks / state / audit / config）
   ↓
Phase 2  项目管理 + 锁命令 + init + worktree-add
   ↓
Phase 3  enqueue + 只读命令
   ↓
Phase 4  merge 三段协议（含 SIGINT）
   ↓
Phase 5  retry / skip / reset-stuck / cleanup
   ↓
Phase 6  Skill + 安装 + 边界文档
   ↓
Phase 7  §17 全量验收 + ready 门禁
```

**关键路径**：T-0001 → T-010x → T-0203 → T-0301 → T-040x → T-0503 → T-0703  
**最高风险**：Phase 4（merge）与 T-0503（reset-stuck 证据化恢复）。

**建议开工顺序**：同 Phase 内可按编号；跨 Phase 必须满足依赖列。

---

## 2. Phase 任务

### Phase 0 — 仓库脚手架

#### T-0001 — 包布局与入口

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: 无
- **设计锚点**: §13, §1.1, §7.1
- **产出文件**: `orch/__init__.py`, `orch/__main__.py`, `orch/cli.py`（骨架）
- **做**:
  - 建立 `orch/` 包，`python -m orch` 可启动。
  - `cli.py` 用 `argparse` 搭顶层：`project` 子命令组 + `orch <project> <command>` 路由占位。
  - 全局/各子命令预留 `--json` 开关（实现可与 T-0102 联调；最终全命令贯通见 T-0102 DoD）。
  - 未实现子命令返回清晰 usage（退出码 2）。
  - 目录上预留 `orch/commands/`、`orch/git/`、`orch/merge/`，避免 commands 直接依赖 `subprocess`。
- **不做**: 业务逻辑；不引入 click/typer。
- **完成定义 (DoD)**: `python -m orch --help` 与 `python -m orch project --help` 退出 0；无第三方 import。
- **对应验收**: —

#### T-0002 — 开发/测试布局

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0001
- **设计锚点**: §17
- **产出文件**: `tests/`, `tests/helpers/`（或等价）
- **做**:
  - 使用标准库 `unittest`（禁止 pytest 等第三方，除非日后改设计）。
  - 约定临时目录 fixture：可创建最小 bare 仓 + `develop` 初始 commit。
  - 文档化如何跑测试：`python -m unittest discover`。
- **不做**: 完整 §17 用例（属 Phase 7）。
- **完成定义 (DoD)**: 至少 1 个占位测试通过；fixture 能建出可用 bare + develop。
- **对应验收**: —

#### T-0003 — 常量与硬编码 develop

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0001
- **设计锚点**: §1.1, §17.14
- **产出文件**: `orch/constants.py`
- **做**:
  - `TARGET_BRANCH = "develop"`。
  - 锁等待超时 30s；Git 子进程默认超时 60s；JSON `schema_version = 1`。
  - 用户目录根：`~/.orchestrator`（跨平台用 `Path.home()`）。
- **不做**: 可配置目标分支。
- **完成定义 (DoD)**: CLI schema 中不存在 `--target`；常量单点引用。
- **对应验收**: §17.14

---

### Phase 1 — 横切基础层

#### T-0101 — 错误类型与退出码

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0003
- **设计锚点**: §9.1, §10, §6.6, §7.9, §11.1, §16.2
- **产出文件**: `orch/errors.py`
- **做**:
  - 定义异常层次或错误码枚举（**完整集**，以 Skill/协议为准补全 §9.1 表）：
    - `0` 成功
    - `1` 一般失败
    - `2` 用法错误
    - `3` 项目未注册
    - `4` merge precheck 失败
    - `5` 队列阻塞（conflict / recovery_required）
    - `6` 锁错误（等锁超时、lock-break 拒绝、hostname/PID 不可判定等，§6.6）
    - `7` **入队与 retry 校验失败**（§7.6 / §7.9）
    - `8` Git 或不可安全恢复
    - `9` DB 错误
    - `130` merge 中断（SIGINT / KeyboardInterrupt，§11.1）
  - 统一「错误 → 退出码」映射，供 CLI 捕获。
- **不做**: 人类文案的最终润色（可后续）；不因 §9.1 表缺项而省略 7 的 retry 语义或 130。
- **完成定义 (DoD)**: 上列每个码至少有一处可构造/单测；与 §9.1 **并集** §6.6/§7.9/§11.1/§16.2 一致，而非仅复制 §9.1 窄表。
- **对应验收**: §17.11（error.code 属于稳定集合）, §17.15（130）

#### T-0102 — JSON 信封与全命令 `--json`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0101, T-0001
- **设计锚点**: §9.2, §7.1, §15.1#11, §17.11
- **产出文件**: `orch/jsonio.py`；`orch/cli.py` 中统一包装
- **做**:
  - 成功/失败统一信封：`schema_version`, `ok`, `command`, `data`, `error`。
  - `error` 含 `code`, `kind`（稳定 snake_case）, `message`, `details`。
  - CLI 层统一处理 `--json`：成功与失败路径均走信封；禁止业务命令在 `--json` 模式下 print 非信封 JSON 或仅人类文本。
  - **每一个**已注册子命令（含 `project *` 与 `orch <project> *`）在 argparse 上接受 `--json`（§7.1）。
- **不做**: 多 schema 版本并存；要求人类模式也输出 JSON。
- **完成定义 (DoD)**:
  - 序列化样例 `schema_version == 1`；错误样例字段齐全。
  - 命令清单（与 §7.2–§7.3 / Skill CLI 表一致）逐条可 `--json` 跑通骨架或真实实现，且 `error.code` 落在 T-0101 集合。
- **对应验收**: §17.11

#### T-0103 — 命名与路径校验

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0003
- **设计锚点**: §2.3, §8.3, §17.10
- **产出文件**: `orch/validate.py` 或并入 `git/parser.py` + 独立 naming 模块
- **做**:
  - project/agent：`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`，不匹配必须拒绝（非 warning）。
  - 路径：`expanduser().resolve()`；拒绝空字节、控制字符、危险 `..` 语义。
  - branch-safe：`/` → `__`；不安全字符替换或拒绝；入队前 `git check-ref-format --branch`。
- **不做**: 猜测式 UUID 识别（见 T-0302）。
- **完成定义 (DoD)**: `../etc`、空字节路径 → 退出码 2；合法名通过。
- **对应验收**: §17.10

#### T-0104 — Git 子进程封装

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0101, T-0103
- **设计锚点**: §8.1–§8.4, §13
- **产出文件**: `orch/git/ref.py`, `orch/git/worktree.py`, `orch/git/parser.py`, `orch/git/__init__.py`
- **做**:
  - `run_git_ref(args, bare_path)`：`--git-dir` bare 类。
  - `run_git_worktree(args, worktree_path)`：`cwd=worktree`，**禁止**对 index/工作区类命令误用 bare `--git-dir`。
  - `run_git_global(args)`：如 `check-ref-format`，不伪造 git-dir。
  - `subprocess.run(..., shell=False, capture_output=True, text=True, timeout=...)`；args 必须为 list。
  - worktree 所有权：`rev-parse --git-common-dir` 与项目 bare resolve 后严格相等；写路径前校验出现在 `worktree list --porcelain`（§8.4）。
  - merge 用子进程需可保留 Popen 句柄以便 SIGINT 终止（接口预留，T-0408 接好）。
  - **唯一**允许 `subprocess` 的 Git 入口在 `orch/git/*`（及 T-0108 Windows `tasklist` 等锁辅助）；`commands/*` 禁止直接 `import subprocess`。
- **不做**: 任何 `shell=True`；远程 `push/fetch`。
- **完成定义 (DoD)**: 单元/集成测：ref 与 worktree 两路调用；非法分支名在 parser 层拒绝；静态约定或测试保证 `commands/` 不直接调 subprocess。
- **对应验收**: —

#### T-0105 — SQLite 连接与 schema

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0101
- **设计锚点**: §3.1–§3.5
- **产出文件**: `orch/db.py`
- **做**:
  - 库路径：`~/.orchestrator/data/<project>/orchestrator.db`。
  - 连接 PRAGMA：`foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `temp_store=MEMORY`。
  - 写事务统一 `BEGIN IMMEDIATE` 上下文管理器。
  - 表：`tasks`（全字段含 UUID、冻结 SHA、`queue_seq`、`archived_at` 等）、`audit_log`、`counters`。
  - 索引：`idx_tasks_status_priority`、`idx_tasks_status_seq`、`idx_tasks_branch`、partial unique `idx_tasks_branch_active`。
  - `INSERT OR IGNORE INTO counters(name,value) VALUES ('queue_seq',0)`。
- **不做**: schema 迁移工具（未来工作）。
- **完成定义 (DoD)**: init schema 后表/索引存在；并发两个 IMMEDIATE 写不损坏 counters 语义（可单测模拟）。
- **对应验收**: —

#### T-0106 — 状态机

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0105
- **设计锚点**: §4.1–§4.2
- **产出文件**: `orch/state_machine.py`
- **做**:
  - 状态：`pending|merging|merged|conflict|recovery_required|skipped`。
  - 仅允许 §4.2 表中的迁移；其它拒绝并触发 `invalid_transition` 审计路径（与 T-0107 协作）。
  - 阻塞语义：`conflict` 与 `recovery_required` 阻塞 merge Claim。
- **不做**: 隐式“信任 main HEAD”的迁移。
- **完成定义 (DoD)**: 合法/非法迁移单测全覆盖 §4.2 行。
- **对应验收**: —

#### T-0107 — 审计写入

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0105
- **设计锚点**: §3.3, §9.3
- **产出文件**: `orch/audit.py`
- **做**:
  - 强制 action 枚举：`enqueued`, `merge_claimed`, `merge_started`, `merge_succeeded`, `merge_aborted_conflict`, `merge_aborted_precheck`, `merge_aborted_recovery_required`, `retried`, `skipped`, `reset_stuck`, `cleanup_pruned`, `project_locked`, `project_unlocked`, `config_updated`, `invalid_transition`。
  - `detail` 为 JSON 字符串；`created_at` ISO 8601 UTC；`task_id` 可空。
- **不做**: 远程导出。
- **完成定义 (DoD)**: 未知 action 在写入层被拒绝；已知 action 可落库。
- **对应验收**: —

#### T-0108 — 跨平台锁协议

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0101, T-0102, T-0107
- **设计锚点**: §6.1–§6.6, §9.3, §13, §17.13
- **产出文件**: `orch/locks.py`
- **做**:
  - 项目锁：`~/.orchestrator/data/<project>/project.lock`；配置锁：`~/.orchestrator/config.json.lock`。
  - **仅** `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)` 独占创建；不写 fcntl/msvcrt 锁。
  - owner payload：`token`, `pid`, `hostname`, `started_at`, `command`, `project`。
  - `release` 仅 token 匹配时 unlink（常规路径**唯一**删锁方式之一）。
  - 获取失败：抖动重试，最长 30s，超时码 6；**禁止**常规路径删锁或覆盖。
  - PID 活性：POSIX `os.kill(pid,0)`；Windows `tasklist`；hostname 不一致或无法判定 → lock-break 拒绝码 6。
  - **审计（§9.3）**：项目锁 **acquire 成功** 后写 `project_locked`（detail 含 token/pid/command 等）；正常 **release 成功** 写 `project_unlocked`（detail 含 reason，如 `release`）；`lock-break --force` 成功删锁亦写 `project_unlocked`（reason 如 `pid_dead`）。配置锁可不写 `project_*` 审计，配置变更走 `config_updated`。
  - **§13**：除本模块的 `release` 与 `lock-break` 路径外，任何代码不得 `unlink`/`os.remove` 锁文件。
  - **v1.1 不**自动 stale 删除。
- **不做**: 锁重入；自动清理活锁；在 commands 里直接删锁文件。
- **完成定义 (DoD)**: 双进程争用串行；活 PID lock-break 失败；死 PID + `--force` 可删；acquire/release 产生对应审计行；Windows 与 POSIX 分支均有测或明确手工步骤。
- **对应验收**: §17.13

#### T-0109 — 配置注册表

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0103, T-0108
- **设计锚点**: §2.1, §6.7
- **产出文件**: `orch/config.py`, `orch/registry.py`
- **做**:
  - `config.json` 结构：`projects` 映射 name → path。
  - 写路径：持配置锁 → 写 `config.json.tmp` → `os.replace` → 释锁；审计 `config_updated`（若适用）。
  - registry 查询：按名解析绝对路径；未注册 → 码 3。
- **不做**: remove 时 purge DB 目录。
- **完成定义 (DoD)**: 并发 add 不损坏 JSON；replace 原子性在单测/文档中说明。
- **对应验收**: —

**Phase 1 出口**：无业务命令也可对 DB/锁/JSON/校验做单元测试；退出码集合含 7=retry 与 130；锁 acquire/release 可写审计；`commands/` 不直接 subprocess 的约束已文档化于 T-0104。

---

### Phase 2 — 项目管理与初始化

#### T-0201 — `project list` / `add` / `remove`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0109, T-0102
- **设计锚点**: §7.2, §15.1#1
- **产出文件**: `orch/commands/project.py`
- **做**:
  - `list [--json]`：只读。
  - `add <name> <path>`：规范化路径；**拒绝**无 `.bare.git`；拒绝重名；原子写入。
  - `remove <name>`：项目锁文件存在则拒绝；仅删 registry 映射，**不删** `data/<project>/`。
- **不做**: `--purge`；创建 bare 仓。
- **完成定义 (DoD)**: add/list/remove 人类输出与 `--json` 信封均可用；非法名码 2；未注册后续命令码 3。
- **对应验收**: §17.10（命名路径）

#### T-0202 — `lock-status` / `lock-break`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0108, T-0107
- **设计锚点**: §6.6, §7.13, §17.13
- **产出文件**: `orch/commands/lock.py`
- **做**:
  - `lock-status [--json]`：不获取锁；打印 token/pid/hostname/started_at/command。
  - `lock-break [--force]`：不走配置锁；无锁则 ok；hostname/PID 守卫；**仅通过 `locks.py`** 删锁；成功写 `project_unlocked`（reason 如 `pid_dead`）。
  - 无 `--force` 时不得删锁（退出非 0 或明确拒绝，与实现选定的 usage/锁错误码一致且稳定）。
- **不做**: 在 command 内直接 `os.unlink` 锁文件；跨机强删；活性不可判定时删除。
- **完成定义 (DoD)**: 与 §17.13 三则 When/Then 对齐；`--json` 可用。
- **对应验收**: §17.13

#### T-0203 — `init`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0105, T-0104, T-0201, T-0108
- **设计锚点**: §7.4, §15.1#4
- **产出文件**: `orch/commands/init.py`
- **做**:
  - 前置：已注册；`.bare.git` 为 bare；`refs/heads/develop` 存在。
  - 创建 `main` worktree（`worktree add main develop`）；已存在则校验归属/分支/干净。
  - 创建 `worktrees/`；初始化用户级 DB schema。
  - 持项目锁；**须先保证 DB schema 可用再写** `project_locked` 审计（或等价：建库与持锁顺序不得导致审计丢到无表）。
  - 支持 `--json`。
- **不做**: 创建裸仓或首个 commit；接受目标分支参数。
- **完成定义 (DoD)**: init 后 `main/` 在 develop 且干净；DB 可打开；缺 develop 时拒绝。
- **对应验收**: —

#### T-0204 — `worktree-add`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0203, T-0103, T-0104
- **设计锚点**: §7.5, §15.1#5
- **产出文件**: `orch/commands/worktree_add.py`
- **做**:
  - 校验 agent/branch 命名与 `check-ref-format`。
  - 分支不存在：`worktree add -b <branch> worktrees/<agent>-<branch-safe> develop`。
  - 分支存在：`worktree add ... <branch>`；已被其他 worktree 检出则拒绝（无 `--force`）。
  - base 默认且仅允许 `develop`。
- **不做**: 任意 base 分支；force 检出。
- **完成定义 (DoD)**: 新建 worktree 路径正确；重复检出拒绝。
- **对应验收**: —

---

### Phase 3 — 入队与只读

#### T-0301 — `enqueue`（五项校验 + 冻结 SHA）

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0204, T-0105, T-0106, T-0107
- **设计锚点**: §7.6, §3.2, §3.5, §17.2, §17.17
- **产出文件**: `orch/commands/enqueue.py`
- **做**:
  1. worktree 存在且 common-dir 指向本项目 bare；HEAD 分支名 == 参数 branch。  
  2. porcelain 为空。  
  3. bare 上 branch 可 `rev-parse --verify`。  
  4. partial unique：同分支无 active 任务。  
  5. `rev-list --count develop..<source_commit> > 0`。  
  - Git 读与 SHA 采集在事务外；事务内 `counters+1` + insert + `enqueued`。  
  - 字段：UUID、`source_commit` 冻结、`target_head_before` 永久、`priority>=0`、`queue_seq`。  
  - 持项目锁；无 `--force`。
- **不做**: 事务内 Git；空变更入队。
- **完成定义 (DoD)**: 双进程同分支 enqueue 仅一成功（码 7）；空变更码 7 且计数器回滚；JSON `enqueue_validation_failed`。
- **对应验收**: §17.2, §17.17

#### T-0302 — task_id | branch 解析器

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0105, T-0104
- **设计锚点**: §7.7
- **产出文件**: `orch/task_resolve.py` 或 `commands/readonly.py` 内模块
- **做**:
  - 先按参数原文查 task 主键；命中即用。
  - 未命中：必须通过 `check-ref-format --branch`，再按 `branch_name` 查。
  - 多历史任务：优先唯一未完成；仅多个终态且无 task id → 拒绝歧义并列出候选。
  - **禁止**按字符串形状猜 UUID；**禁止**未查库把输入丢给 Git。
- **不做**: 模糊搜索。
- **完成定义 (DoD)**: 单测覆盖：精确 id、唯一 pending 分支、歧义终态。
- **对应验收**: —

#### T-0303 — `pending` / `list`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0301, T-0302
- **设计锚点**: §7.3, §7.7, §15.1#7
- **产出文件**: `orch/commands/readonly.py`（部分）
- **做**:
  - `pending [--json]`：仅 `status=pending`；人类可读含冻结 SHA 的 `diff --stat develop...<source_commit>` 与 `log --oneline develop..<source_commit> -n 5`。
  - `list [--all] [--json]`（方案未写默认过滤，**本 task 钉死**）：
    - **默认**（无 `--all`）：`archived_at IS NULL` 的任务（含 pending/merging/conflict/recovery_required/merged/skipped 未归档行）。
    - **`--all`**：同一项目全部 task 行（含已 `archived_at` 的历史）。
    - 排序建议：`submitted_at ASC, queue_seq ASC`（或等价稳定序），JSON 中字段完整可区分状态。
  - **只读**：不改 DB、不改 Git；不持项目锁（§5.8）。
  - 支持 `--json`（T-0102）。
- **不做**: 写锁；把 `list` 默认成“仅 pending”（那是 `pending` 命令的职责）。
- **完成定义 (DoD)**: 默认与 `--all` 集合可测区分；与 merge 并发读不破坏状态；pending 展示使用入库 `source_commit`。
- **对应验收**: —

#### T-0304 — `diff` / `changes` / `log`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0302, T-0104
- **设计锚点**: §7.7, §17.3
- **产出文件**: `orch/commands/readonly.py`（部分）
- **做**:
  - `diff`：`git diff develop...source_commit`（三点）。
  - `changes`：name-status + stat + log。
  - `log`：`git log develop..source_commit`（两点）。
  - 一律用任务冻结的 `source_commit`，**不用**分支当前 HEAD。
- **不做**: 修改任务或 Git。
- **完成定义 (DoD)**: 入队后 worktree 新 commit 不改变 diff/log 展示的 SHA 范围（§17.3）。
- **对应验收**: §17.3

---

### Phase 4 — merge 核心

#### T-0401 — Precheck

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0203, T-0104, T-0108
- **设计锚点**: §5.2
- **产出文件**: `orch/merge/claim.py`（或 `precheck.py`）
- **做**:
  - 已持项目锁；仅 Git 读，不写任务状态。
  - 校验：`main/` 存在；common-dir=bare；HEAD=develop；porcelain 空；无 MERGE_HEAD。
  - 读取 develop HEAD 为 `target_commit_for_claim`（内存）。
  - 失败：审计 `merge_aborted_precheck`，任务保持 pending，释锁，码 4。
- **不做**: Claim 写库；自动修 main。
- **完成定义 (DoD)**: main 脏/错分支时码 4 且 pending 不变。
- **对应验收**: —

#### T-0402 — Claim

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0401, T-0106, T-0107
- **设计锚点**: §5.3, §17.1
- **产出文件**: `orch/merge/claim.py`
- **做**:
  - `BEGIN IMMEDIATE`：若存在 conflict/recovery_required → 审计，码 5。
  - 选 pending 队首：`priority ASC, submitted_at ASC, queue_seq ASC`。
  - 置 `merging`，写 `claimed_at`、`target_commit_at_claim`，`attempts+=1`，审计 `merge_claimed`。
  - 无 pending：码 0，报告 no pending tasks。
  - 后续只读冻结 `source_commit`。
- **不做**: 事务内 Git。
- **完成定义 (DoD)**: 双进程同时 merge 时 Claim 串行；顺序稳定。
- **对应验收**: §17.1

#### T-0403 — Do

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0402
- **设计锚点**: §5.4
- **产出文件**: `orch/merge/do.py`
- **做**:
  - 短事务写 `merge_started`。
  - `git -C main/ merge --no-ff --no-edit <source_commit>`；**禁止**传分支名。
  - 等待子进程结束；按返回码分支到成功/冲突/恢复路径。
- **不做**: ff-only；在 main 里 commit 解决冲突。
- **完成定义 (DoD)**: merge 对象 SHA 与 DB `source_commit` 一致（可用测试断言 argv）。
- **对应验收**: §17.3（合入冻结 SHA）

#### T-0404 — Finalize 成功路径

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0403
- **设计锚点**: §5.5
- **产出文件**: `orch/merge/finalize.py`
- **做**:
  - post-check：porcelain 空、无 MERGE_HEAD、`merge-base --is-ancestor source_commit develop`。
  - 通过：状态 `merged`，写 `merged_commit`/`finished_at`，清 error 字段，审计 `merge_succeeded`。
  - 不通过：转 T-0406。
- **不做**: 跳过 post-check。
- **完成定义 (DoD)**: 成功合入后 develop 含 source；任务终态 merged。
- **对应验收**: —

#### T-0405 — Finalize 冲突路径

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0403
- **设计锚点**: §5.6, §17.4, §17.12
- **产出文件**: `orch/merge/finalize.py`
- **做**:
  - 判定为 merge conflict（CONFLICT + 非 0 + MERGE_HEAD）。
  - **先** `diff --name-only --diff-filter=U` 捕获 `conflict_files`。
  - **再** 单次 `merge --abort`；成功 → `conflict` + 审计 `merge_aborted_conflict`；失败 → recovery。
  - 此后 merge 遇阻塞码 5，直到 retry/skip。
- **不做**: 在 main 上 add/commit 解决；提供“信任 main HEAD”开关。
- **完成定义 (DoD)**: 冲突后重启 merge 立即码 5；DB 为 conflict。
- **对应验收**: §17.4, §17.12

#### T-0406 — Finalize recovery 路径

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0403
- **设计锚点**: §5.7, §14
- **产出文件**: `orch/merge/finalize.py`
- **做**:
  - 非冲突失败、abort 失败、post-check 失败等 → `recovery_required`。
  - 审计 `merge_aborted_recovery_required`；退出码 8。
  - **禁止** `reset --hard` / 破坏性 checkout。
  - 持锁至命令退出。
- **不做**: 自动盲恢复。
- **完成定义 (DoD)**: 任务阻塞队列；证据写入 last_error/detail。
- **对应验收**: —

#### T-0407 — `merge` CLI 装配 + `--once`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0401, T-0402, T-0403, T-0404, T-0405, T-0406
- **设计锚点**: §7.3, §7.8, §5.8, §15.1#8, §16.2
- **产出文件**: `orch/commands/merge.py`
- **做**:
  - 获取项目锁 →（循环内）Precheck → Claim → Do → Finalize → **`finally` 释锁**（整次命令持锁期间可处理多个任务，或每任务一轮锁——须保证 §5.8 至多一个 Do；推荐整次 invoke 持锁并循环）。
  - **默认（无 `--once`）**：在同一次命令中按队列顺序处理 pending，直到 (a) 无 pending（报告 no pending / 已排空），或 (b) precheck 失败，或 (c) 出现 conflict/recovery 阻塞（码 5），或 (d) 其它致命错误。不得在阻塞后继续 Claim 下一任务。
  - **`--once`**：最多成功 Claim+处理 **一个** 任务后退出（CI / §17.1）；无 pending 时码 0。
  - `--json` 信封；command 名如 `alpha.merge`；成功 acquire 写 `project_locked`。
- **不做**: 绕过项目锁的并行 Do；阻塞后跳过 conflict 继续 merge。
- **完成定义 (DoD)**: 两 shell 同时 `merge --once --json` 符合 §17.1；默认模式多 pending 时顺序合入直至空或阻塞；锁必释放。
- **对应验收**: §17.1, §17.4

#### T-0408 — SIGINT / KeyboardInterrupt

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0407
- **设计锚点**: §11.1, §17.15
- **产出文件**: `orch/merge/do.py`, `orch/commands/merge.py`（中断处理）
- **做**:
  1. 终止并等待 Git 子进程（超时强杀）。  
  2. 有 MERGE_HEAD：采冲突文件 → 一次 abort；无则按 HEAD/status 对账（同 reset-stuck 逻辑子集）。  
  3. 可证明回滚 → pending；可证明完成 → merged；否则 recovery_required。  
  4. 写完状态后 `finally` 释锁；退出码 **130**。
- **不做**: 子进程仍写 index 时做恢复。
- **完成定义 (DoD)**: 中断后无死锁文件（token 持有者已 release 或可 lock-break）；状态可解释。
- **对应验收**: §17.15

**Phase 4 出口**：并发 merge 顺序正确；冲突阻塞；冻结 SHA 合入。

---

### Phase 5 — 恢复、跳过与清理

#### T-0501 — `retry`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0405, T-0106
- **设计锚点**: §7.9, §17.5, §17.16
- **产出文件**: `orch/commands/retry.py`
- **做**:
  - 仅 `conflict`；**零 Git 写命令**。
  - 事务外校验：所有权、干净、分支名、bare HEAD==worktree HEAD、new≠old SHA、`merge-base --is-ancestor develop new`。
  - 失败：状态与字段全不变，码 7。
  - 成功：事务内再确认仍 conflict → 更新 `source_commit`，清空 claim/error/conflict 字段，`attempts=0`，→ `pending`；**不改** `target_head_before`；审计 `retried` 含 old/new SHA。
- **不做**: 替 Agent merge/rebase。
- **完成定义 (DoD)**: §17.5 与 §17.16 均满足。
- **对应验收**: §17.5, §17.16

#### T-0502 — `skip`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0405
- **设计锚点**: §7.10, §17.18
- **产出文件**: `orch/commands/skip.py`
- **做**:
  - 允许 `pending` 或 `conflict`；禁止 merging/recovery_required/终态。
  - skip conflict 前：main 在 develop、干净、无 MERGE_HEAD；否则拒绝。
  - → `skipped`；审计 `skipped` + reason；不删 worktree。
- **不做**: 跳过 recovery_required；清理 worktree。
- **完成定义 (DoD)**: 守卫失败时任务保持 conflict；成功后队列解锁。
- **对应验收**: §17.18

#### T-0503 — `reset-stuck` 证据化恢复

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0406, T-0106
- **设计锚点**: §7.11, §5.9, §17.6–§17.8, §17.12
- **产出文件**: `orch/merge/recover.py`, `orch/commands/reset_stuck.py`
- **做**:
  - 持锁；处理 `merging` 与 `recovery_required`（正常至多一条）。
  - 处理对象：`status IN ('merging', 'recovery_required')`（§7.11）；**不处理** `conflict`（§17.12）。
  - 判定顺序固定（对 merging 与 recovery_required 均适用，权威 §7.11）：  
    1) develop 含 `source_commit` 且 main 在 develop、干净、无 MERGE_HEAD → `merged`（含 **recovery_required → merged**，即使 §4.2 表未单列）；  
    2) HEAD==`target_commit_at_claim` 且 main 干净 develop → `pending`；  
    3) 有 MERGE_HEAD → 采冲突 + **一次** abort → conflict 或 pending，abort 失败则 `recovery_required`；  
    4) 其它 → 保持/置 `recovery_required`（manual），**禁止**自动 reset/checkout。  
  - 审计 `reset_stuck` 且 detail 含 `recovered_as`。
- **不做**: 盲改 DB；处理 conflict；破坏性 git reset。
- **完成定义 (DoD)**: §17.6 / §17.7 / §17.8 场景可复现；main 手工 commit 不被承认（§17.12）。
- **对应验收**: §17.6, §17.7, §17.8, §17.12

#### T-0504 — `cleanup` 清单模式

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0303
- **设计锚点**: §7.12
- **产出文件**: `orch/commands/cleanup.py`
- **做**:
  - 无 `--prune`：只读列出可清理候选 worktree（`status=merged` 且 `archived_at IS NULL` 等）；展示 **merged 后 24h 冷却**（§7.12）；冷却未满的仍可列出但须标注不可 prune。
  - v1.1：此模式**不执行**删除；不持项目锁（Skill：write mode 才持锁）。
  - 支持 `--json`。
- **不做**: 无 `--prune` 时删除 worktree 或写 `archived_at`。
- **完成定义 (DoD)**: 输出可读/JSON；不修改 Git/DB。
- **对应验收**: —

#### T-0505 — `cleanup --prune` 保守删除

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0504, T-0104
- **设计锚点**: §7.12, §17.9
- **产出文件**: `orch/commands/cleanup.py`
- **做**:
  - 持项目锁；对每个候选：common-dir、porcelain 空、porcelain worktree list 唯一注册、分支无其它 worktree、非 git worktree locked。
  - 删前读 `branch_tip`；`merge-base --is-ancestor branch_tip develop`。
  - `worktree remove`（无 `--force`）→ `update-ref -d refs/heads/<branch> <branch_tip>` → `worktree prune`。
  - **全部 Git 成功后** 短事务写 `archived_at` + `cleanup_pruned`；**永不删 task 行/audit**。
  - 单任务失败不影响其它；**禁止先 DB 后 Git**。
- **不做**: `branch -D` 无 SHA 保护；force remove。
- **完成定义 (DoD)**: §17.9：仅安全候选被归档；被其它 worktree 引用的保留。
- **对应验收**: §17.9

---

### Phase 6 — Skill、安装与边界

#### T-0601 — 交付 `SKILL.md`

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0407, T-0501, T-0502, T-0503, T-0505（命令面稳定）
- **设计锚点**: §16.2, §16.3, §17.19
- **产出文件**:
  - **安装/交付路径（§16）**：`~/.orchestrator/skills/orchestrator/SKILL.md`（`Path.home() / ".orchestrator" / "skills" / "orchestrator" / "SKILL.md"`）
  - **仓库内副本（建议）**：`skills/orchestrator/SKILL.md` 或构建步骤复制到上述用户路径；Agent 框架可再软链
- **做**:
  - 文件**完整内容**（含 YAML frontmatter + 正文）与设计文档 **§16.2 代码块**一致；**不以 §16.1 的 description 覆盖 §16.2**（§16.1 仅作设计内说明，交付权威是 §16.2）。
  - 换行标准化策略在 T-0602 测试中固定（如统一 `\n`）。
- **不做**: 依赖“历史对话”补全；混用 §16.1 / §16.2 两套 frontmatter。
- **完成定义 (DoD)**: 用户路径或安装步骤可复现该文件；内容含 CLI、退出码（含 130）、冲突流程、禁止项；与 §16.2 可字节级对齐（经标准化换行）。
- **对应验收**: §17.19

#### T-0602 — Skill 一致性测试

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0601
- **设计锚点**: §16.3, §17.19
- **产出文件**: `tests/test_skill_consistency.py`（或等价）
- **做**:
  - 从设计文档提取 **§16.2 完整代码块**（含 frontmatter）；与交付文件全文比较（标准化换行后完全一致）。
  - 交付文件路径解析：优先仓库副本，并覆盖/校验安装目标 `~/.orchestrator/skills/orchestrator/SKILL.md` 的生成逻辑。
- **不做**: 手工“看起来差不多”即通过；只比对正文忽略 frontmatter。
- **完成定义 (DoD)**: 正文或 frontmatter 漂移则测试失败；CLI 变更必须同变更更新设计 §16.2 与 Skill。
- **对应验收**: §17.19

#### T-0603 — POSIX / Windows 安装入口

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: T-0001
- **设计锚点**: §12.1–§12.2
- **产出文件**: 安装脚本或 `README` 安装节 + 可选 `~/.local/bin` 包装说明
- **做**:
  - 文档化：`python -m orch` 与用户 bin 包装。
  - Windows PATH、`.cmd`/`.ps1` 建议；长路径 / MAX_PATH 提示。
  - 说明前置：用户自备 `.bare.git` + develop commit；`project add` / `init` 不建首 commit。
- **不做**: 自动安装第三方；打包上 PyPI（除非后续单独立项）。
- **完成定义 (DoD)**: 按文档在本机 Windows 可跑通 `project list`；POSIX 步骤在文档中完整。
- **对应验收**: —

#### T-0604 — 部署边界说明

- **状态**: `[ ]`
- **优先级**: P0
- **依赖**: 无（可与实现并行，收口前完成）
- **设计锚点**: §1.3, §17.20
- **产出文件**: `README.md` 或 `docs/security.md`
- **做**:
  - 明确 orch 非沙箱；同权限可 `update-ref`/改 DB/删锁。
  - 建议最小权限、独立账户或外部沙箱。
  - 说明 merge 仅按当前 Git 事实记录 `target_commit_at_claim`，审计不声称阻止了外部写入。
- **不做**: 假称强制安全边界。
- **完成定义 (DoD)**: 文档可被 §17.20 核对。
- **对应验收**: §17.20

---

### Phase 7 — 验收与收口

#### T-0701 — §17.1–§17.20 验收覆盖

- **状态**: `[x]`
- **优先级**: P0
- **依赖**: Phase 2–6 相关任务完成（含 T-0604）
- **设计锚点**: §17, §19
- **产出文件**: `tests/` 集成测 + 可选 `docs/acceptance-results.md`
- **做**:
  - **§17.1–§17.19**：每条 GWT 有自动化或可重复手工脚本；记录通过/失败与复现命令。
  - **§17.20**：核对 T-0604 部署边界文档 + merge 行为不伪称阻止同权限外部写入（文档审查清单即可，无需“防恶意”测试）。
- **不做**: 未通过时标注 ready。
- **完成定义 (DoD)**: 清单 **17.1–17.20** 全绿或仅残留已文档化且用户书面接受的环境限制。
- **对应验收**: §17.1–§17.20

#### T-0702 — 并发与崩溃手工清单

- **状态**: `[x]`
- **优先级**: P0
- **依赖**: T-0408, T-0503
- **设计锚点**: §17.1, §17.6–§17.8, §17.15
- **产出文件**: `docs/crash-drills.md`（或 acceptance 附录）
- **做**:
  - 记录至少一次：双进程 merge、Claim 后杀进程、Do 中杀进程、Finalize 前杀进程、SIGINT。
  - 每次含：操作步骤、DB 状态前后、Git 状态、reset-stuck 结果。
- **不做**: 仅理论推导无实操。
- **完成定义 (DoD)**: 五类场景均有实录。
- **对应验收**: §17.1, §17.6–§17.8, §17.15

#### T-0703 — v1.1 ready 门禁

- **状态**: `[~]`
- **优先级**: P0
- **依赖**: T-0701, T-0702, T-0602, T-0604
- **设计锚点**: §19
- **产出文件**: `docs/ready-checklist.md`；版本标注 / README 状态行
- **做**:
  - 门禁清单已写：`docs/ready-checklist.md`（A/B/C 已勾选；**D 节人工签字待办**）。
  - **仅当** D 节确认后，才可将 `1.1.0-candidate` 改为 ready，并更新设计 §19 / README。
  - 未签字前禁止“可交付”定论措辞。
- **不做**: 提前宣布完成；跳过 17.20。
- **完成定义 (DoD)**: `docs/ready-checklist.md` 全勾且版本字符串去掉 `-candidate`。
- **对应验收**: §19

---

## 3. 验收映射表（§17 → 任务）

| 验收 | 主题 | 主责任务 | 协作任务 |
|------|------|----------|----------|
| 17.1 | 并发 merge | T-0402, T-0407 | T-0702 |
| 17.2 | 并发 enqueue | T-0301 | — |
| 17.3 | 入队后分支变更 / 冻结 SHA | T-0304, T-0403 | — |
| 17.4 | 冲突持久化阻塞 | T-0405, T-0407 | — |
| 17.5 | retry 流程 | T-0501 | — |
| 17.6 | Claim 后 Do 前崩溃 | T-0503 | T-0702 |
| 17.7 | Do 中崩溃 | T-0503 | T-0702 |
| 17.8 | Finalize 前崩溃 | T-0503 | T-0702 |
| 17.9 | cleanup 安全 | T-0505 | — |
| 17.10 | 错误命名与路径 | T-0103, T-0201 | — |
| 17.11 | JSON 契约 | T-0102 | 全体命令 |
| 17.12 | 禁止 main 手工解决 | T-0405, T-0503 | — |
| 17.13 | 锁语义 | T-0108, T-0202 | — |
| 17.14 | develop 硬编码 | T-0003 | CLI 装配 |
| 17.15 | SIGINT 恢复 | T-0408 | T-0702 |
| 17.16 | retry 不改 Git | T-0501 | — |
| 17.17 | 空变更拒绝 | T-0301 | — |
| 17.18 | skip 冲突任务 | T-0502 | — |
| 17.19 | Skill 完整性 | T-0601, T-0602 | 路径 `~/.orchestrator/skills/orchestrator/SKILL.md` |
| 17.20 | 非沙箱边界 | T-0604 | T-0701 审查勾选；merge 行为不伪称防护 |

---

## 4. 明确不做（v1.1 范围外）

### 4.1 Future（§15.3 / §18）— 不建实现任务

| 项 | 说明 |
|----|------|
| `test` 子命令 | 已从 v1.1 CLI 移除；独立 Skill 未来接管 |
| 自定义默认分支 / TTL / 自动归档 | 项目级策略 |
| Web UI / 远程看板 | — |
| 跨主机协调 | — |
| 第三方依赖 | rich、click 等 |
| audit 远程导出 | — |
| `orch upgrade` schema 迁移 | — |
| 锁 stale **自动删除** | v1.1 仅 release 或 lock-break --force |
| 超时自动 reset-stuck | — |

### 4.2 P1 可选（不阻塞 ready，默认不排期）

| ID | 标题 | 设计锚点 | 说明 |
|----|------|----------|------|
| T-P101 | 审计 detail 增强（操作者/上下文） | §15.2 | 在强制事件已满足后增强 |
| T-P102 | 锁 stale **仅检测**提示 | §15.2, §6.5 | 检测不删除 |
| T-P103 | 项目级配置架构预留 | §15.2 | 仍硬编码 develop |

状态：`[ ]` 可选，未开始。

---

## 5. 进度汇总

| Phase | 名称 | 任务数 | 完成 | 状态 |
|-------|------|--------|------|------|
| 0 | 脚手架 | 3 | 3 | 已完成 |
| 1 | 横切基础 | 9 | 9 | 已完成 |
| 2 | 项目 / init / worktree | 4 | 4 | 已完成 |
| 3 | 入队与只读 | 4 | 4 | 已完成 |
| 4 | merge | 8 | 7 | 主体完成（SIGINT 精细对账可再加强） |
| 5 | 恢复与清理 | 5 | 5 | 已完成 |
| 6 | Skill / 安装 | 4 | 4 | 已完成 |
| 7 | 验收收口 | 3 | 2.5 | T-0701/0702 完成；T-0703 待签字 |
| **合计** | | **40** | **~39** | **实现完成；ready = 待 `docs/ready-checklist.md` D 节** |

P1 可选：3 项，不计入 v1.1 ready 分母。

---

## 6. 模块文件对照（§13 → 任务）

| 模块路径 | 主要任务 |
|----------|----------|
| `orch/__main__.py`, `cli.py` | T-0001 |
| `orch/constants.py` | T-0003 |
| `orch/errors.py` | T-0101 |
| `orch/jsonio.py` | T-0102 |
| `orch/config.py`, `registry.py` | T-0109 |
| `orch/db.py` | T-0105 |
| `orch/locks.py` | T-0108 |
| `orch/git/*` | T-0104 |
| `orch/state_machine.py` | T-0106 |
| `orch/audit.py` | T-0107 |
| `orch/merge/claim.py` | T-0401, T-0402 |
| `orch/merge/do.py` | T-0403, T-0408 |
| `orch/merge/finalize.py` | T-0404–T-0406 |
| `orch/merge/recover.py` | T-0503 |
| `orch/commands/project.py` | T-0201 |
| `orch/commands/init.py` | T-0203 |
| `orch/commands/worktree_add.py` | T-0204 |
| `orch/commands/enqueue.py` | T-0301 |
| `orch/commands/readonly.py` | T-0302–T-0304 |
| `orch/commands/merge.py` | T-0407, T-0408 |
| `orch/commands/retry.py` | T-0501 |
| `orch/commands/skip.py` | T-0502 |
| `orch/commands/reset_stuck.py` | T-0503 |
| `orch/commands/cleanup.py` | T-0504, T-0505 |
| `orch/commands/lock.py` | T-0202 |
| `~/.orchestrator/skills/orchestrator/SKILL.md`（及仓库副本） | T-0601, T-0602 |

---

## 7. 维护约定

1. 开始某任务：状态改为 `[~]`；完成 DoD 后改为 `[x]`，并更新 §5 汇总数字。  
2. 设计变更：先改 `worktree开发设计方案.md`，再同步本文件锚点与 DoD。  
3. CLI/退出码/JSON/Skill 变更：同一变更内更新设计 **§16.2** 与交付 Skill（T-0602 护栏）。  
4. 设计正文与本文件 §0.4 权威表冲突时：**先改设计消歧**，再改 task；在消歧前实现跟 §0.4。  
5. 未通过 T-0703 前，仓库对外描述保持 **实现候选**，禁止“可交付”。  
6. 修复本清单与方案的一致性时：只改 task 不改方案语义，除非用户明确要求同步设计。

---

## 8. 修订记录（task 自身）

| 日期 | 变更 |
|------|------|
| 2026-07-25 | 一致性修复：§0.4 权威节；退出码并集；全命令 `--json`；`project_locked` 审计；§13 边界；`list` 默认过滤钉死；merge 默认排空；Skill 路径与仅 §16.2；ready 门禁含 17.20 / T-0604 |
| 2026-07-25 | 实现落地：`orch/` 包、CLI 全命令、Skill/README、unittest（含 e2e）；Phase 7 验收未跑完 |
| 2026-07-25 | SIGINT 证据对账（`merge/interrupt.py`）；Windows tasklist 编码修复；§17 自动化约 2/3；`docs/acceptance-results.md` |
| 2026-07-25 | 补齐 17.1 双进程 merge、17.7–17.9、17.12、17.15 mock 中断；fixture 设置 bare user.identity |
| 2026-07-25 | T-0702 `docs/crash-drills.md`；T-0703 `docs/ready-checklist.md`；`scripts/install_skill.py` / `run_tests.ps1`；测试默认 --json 静默 |

---

*本任务清单由 v1.1 设计方案拆解生成，并经与方案的一致性修订；实现代码尚未开始。*
