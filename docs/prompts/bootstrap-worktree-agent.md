# 引导 Agent：将业务项目改造为 orch worktree 布局

将下列 Prompt 整段粘贴到 OpenCode / 其他 Agent 的新 session。  
把尖括号参数换成真实值；或在 Prompt 顶部写死 `PROJECT_*` 变量。

相关文档：

- 本机安装与多 session：`docs/opencode-multi-session.md`
- Skill 源文件：`skills/orchestrator/SKILL.md`

---

## 完整启动 Prompt（推荐）

```markdown
你是本机开发助手。请把**当前工作项目**改造成可用 `orch` 多 Agent worktree 协同的形式，并完成可验证的初始化。严格遵守下列规则。

## 背景与目标
- 工具：全局 CLI `orch`（`python -m orch` 亦可），版本 1.2.0-candidate。
- v1.2 可选：`orch runtime start/status`、`agent-start/list/takeover`（见 Skill）；本引导以 merge 队列布局为主。
- 目标布局（项目根下）：
  - `.bare.git/`  共享裸仓库
  - `main/`       仅合入用 worktree，固定在 `develop`
  - `worktrees/`  各 Agent 工作区：`worktrees/<agent>-<branch-safe>/`
- 用户配置与 DB：`~/.orchestrator/`（不要手改 DB/锁文件）。
- 合入目标分支硬编码为 **`develop`**，禁止改 target。
- 禁止在 `main/` 里 `git add` / `commit` / 手动 merge 解决冲突。
- 需要机器可读输出时一律加 `--json`。

## 输入参数（若用户未给，先问清楚再执行）
- `PROJECT_NAME`：orch 项目名（如 `myapp`，匹配 `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`）
- `PROJECT_ROOT`：orch 布局根目录（可与现有仓不同；建议独立目录如 `D:\work\myapp-orch`）
- `SOURCE_REPO`：现有 Git 工作区路径（含 `.git` 的源仓；若当前目录已是源仓则用当前目录）
- `AGENT`：默认 `agentA`
- `BRANCH`：默认 `feat/bootstrap`（须能通过 `git check-ref-format --branch`）

## 执行步骤（按序，失败即停并报告）

### 0. 环境检查
1. 运行 `orch --version`，确认可用。
2. 确认 `SOURCE_REPO` 是 Git 仓库；记录当前默认分支、是否已有 `develop`。
3. 若没有 `develop` 但有 `main`：在 **源仓或 bare 上** 创建 `develop` 指向 `main`（不要用 orch 创建首 commit）。
4. 若 `PROJECT_ROOT` 已有半成品布局，先 `orch project list --json` / 检查目录，避免重复破坏。

### 1. Bootstrap（优先用脚本）
在 open-worktree 仓库（常见 `E:\open-worktree`）执行，或等价手动步骤：

```powershell
powershell -ExecutionPolicy Bypass -File E:\open-worktree\scripts\bootstrap_project.ps1 `
  -ProjectName <PROJECT_NAME> `
  -SourceRepo <SOURCE_REPO> `
  -ProjectRoot <PROJECT_ROOT>
```

若脚本不可用，则手动：
1. `git clone --bare <SOURCE_REPO> <PROJECT_ROOT>\.bare.git`
2. 确保 `refs/heads/develop` 存在
3. bare 上配置 `user.name` / `user.email`（供 merge 提交）
4. `orch project add <PROJECT_NAME> <PROJECT_ROOT>`
5. `orch <PROJECT_NAME> init`

验收：`PROJECT_ROOT` 下存在 `.bare.git`、`main`、`worktrees`；`main` 在 `develop` 且干净。

### 2. 创建第一个 Agent worktree
```powershell
orch <PROJECT_NAME> worktree-add <AGENT> <BRANCH>
```
记录输出中的绝对路径 `WORKTREE_PATH`（形如 `...\worktrees\<AGENT>-<branch-safe>`，`/` 会变成 `__`）。

### 3. 给用户「下一步怎么开 OpenCode」的明确指令
输出两段可复制命令：
- 开发 session：`opencode <WORKTREE_PATH>`
- 协调终端示例：
  - `orch <PROJECT_NAME> pending --json`
  - 干净 commit 后：`orch <PROJECT_NAME> enqueue <AGENT> <BRANCH> <WORKTREE_PATH> --priority 1`
  - `orch <PROJECT_NAME> merge --once --json`

### 4. （可选）在 worktree 里做一次冒烟
仅当用户要求时：在 worktree 写一个无关紧要文件、commit，再 enqueue + merge --once，用 `--json` 证明闭环；默认**不要**改业务代码。

## 输出格式
完成后用中文简报：
1. `PROJECT_NAME` / `PROJECT_ROOT`
2. bare / main / 首个 worktree 路径
3. 已执行命令与关键退出码
4. OpenCode 多 session 启动命令（可复制）
5. 风险与未决（例如源仓是否仍要保留旧工作区、PATH 是否需新开终端）

## 禁止
- 删除用户源仓未确认的数据
- 在 `main/` 手工解决冲突或直接改 `develop`
- 引入第三方 Python 依赖
- 编造「已 merge / 已 init」——一切以命令输出为准
```

---

## 参数预制模板（贴在完整 Prompt 上方）

```text
PROJECT_NAME=myapp
SOURCE_REPO=D:\work\my-app
PROJECT_ROOT=D:\work\my-app-orch
AGENT=agentA
BRANCH=feat/demo
```

---

## 短版 Prompt

```text
用本机 orch 把仓库 <SOURCE_REPO> 初始化为 worktree 协同布局，项目名 <PROJECT_NAME>，根目录 <PROJECT_ROOT>。
优先跑 E:\open-worktree\scripts\bootstrap_project.ps1，再 worktree-add agentA feat/demo。
禁止动 main/ 手合 develop。全部关键命令加 --json。最后给出 opencode 打开 worktree 的路径和 enqueue/merge 示例。
```

---

## 使用提示

| 场景 | 建议 |
|------|------|
| 在源项目目录开的 OpenCode | 使用完整 Prompt，并设 `SOURCE_REPO` 为当前目录 |
| 多 Agent 并行 | bootstrap 后为每个 Agent 再 `worktree-add`，各 session 打开各自 worktree |
| orch 找不到 | 新开终端；或重跑 `E:\open-worktree\scripts\install_orch.ps1` |
| Skill 未加载 | 重跑 `python E:\open-worktree\scripts\install_skill.py` 后新开 session |
