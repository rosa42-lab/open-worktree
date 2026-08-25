# Topic 类系统行业对照（orch 1.3.0）

> **历史对照（2026-08-24）。** 下文「当前实现」描述的是闭环落地前的记录层。落地后的代码事实见 [`current-architecture.md`](current-architecture.md) §8 与 [`topic-closed-loop-v15-tasks.md`](topic-closed-loop-v15-tasks.md)：`topic-start` 供给隔离 branch+worktree；存在 `topic-enqueue`；`merged` 只表示本地 develop。
>
> 为 `docs/topic-closed-loop-plan.md` 提供依据。本文对照业界相近实践，只抽取与 orch 硬约束相容的做法。orch Topic **不是**云端 Agent 平台，也不是 Gerrit 式跨仓原子提交。
>
> 基线：orch `1.3.0`。对照日期：2026-08-24。

## 1. orch 要对齐的产品形状

当前实现（代码事实，见 `orch/commands/topic.py`）：

- `topic-start` 只插入 `topics` 行，绑定已有 coordinator 与 **已存在** 的 worktree；不创建 branch / worktree / session / worker。
- `topic-ready` 把 `--command` + `--commit` 桥接到 `verification_records` 并标记 `ready_for_enqueue`；**不入队**。
- 没有 `topic-enqueue`；ready 之后仍走 v1.1 `enqueue`。`topic_id` / `agent_run_id` / `task_id` 可空且松散。
- brief 只写成 `plan_path = "brief:..."` 标记。

目标闭环（`docs/current-architecture.md` §13 P0）：

```text
topic-start → worktree-add → agent-start → development
  → topic-ready → topic-enqueue → merge → archive/cleanup
```

**`topic-start` 必须供给隔离，而不是给已有目录贴标签。** 成功的 start = 新 branch + 专用 worktree + 专题 session（再绑 worker）。只 INSERT `topics` 行、要求调用方先建树，是现状缺口，不是目标语义。无隔离（会写到 `main/`、`develop` checkout、或别人的树）必须失败。

与 orch 永久相容、因此值得“偷”的实践：

| 原则 | 业界来源 | orch 落点 |
|---|---|---|
| start = `worktree add -b` 钉住主干 SHA | [Worktrunk](https://worktrunk.dev/switch/) `--create --base`；Git Town `hack` | 先 `rev-parse develop`，再 `git worktree add -b <branch> <path> <sha>`；记下 `base_commit` |
| 一个 ID 贯穿 branch/path/session/queue | Worktrunk 用 branch 当地址；orch 产品层用 `topic_id` | `topic_id` 是相关 ID；Git worktree 列表是路径/分支 SoT |
| Git 注册表是 SoT | `git worktree list` / bare refs | SQLite 是产品叠加；Git 与 DB 冲突则 recovery，不猜 |
| 入队喂 **orch 队列**，不抄 wt/town merge | Worktrunk `wt merge`、Git Town `ship` 会直接合进主干 | `topic-enqueue` 只调 v1.1 `enqueue`；合入仍是 `orch merge` |
| ready ≠ enqueue ≠ merge | Linear / IssueOps；Git Town 把 ship 与 hack 分开 | **两条命令**：`topic-ready` 然后 `topic-enqueue`；merge 第三条 |
| 入队再检查一遍 | GitHub merge queue 在冻结 SHA 上跑检查 | enqueue 重跑五项校验；verification SHA 必须等于冻结 SHA |
| 冻结期间锁住该 WT | v1.1 已冻 `source_commit` | enqueued 期间 orch 拒绝在该树上再开 writer / 再 ready 新 SHA |
| 单写者 / generation lease | orch lease；Claude 阻写主 checkout | 不绕过 generation++ |
| 证据 = SHA + command | merge queue `head_sha` | `verification_records` |
| 归档 ≠ 删除；落地后再 cleanup | Claude 保留有活；git-town 不删仍 checkout 的分支 | `topic-archive` 只改产品行；`cleanup --prune` 在 merged+cooldown 之后 |
| 永不在 develop checkout 写业务 | orch `main/` 只合并 | start 若目标是 `main/` 或 HEAD=`develop` → 失败 |
| 失败可 continue，不自动 undo Git | [Git Town continue/undo](https://www.git-town.com/error-commands.html) | 重复 `topic-start`/`topic-enqueue` = continue；禁止盲目 `undo` 删树 |
| 禁止 Maestro/`--ship` 自动合入 | [BeFeast/maestro](https://github.com/BeFeast/maestro) CI 绿就 merge；[git town ship](https://www.git-town.com/commands/ship.html) | 无 `topic-ship`；ready/enqueue 都不 merge |
| OMO `/goal` 不是 Topic | [`docs/omo-goal-quickstart.md`](omo-goal-quickstart.md) | `/goal` 只续跑 coord Desktop；不替代 start/ready/enqueue |

## 2. Worktree / topic branch CLI

### 2.1 Worktrunk

[Worktrunk](https://worktrunk.dev/switch/)（`wt switch --create <branch> --base <base>`）把「新分支 + 新 worktree」收成一条命令：默认从主干建 branch，路径由模板生成，可选 `-x` 在树里拉起 agent。[FAQ](https://worktrunk.dev/faq/) 的闭环是 `wt switch --create` → 干活 → **`wt merge` 合进 default branch 并清理**。

**可抄：**

- start = 供给，不是 annotate：等价于 `git worktree add -b <branch> <path> <base>`。
- `--base` 钉住起点；orch 必须钉住 **当时的 develop SHA**，不要只传会移动的 `develop` 名字。
- 一个稳定键（Worktrunk 用 branch 名）对应 path；orch 用 `topic_id`，branch/path 由 Git 派生。
- 落地后再删树，而不是 session 一结束就删。

**不抄：**

- **`wt merge`。** topic-enqueue 只把冻结 SHA **喂给 orch merge queue**，不在 Topic 命令里做 worktree merge、不删 branch。
- 默认把 worktree 放在仓库兄弟目录 `../repo.branch`。orch 布局固定为项目内 `worktrees/<agent>-<safe-branch>`，且禁止占用 `main/`。

### 2.2 git-town

[Git Town](https://www.git-town.com/index.html) 把 `hack` / `sync` / `propose` / `ship` 做成可恢复工作流。[`hack`](https://www.git-town.com/commands/hack.html) 从主干拉 feature 分支。[错误处理](https://www.git-town.com/error-commands.html)：卡住后 **`git town continue` 从失败步重放**，**`git town undo` 回到命令前的仓库状态**。[`ship`](https://www.git-town.com/commands/ship.html) 把 feature **直接合进 main 并删分支**；官方也说多数人应走托管 UI / merge queue，ship 是离线/叠层边车。worktree 占用时不能删该分支（[issue #6083](https://github.com/git-town/git-town/issues/6083)）。

**可抄：** continue = 幂等重试未完成步骤（orch：再跑同一 `topic-*`，读 `topic_events` / `last_step`）。命令是状态机不是一次性脚本。ship 与创建分支分离。

**不抄：**

- **`git town ship` / `--ship` 自动合入。** 合入只经 `orch merge`。
- `undo` 自动回滚 Git。orch 对不确定 Git 进 recovery，禁止猜测「删树/删分支是安全的」。
- stacked PR / 跨层 rebase。orch 合入单元是单条冻结 SHA 的 queue task。

### 2.3 Claude Code worktrees

[官方文档](https://code.claude.com/docs/en/worktrees)：`claude --worktree <name>` 在 `.claude/worktrees/` 建独立目录与分支；工具层阻止写回主 checkout。子 agent 可用 `isolation: worktree`。有改动的 worktree 不会在 session 结束时立刻删掉；孤儿 worktree 按 `cleanupPeriodDays` 清扫。`--worktree` 会话创建的目录 **不会** 被这场清扫删掉。

**可抄：** 一会话一目录；隔离在工具层而不是 prompt；有工作成果则保留；定期 prune 而不是立刻删。

**不抄：** 默认把 worktree 放进仓库内 `.claude/`；squash-merge 后自动当“已合入”删除（[claude-code#47630](https://github.com/anthropics/claude-code/issues/47630)）。orch 必须以 `develop` 祖先关系 + cooldown + runtime guard 为准，不能用“PR 已 squash”猜测。

### 2.4 布局类工具（gwt / conductor 风格）

[gwt](https://github.com/slowestmonkey/gwt) 一类包装器：`create` = worktree + 启动 AI，`remove` / `clean` 管干净目录。orch 已经用 `worktrees/<agent>-<safe-branch>`，不必再引入第二种布局。

**可抄：** 创建与打开绑定在同一条产品命令上。

**不抄：** `clean` 扫掉所有干净 worktree；OpenCode 社区插件在 delete 时 **自动 commit 再 `--force` 删**（[opencode-worktree](https://github.com/kdcokenny/ocx/blob/75e05a9a/facades/opencode-worktree/README.md)）。orch 禁止猜结果、禁止 force 删未证明可删的树。

## 3. Agent 编排器

### 3.1 OpenCode（orch 的 runtime）

orch 已经用共享 Server + 每 run 一个 worker + session 绑定 worktree。Topic 闭环应 **编排现有** `worktree-add` / `agent-start`，不要再开一套 session 协议。

### 3.2 Aider

[Aider Git 集成](https://aider.chat/docs/git.html)：每次编辑自动 commit，便于 undo。这是 pair-programming 语义。

**不抄自动 commit：** orch 不替 Agent 写 Git 历史；提交发生在专题 worktree 内，由 Agent/人完成。enqueue 只冻结 **已存在** 的 tip SHA。

### 3.3 OpenHands / CAID

[OpenHands](https://www.openhands.dev/) 是平台/SDK。[CAID](https://www.openhands.dev/blog/asynchronous-software-engineering-agents)（[arXiv:2603.21489](https://arxiv.org/abs/2603.21489)）用 manager 拆任务，每个 engineer 一个 git worktree，用 **结构化 JSON + git commit** 交接，而不是 agent 互聊。论文强调：软隔离（prompt 约定文件）差于物理 worktree 隔离。

**可抄：** 物理隔离；结构化 brief；测试作为门禁；coordinator ≠ 专题 worker。

**不抄：** manager 直接 `git merge` 进主干。orch 的 local `develop` 只能走 merge queue（或受锁的 release-sync）。

### 3.4 Devin 等云端 SWE agent

云沙箱 + PR 是另一种产品。orch 是 **本机 CLI + SQLite + 确定性队列**。不把 Topic 做成托管 Dev 环境。

### 3.5 Maestro 与 `--ship` 自动合入（禁止）

[BeFeast/maestro](https://github.com/BeFeast/maestro) 一类编排器：按 label 抢 issue → 每 agent 一 worktree → CI 绿 **自动 merge PR**。Git Town [`ship`](https://www.git-town.com/commands/ship.html) 是本地等价物：把 feature 合进主干并删分支。

**隔离 worktree 可抄；自动合入不可抄。** orch 没有 `topic-ship`，没有 Maestro 式 daemon。Agent 不得自 merge。`topic-ready` 不入队，`topic-enqueue` 不合入，合入只走显式 `orch merge`。

### 3.6 oh-my-openagent `/goal` 不是 Topic

[`docs/omo-goal-quickstart.md`](omo-goal-quickstart.md) 把 `/goal` 限定在 **coord Desktop** 的空闲续跑。worker 没有 `/goal`。`/goal complete` 只表示协调目标勾完，**不是** `topic-ready`，也不是 enqueue / merge / deployed。

Topic 是 orch 的产品记录 + 隔离供给。Coordinator 可以调用 `topic-start`，但不得把 `/goal` 状态机当成 Topic 生命周期。

## 4. 合并队列 / Gerrit Topic / Graphite

### 4.1 GitHub merge queue

[文档](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)：入队后构造 `merge_group`，CI 必须跑在 **group 的 `head_sha`** 上，而不是 PR 当时的 tip。检查失败则剔除。

orch v1.1 已经冻结 `source_commit`；`topic-enqueue` 必须复用这套五项校验，并要求 **verification SHA == 冻结 SHA**。ready 之后、enqueue 之前 branch 若前进，必须重新 `topic-ready`。

### 4.2 Gerrit Topics

[跨仓 Topic 提交](https://gerrit-review.googlesource.com/Documentation/cross-repository-changes.html)：`submitWholeTopic` 把同名 topic 的 change **一起提交**。每条 change 仍要单独 review；全部 submittable 后才能 Submit whole topic。

**可抄：** “topic” 是分组标签；**每条 change 自己过门禁**；提交是显式动作。

**不抄：** 多 change / 跨仓原子提交。orch 一个 Topic 对应 **一条** 队列任务、一个 branch、一个 worktree。

### 4.3 Graphite stacked PRs

[Graphite merge queue](https://graphite.dev/blog/the-first-stack-aware-merge-queue) 把 stack 当合入单元，并用 draft PR 钉住测试 SHA。GitHub 原生 stacked PR（2026-07 公测）同样按层合入。

**本阶段不抄 stacked PR。** orch 队列是单 SHA 串行 `--no-ff` merge。栈式依赖会破坏“一个 Topic 一个 task”和现有 `idx_tasks_branch_active`。

## 5. Issue / epic / spec / flag 生命周期

映射到 orch 的固定链：

```text
topic-start → worktree → agent → topic-ready → topic-enqueue → merge → archive
```

四段门禁必须分开：**ready ≠ enqueue ≠ merge ≠ deployed**。`merged` 只表示进了 local `develop`；origin/develop 与 release 仍是 v1.3 promotion，本 Topic 方案不自动 Done、不自动部署。

### 5.1 Linear：不要把 merge 当成 Done

[Ready for merge](https://linear.app/changelog/2023-11-15-github-workflow-updates) 与 [GitHub 同步](https://linear.app/docs/github) 把 In Progress / Ready for merge / Done 拆开。许多团队仍把 **PR merged → issue Done** 设成默认。

**可抄：** 状态拆开；ready 依赖稳定证据，检查失败则不进 ready。

**不抄：** merge 后自动 Done。orch：`finalize_success` 只把 topic 标 `merged`；Done/归档是显式 `topic-archive`；deployed 不是 Topic 状态。

### 5.2 IssueOps：校验过了仍要显式下一刀

[IssueOps](https://github.blog/engineering/issueops-automate-ci-cd-and-more-with-github-issues-and-actions/)：Issue 是状态机；validation 通过后仍要 `.submit`。

**可抄：** 门禁 ≠ 副作用；命令幂等（重复 `.submit` 不双跑）。对应 `topic-ready` 然后 `topic-enqueue`。

### 5.3 OpenSpec change ≈ Topic

[OpenSpec](https://openspec.dev/docs/overview)：一次 **change** = 一个文件夹（proposal / delta spec / design / tasks）。`/opsx:apply` 实现；`/opsx:verify` 对照 spec；合入代码后 **`/opsx:archive` 把 delta 折回主干 spec，change 目录搬到 dated archive**，而不是删掉。团队流建议 **PR merge 之后再 archive**（[team workflow](https://openspec.dev/docs/team-workflow)）。

**可抄：** 一 change 一 Topic；结构化 brief；verify ≠ merge；archive 是折叠+墓碑，不是 delete；幂等的 propose/apply/archive。

**不抄：** 把 OpenSpec 工具链嵌进 orch；orch 不解析 `openspec/changes/`。`--brief-file` 指向仓库内 RFC/ADR/OpenSpec proposal 即可。

### 5.4 RFC / ADR 作为 brief

[ADR + OpenSpec](https://intent-driven.dev/blog/2026/04/29/spec-driven-development-with-adr/)：proposal/design 会随 archive 进冷库，**ADR 应作为持久决策** 留在主干旁。V12-015 §27.7 的 brief 字段（goal / non-goals / acceptance / constraints / risks / plan path / verification / priority）就是 RFC 头。

**可抄：** `--brief-file` 存真实路径（`docs/adr/…`、`docs/rfcs/…`、`openspec/changes/<id>/proposal.md`）；建议提交到专题 branch。禁止再写 `plan_path = "brief:"` 标记。

### 5.5 SHA 绑定验证 + 相对 base 再测

ready 的证据必须绑 **source commit SHA + commands**（已有 `verification_records`）。enqueue / merge queue 还要相对 **当前 develop** 再验一遍：非空 `develop..source`、干净、HEAD 未动。这是 GitHub merge group 在最新 base 上重测的本地对应，不是「smart commit 即 ready」。

**不抄：** Aider/插件把一次自动 commit 或模型声称完成当成 `topic-ready`。

### 5.6 一个 `active_run_id`；`topic_id` 出现在 Git 里

产品层同时最多一个 active run（lease/generation 单写者）。Git 侧可见身份：

- branch / `git worktree list` 是 SoT；
- 建议 commit trailer `Orch-Topic: <topic_id>`（写入 brief/agent prompt，orch **不改写** 已有 commit）；
- `topic-show` 用 DB 关联，trailer 只作 provenance。

### 5.7 Flag 式归档 + cooldown prune

功能开关下线是 **retire/archive 标识**，不是立刻删代码与配置。OpenSpec archive 同样保留 dated 目录。

orch：`topic-archive` 只改产品行（UNIQUE name/branch/path 仍占着，像退休 flag key）；物理删除只走 `cleanup --prune`（merged + 24h cooldown + runtime guard + develop 祖先）。

### 5.8 不要把 sprint / OKR 做成 Topic

Jira Epic、季度 OKR、sprint 是规划桶。Topic 是 **一条可隔离开发、可冻结 SHA、可入队合入** 的工作单元。一个 OKR 可以派生多个 Topic，一个 Topic 不是一个 sprint。

## 6. 学术多 Agent SWE：实验室 vs 生产

- [SWE-agent](https://github.com/SWE-agent/SWE-agent) 与后续综述表明：**工具接口质量**往往比再加几个角色 Agent 更重要。
- MetaGPT / ChatDev 用固定 SOP 角色扮演，适合原型；生产缺持久编排、可恢复状态和失败证据（[TOSEM 综述](https://dl.acm.org/doi/10.1145/3712003)；[MetaGPT 评测](https://www.modern-datatools.com/tools/metagpt)）。
- MAS 常见失败是级联幻觉、无法判定终止、缺少 checkpoint（[MAS landscape](https://christophermeiklejohn.com/ai/agents/mas-series/2026/04/24/mas-series-01-the-landscape.html)）。
- CAID 的结论与 orch 已选架构同向：用 git worktree + 测试门禁 + 结构化指令，而不是让 Agent 聊天协调。

**对 orch 的含义：** Topic 闭环要做 **可恢复的应用流程**（每步落库、可幂等重试），不要做“一键叫一群角色把 feature 聊出来”。不确定的外部结果进 recovery/manual，禁止猜测成功。

## 7. 映射到 orch Topic：抄与不抄清单

### 必须抄进闭环方案

1. **`topic-start` 供给** branch + 专用 WT + session，不是 annotate；无隔离则失败。
2. 新 branch 从 **钉住的 develop SHA** 创建（`git worktree add -b`），记下 `base_commit`。
3. **一个 `topic_id`** 贯穿 name/branch/path/session/run/queue；Git worktree 列表是 SoT；建议 commit trailer `Orch-Topic:`。
4. 结构化 brief（RFC/ADR/OpenSpec proposal）；OpenSpec change ≈ Topic。
5. 每步持久化；重复命令 = continue；禁止盲目 undo 删 Git。
6. **`topic-ready`**：证据 = SHA + commands；不入队、不合入。
7. **`topic-enqueue`**：相对 **当前 develop** 再验 + 冻 SHA，只喂 orch 队列；冻期间锁该 WT（不再开 writer）。
8. **两条命令** ready 然后 enqueue；merge 第三条；**merged ≠ deployed**。
9. 同时最多一个 `active_run_id`；单写者 / generation lease。
10. **archive ≠ delete**（flag 式墓碑）；落地后 cooldown prune。
11. 永不在 `develop` / `main/` checkout 写业务。
12. OMO `/goal` 不是 Topic。

### 明确不抄

| 做法 | 原因 |
|---|---|
| 只 INSERT topic、要求 WT 已存在（现状 annotate） | start 必须供给隔离 |
| Worktrunk `wt merge` / Git Town `ship` / Maestro auto-merge / Agent 自 merge | 合入只经 `orch merge` |
| merge 后自动 Done；`/goal complete` 当 ready | ready ≠ enqueue ≠ merge ≠ deployed |
| smart-commit / 模型声称完成 = ready | 必须 SHA+command 证据 |
| sprint / OKR / Jira Epic 当 Topic | Topic 是可入队的隔离工作单元 |
| 盲目 `git town undo` 删树 | 不确定 Git → recovery |
| Gerrit submit-whole-topic / Graphite stack | 一个 Topic 一条 task |
| Claude 按 squash-PR 或 session 退出删树 | develop 祖先 + guard |
| Aider 每编辑一 commit；删除前自动 commit | 编排器不代提交 |
| GitLab / `candidate_pr` / `pyproject` | 本方案范围外 |

## 8. 与当前代码的差距（一句话）

业界已验证的是：**start 供给隔离（钉住主干 SHA 的 `worktree add -b`）+ 一 ID 贯穿资源 + Git 为 SoT + RFC/ADR/OpenSpec 式 brief + SHA+command 证据 + ready ≠ enqueue ≠ merge ≠ deployed + 入队相对 base 再测并冻 SHA + 冻期间锁 WT + flag 式归档 + continue 而非盲目 undo**。禁止抄 Maestro/`ship`/`wt merge`、自动 Done、smart-commit-as-ready、sprint-as-Topic、Agent 自 merge。OMO `/goal` 只驱动 coord 续跑。orch 已有 queue / lease / verification；缺的是把 Topic 从 annotate 升成供给型幂等编排，并补上独立的 `topic-enqueue`。任务拆解见 `docs/topic-closed-loop-plan.md`。
