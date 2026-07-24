**完整开发设计方案：多 Agent Worktree 编排系统（Orchestrator）**

**版本**：v1.0（最终可交付版）  
**目标**：一次全局安装，支持任意数量项目，多个 Agent 并行开发同一仓库，系统强制按优先级 + 时间顺序安全合入，并提供 Agent 可主动读取待合入代码的能力。  
**交付物**：一个可执行的全局 CLI 工具 `orch` + 一个标准 Skill 文件。

---

### 1. 项目目标与核心原则

**目标**
- 支持同一项目下多个 Agent 并行开发，互不干扰。
- 所有合入必须经过统一队列，按 **priority 升序 → submitted_at 升序** 顺序执行。
- Agent 可主动读取所有待合入任务的真实代码变更（diff、文件列表、提交历史）。
- 全局安装，多项目隔离，零侵入项目目录（配置与 DB 全部放在用户目录）。

**核心原则**
1. 每个项目拥有独立的 SQLite 数据库（物理隔离）。
2. 使用 Git bare repository + worktree 模型，所有 Agent 共享同一个 `.bare.git`。
3. 禁止任何 Agent 直接 merge 到 `develop`，必须通过 `orch <project> merge`。
4. 合入过程中一旦发生冲突，立即停止后续任务，等待人工/Agent 修复后 `retry`。
5. 所有检查命令（pending / diff / changes / log）只读，不修改任何状态。

---

### 2. 系统架构

```
~/.orchestrator/                          # 全局配置中心
├── config.json                           # 项目注册表
└── data/
    └── <project-name>/
        └── orchestrator.db               # 该项目独立任务库 + 审计日志

<project-root>/                           # 用户实际项目目录（由用户提供）
├── .bare.git                             # 共享裸仓库（必须存在）
├── main/                                 # 合并专用 worktree（必须始终在 develop 分支）
├── test/                                 # 测试专用 worktree（由系统管理）
└── worktrees/                            # Agent 工作区（由 worktree-add 创建）
    ├── <agent>-<branch-safe>/
    └── ...
```

**config.json 结构**
```json
{
  "projects": {
    "alpha": "/home/user/projects/my-web-app",
    "beta": "/home/user/projects/my-api"
  }
}
```

---

### 3. 数据模型（SQLite）

每个项目一个独立数据库文件：`~/.orchestrator/data/<project>/orchestrator.db`

**表 tasks**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PRIMARY KEY | `task-<unix_timestamp>` |
| agent_name | TEXT NOT NULL | 提交 Agent 名称 |
| branch_name | TEXT NOT NULL | 分支名 |
| worktree_path | TEXT NOT NULL | 绝对路径 |
| priority | INTEGER DEFAULT 1 | 数字越小优先级越高 |
| status | TEXT DEFAULT 'pending' | pending / merging / merged / conflict / skipped |
| submitted_at | DATETIME | 入队时间 |
| merged_at | DATETIME | 合入完成时间 |
| commit_hash | TEXT | 合入后的 commit |
| attempts | INTEGER DEFAULT 0 | 尝试次数 |

**表 audit_log**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | |
| task_id | TEXT | |
| action | TEXT | enqueued / merge_started / merged / conflict / retried / skipped / reset_stuck |
| detail | TEXT | 可选详情（commit hash 或错误信息） |
| created_at | DATETIME | |

**索引**：`CREATE INDEX idx_status_priority ON tasks(status, priority, submitted_at);`

---

### 4. CLI 完整命令规范

工具名称：`orch`  
安装位置：`~/.local/bin/orch`（需在 PATH 中）

#### 4.1 项目管理（全局）
```
orch project list
orch project add <name> <path>
orch project remove <name>
```

#### 4.2 项目操作（必须指定项目名）
```
orch <project> init
orch <project> worktree-add <agent> <branch> [--base develop]
orch <project> enqueue <agent> <branch> <worktree_path> [--priority N]
orch <project> list [--all] [--json]
orch <project> pending [--json]                  # Agent 核心命令
orch <project> diff <task_id|branch>             # 完整 diff
orch <project> changes <task_id|branch>          # 文件列表 + stat + log
orch <project> log <task_id|branch>
orch <project> merge                             # 顺序合入
orch <project> test
orch <project> cleanup [--prune]
orch <project> retry <task_id>
orch <project> skip <task_id>
orch <project> reset-stuck
```

#### 4.3 关键行为要求

**enqueue 必须执行的 4 项校验**
1. worktree_path 必须存在且是有效 git worktree（存在 `.git` 文件）。
2. branch 必须在裸仓库中存在。
3. worktree 必须干净（`git status --porcelain` 为空）。
4. 同一 branch 不允许存在多个 `pending` 状态的任务。

**pending 命令输出内容**（人类可读时）
- 任务元数据
- `git diff --stat develop...branch`
- 最近 5 条 `git log --oneline develop..branch`

**diff / changes / log**
- 支持传入 `task-xxxx` 或直接传 branch 名。
- 使用三点语法 `develop...branch`（正确计算变更范围）。

**merge 流程**
1. 检查 `main/` 工作区存在且干净。
2. 强制 `checkout develop`。
3. 按 priority + submitted_at 取第一条 pending。
4. 状态改为 merging，attempts+1。
5. 执行 `git merge --no-ff <branch>`。
6. 成功 → 记录 commit_hash，状态改为 merged。
7. 失败 → `merge --abort`，状态改为 conflict，**立即停止**后续任务。
8. 循环直到没有 pending 或遇到冲突。

**cleanup --prune**
- 删除 status=merged 的数据库记录。
- 同时执行 `git worktree remove --force` + `git branch -d` + `worktree prune`。

---

### 5. 核心工作流

#### Agent 标准工作流
1. `orch <project> pending [--json]` → 主动读取所有待合入代码。
2. 对相关任务执行 `orch <project> diff <task_id>` 阅读真实代码。
3. `orch <project> worktree-add <自己名字> <新分支>`。
4. 在生成的 worktree 中开发并 commit。
5. `orch <project> enqueue ...`（自动校验）。
6. 由协调 Agent 或 CI 执行 `orch <project> merge`。

#### 冲突处理流程
1. merge 遇到冲突 → 任务标记为 conflict，后续任务暂停。
2. 人工/Agent 进入 `main/` 工作区解决冲突并 commit。
3. 执行 `orch <project> retry <task_id>` → 状态回到 pending。
4. 重新执行 `merge`。

---

### 6. Skill 定义（必须同步交付）

文件路径建议：`orchestrator/SKILL.md`（或放入用户 Agent 的 skills 目录）

```yaml
---
name: orchestrator
description: Multi-agent worktree orchestration system. Use when working on a project managed by the orch CLI, when you need to inspect pending merge tasks, read the actual code waiting to be merged, enqueue your own finished work, or trigger sequential merges. Triggers include orch, pending, worktree, multi-agent coordination, sequential merge, develop branch.
---
```

Skill 正文必须包含：
- 核心原则（禁止直接 merge develop）
- 所有 CLI 命令速查
- 推荐 Agent 行为（启动时先 pending，完成后再 enqueue）
- JSON 输出说明
- 项目目录结构提醒

（完整正文见上一轮最终版本，实现时必须一字不差保留）

---

### 7. 错误处理与边界情况

| 场景 | 处理方式 |
|------|----------|
| 项目未注册 | 明确提示 `orch project add` |
| 裸仓库不存在 | 所有 git 操作失败并提示 |
| main/ 不存在或不干净 | merge 直接拒绝 |
| 任务卡在 merging（进程崩溃） | `reset-stuck` 强制改回 pending |
| 重复入队同一分支 | enqueue 拒绝 |
| worktree 不干净 | enqueue 拒绝 |
| 分支不存在 | enqueue / diff 等拒绝 |
| 冲突后 | 停止队列，提示 retry |

---

### 8. 技术实现要求

- 语言：Python 3.10+
- 依赖：仅使用标准库（sqlite3、subprocess、argparse、pathlib、json、datetime）
- 入口：`#!/usr/bin/env python3`
- CLI 解析方式：
  - `orch project ...` 走独立子解析
  - 其他统一为 `orch <project> <cmd> ...`
- Git 调用统一封装为 `run_git(project, args, cwd=None)`，始终使用 `--git-dir <bare>`。
- 所有路径使用 `pathlib.Path`，相对路径统一 resolve 到项目根。
- 默认分支硬编码为 `develop`（后续可配置化，本版本不要求）。

---

### 9. 安装与初始化步骤（必须写进文档）

```bash
# 1. 安装 CLI
mkdir -p ~/.local/bin
# 将完整脚本保存为 ~/.local/bin/orch
chmod +x ~/.local/bin/orch
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc   # 或 zshrc

# 2. 注册项目（项目需已具备 .bare.git + main worktree）
orch project add alpha /path/to/project

# 3. 项目初始结构准备（一次性）
cd /path/to/project
git init --bare .bare.git
git --git-dir=.bare.git worktree add main -b develop
# 在 main/ 中完成首次 commit
```

---

### 10. 实现优先级（交给本地 Grok Build 时使用）

**P0（必须完整实现）**
1. 项目管理（add/list/remove）
2. 数据库初始化与 schema
3. worktree-add
4. enqueue（含完整 4 项校验）
5. pending / diff / changes / log（含 task_id 解析）
6. merge（顺序 + 冲突停止）
7. retry / skip / reset-stuck
8. cleanup --prune
9. 完整 Skill 文件

**P1（建议实现）**
- 所有命令的 `--json` 输出
- test 命令
- 详细的 audit_log 记录

**P2（可后续迭代）**
- 项目级配置（自定义默认分支、自动删除策略等）
- 超时自动 reset-stuck
- 简单 Web 看板

---

### 11. 验收标准

1. 同一项目可同时注册多个 Agent worktree，互不干扰。
2. 多个任务入队后，`merge` 严格按 priority + 时间顺序执行。
3. 发生冲突时队列正确停止，retry 后可继续。
4. `pending` 和 `diff` 能真实展示待合入代码变更。
5. 全局安装后，在任意目录均可操作任意已注册项目。
6. Skill 文件可被 Agent 系统正确加载并指导行为。

---

**本设计方案已完整、自洽、可直接交给本地 Grok Build 进行实现。**  
实现时请严格遵循以上数据模型、命令行为与错误处理，不要擅自简化校验逻辑或改变顺序合入规则。