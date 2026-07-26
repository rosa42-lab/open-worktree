# OpenCode + orch 多 Session 协同（上手清单）

版本：`orch 1.2.0-candidate`  
假设：已在本机跑过集成安装（见下方「一次性安装」）。

---

## 一次性安装（本机只需做一次）

在仓库 `E:\open-worktree`（或你的 clone 路径）执行：

```powershell
cd E:\open-worktree
powershell -ExecutionPolicy Bypass -File scripts\install_orch.ps1
python scripts\install_skill.py
```

这会：

1. 安装全局 `orch` / `orch.ps1` 到 `%USERPROFILE%\.local\bin`（任意目录可调用）
2. 写入 User `PATH` + `PYTHONPATH`
3. 把 Skill 装到 OpenCode 能发现的路径：
   - `~/.config/opencode/skills/orchestrator/SKILL.md`
   - `~/.agents/skills/orchestrator/`
   - `~/.claude/skills/orchestrator/`
   - `~/.orchestrator/skills/orchestrator/`
   - 本仓库 `.opencode/skills/orchestrator/`

**然后新开一个终端 / 新 OpenCode session**（旧 session 可能读不到新 PATH）。

验证：

```powershell
orch --version
# orch 1.2.0-candidate

# 任意目录
cd $env:TEMP
orch project list --json
```

---

## 业务仓初始化（每个要协同的项目做一次）

### 方式 A：从已有 Git 仓 bootstrap

```powershell
cd E:\open-worktree
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_project.ps1 `
  -ProjectName myapp `
  -SourceRepo D:\work\my-app `
  -ProjectRoot D:\work\my-app-orch
```

- `-SourceRepo`：现有带提交的仓库  
- `-ProjectRoot`：orch 布局根目录（会生成 `.bare.git` / `main` / `worktrees`）  
- 若源仓没有 `develop` 但有 `main`，脚本会建 `develop` 指向 `main`

### 方式 B：手动

```powershell
# 1) bare + develop 已存在于 D:\work\my-app-orch\.bare.git
orch project add myapp D:\work\my-app-orch
orch myapp init
```

检查：

```powershell
orch myapp list --json
dir D:\work\my-app-orch
# 应有 .bare.git  main  worktrees
```

---

## 多 Session 协同（你要试的流程）

### 1. 为每个 Agent 建 worktree

```powershell
orch myapp worktree-add agentA feat/login
orch myapp worktree-add agentB feat/api
```

路径示例（`/` 会变成 `__`）：

```text
D:\work\my-app-orch\worktrees\agentA-feat__login
D:\work\my-app-orch\worktrees\agentB-feat__api
```

### 2. 每个 OpenCode session 打开「自己的」目录

**Session A：**

```powershell
opencode D:\work\my-app-orch\worktrees\agentA-feat__login
```

**Session B：**

```powershell
opencode D:\work\my-app-orch\worktrees\agentB-feat__api
```

说明：

- OpenCode **可以**基于项目目录开发；这里只是让每个 session 用**不同的工作区**，避免互相覆盖。
- 在 session 里正常改代码、`git add` / `git commit`（只在本 worktree）。
- 需要时让 Agent 使用 skill **orchestrator**（描述里含 orch / worktree / pending merge）。

### 3. 开发完成后入队

在对应 worktree 目录、工作区干净时：

```powershell
orch myapp enqueue agentA feat/login D:\work\my-app-orch\worktrees\agentA-feat__login --priority 1
orch myapp enqueue agentB feat/api   D:\work\my-app-orch\worktrees\agentB-feat__api --priority 1
```

或让 Agent 执行同等命令（推荐 `--json`）。

### 4. 协调者合入 develop

单独开一个终端（或第三个 session）：

```powershell
orch myapp pending --json
orch myapp merge --once --json
# 或连续：orch myapp merge --json
```

冲突时：

```powershell
# 在源 worktree 里 merge develop、解决、commit，再：
orch myapp retry <task_id> --json
orch myapp merge --once --json
```

禁止在 `main/` 里手工 `git add/commit` 解决冲突。

### 5. 开干前建议每个开发 session 先做

```powershell
orch myapp pending --json
orch myapp diff <task_id或branch>
```

避免和队列里待合入改动打架。

---

## Session 角色速查

| 角色 | OpenCode 打开目录 | 主要动作 |
|------|-------------------|----------|
| Agent A | `worktrees/agentA-...` | 编码、commit、enqueue |
| Agent B | `worktrees/agentB-...` | 同上 |
| 协调者 | 任意 / 或不跑 OpenCode | `pending` / `merge` / `reset-stuck` |

| 不要做 |
|--------|
| 多个 session 都打开 `main/` 或同一个 worktree |
| 在 `main/` 里 merge/commit |
| 绕过 orch 直接改 `develop` |

---

## 故障速查

| 现象 | 处理 |
|------|------|
| `orch` 找不到 | 新开终端；检查 `%USERPROFILE%\.local\bin` 是否在 PATH |
| `No module named orch` | 重跑 `install_orch.ps1`；确认 `PYTHONPATH` 含 open-worktree 仓库路径 |
| OpenCode 不知道 orch | 重跑 `python scripts\install_skill.py`；新开 session；提示「使用 orchestrator skill」 |
| enqueue 失败「not clean」 | 先 commit 或清理 worktree |
| merge 退出码 5 | 有 conflict / recovery_required → `retry` 或 `reset-stuck` |

---

## 相关文件

| 文件 | 作用 |
|------|------|
| `scripts/install_orch.ps1` | 全局 orch |
| `scripts/install_skill.py` | Skill 多路径安装 |
| `scripts/bootstrap_project.ps1` | 业务仓 bootstrap |
| `skills/orchestrator/SKILL.md` | Skill 源文件 |
| `docs/prompts/bootstrap-worktree-agent.md` | 引导 Agent 改造业务仓为 worktree 的启动 Prompt |
| `docs/ready-checklist.md` | ready 门禁（与 OpenCode 无关） |
