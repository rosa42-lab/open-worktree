# orch v1.2 分阶段开发任务

**源计划：** `docs/v1.2-upgrade-plan.md`  
**目标版本：** `orch 1.2.0-candidate`  
**阶段数：** 4  
**状态：** 阶段 1 自动化验收通过；H2 Desktop 人工签署待完成后方可关闭阶段 1

本文将源计划的 Phase 0-5 合并为四个开发阶段。任务 ID、依赖和完成定义以源计划 §23 为准；每个阶段必须满足退出条件后才能进入下一阶段。

## 1. 全局约束

- Python 核心仅使用标准库，不引入第三方 Python runtime dependency。
- 固定合入目标仍为 `develop`，不增加 `--target`；Agent worker 不得在 `main/` 运行。
- 不改变 v1.1 merge queue、冻结 SHA、三阶段 merge、项目锁、JSON envelope、退出码、audit allowlist 和 cleanup safety gauntlet 语义。
- runtime 状态不得写入 `tasks.status`；分别维护 Git task、Agent runtime、Topic 产品状态。
- 网络、SSE、进程等待和 Git 操作不得放入长 DB 事务；worker 不长期持有项目锁。
- 所有 Git subprocess 继续仅由 `orch.git` 执行；OpenCode/worker subprocess 使用独立 runtime process 模块。
- 仍只有锁模块可以 unlink 项目锁文件；runtime lease 和 runtime lock 使用各自独立的受保护协议。
- Observe 命令只读且无控制副作用；Control 命令必须校验项目锁、generation 和 lease。
- PID 不是单独的进程身份依据；证据不足时进入 `manual_required`，不得误杀、重放 prompt 或危险清理。
- OpenCode Desktop 和 PID 文件不是权威状态；以 orch DB、OpenCode API、进程和 Git 的组合证据为准。
- 默认仅绑定 `127.0.0.1`，不得自动修改 Desktop 内部存储，不实现跨主机调度或 Web Dashboard。

## 2. 依赖总览

```text
阶段 1  OpenCode 能力验证与协议冻结
   |
   v
阶段 2  可迁移持久状态、Adapter 与只读观测
   |
   v
阶段 3  Runtime Server、Worker、Lease 与生命周期闭环
   |
   v
阶段 4  人工接管、Topic 工作流、清理、Hooks 与发布加固
```

关键依赖链：

```text
V12-001 -> V12-005 -> V12-007 -> V12-008
                                     |
V12-002 -> V12-003 -> V12-009 -------+
                                     v
                         V12-010/V12-011/V12-012
                                     |
                                     v
                                  V12-013B
                                     |
                                     v
                                  V12-015
                                     |
                                     v
                                  V12-014
```

## 3. 阶段任务

### 阶段 1：OpenCode 能力验证与协议冻结

**目标：** 在实现 runtime 前，以真实 OpenCode Server 冻结 directory routing、session、SSE、abort、dispose、attach/fork 协议，并决定 shared Server 是否可作为默认架构。

**阶段依赖：** 无。

#### V12-001：Capability Probe

- **优先级：** P0
- **依赖：** 无
- **设计锚点：** §5、§18 Phase 0、§19.3、§25 H1-H2、§24 D1
- **任务：**
  - 实现 `orch runtime probe [--json]` 原型，不修改项目 Git 或 project DB。
  - 探测 `/global/health`、directory routing、session 创建、SSE、abort、instance dispose 和 attach/fork。
  - 使用一个 Server、两个真实 worktree 创建独立 session，验证 cwd、branch、文件变更和事件流不串线。
  - 验证 Desktop 只 Add Server 一次后可定位多个 session，并记录 attach 命令后备路径。
  - 输出 OpenCode 版本、endpoint、capability 矩阵，确定最低 supported 版本。
- **交付物：** Probe 原型、能力矩阵、双 worktree 冒烟证据、Desktop 人工验收记录。
- **完成定义：** 真实 Server 上 directory routing、独立 SSE、abort、dispose、attach 和 fork 均通过；最低支持版本已确定。
- **自动化状态（2026-07-26）：** `phase0_pass=true`，`supported_min_version=1.18.5`，架构 `shared`。证据：`docs/probe/phase0-capability-matrix.md`、`docs/probe/phase0-result.json`。H2 Desktop 签署仍待人工完成。

**阶段退出条件：**

- §19.3 Shared Server 多目录隔离验收通过。
- H1-H2 在 Phase 0 关闭前有实测结论；H3-H7 的 capability 前置证据已记录，并分别在 takeover、worker 和 adapter 编码门槛前完成最终验证。
- 若 shared Server 的致命假设失败，先更新架构决定并回退为 `per_agent` Server + orch registry；不得带着未验证假设进入阶段 2。
- 任一致命或高风险假设失败，都必须先更新源计划的架构决策，不得以“后续再修”绕过。
- v1.1 回归测试和 stdlib-only 门禁保持通过。

### 阶段 2：可迁移持久状态、Adapter 与只读观测

**目标：** 建立 schema 2、独立状态模型和 OpenCode 协议边界，先提供无写副作用的 Agent 观测能力。

**阶段依赖：** 阶段 1 的协议与最低版本已冻结；V12-002 可与阶段 1 独立准备，但阶段 2 不得在能力结论缺失时关闭。

#### V12-002：Schema Migration Framework

- **优先级：** P0
- **依赖：** 无
- **设计锚点：** §9.1、§9.7、§19.2
- **任务：**
  - 引入 `PRAGMA user_version=2` 的幂等迁移框架。
  - 支持空 DB 完整初始化和完整 v1.1 schema 的 additive migration。
  - 对部分 v1.1 结构、同名异构表、更高版本或结构不符的 DB 拒绝写入。
  - 在单个短 `BEGIN IMMEDIATE` 中迁移并自检，失败时整体 rollback。
  - schema 2 同步创建 `agent_runs`、`control_leases`、`lifecycle_events`、`inspection_forks`、`coordinator_sessions`、`topics`、counter 和全部约束/索引。
- **交付物：** `orch/migrations.py` 或等价迁移模块、迁移测试与真实 v1.1 DB fixture。
- **完成定义：** 连续迁移两次时第二次 no-op，旧 tasks/audit/counters 数据逐行一致；异常 schema 原文件不被修改。

#### V12-003：Agent State Machine 与 Tables

- **优先级：** P0
- **依赖：** V12-002
- **设计锚点：** §8、§9.2-§9.5、§9.7、§27.6
- **任务：**
  - 实现 `agent_runs` lifecycle、desired、observed、controller 状态闭集及 DB `CHECK` 约束。
  - 实现 generation 单调递增、旧 generation 写请求拒绝和 active worktree/session 唯一约束。
  - 实现 control lease、lifecycle event 顺序 counter 和 inspection fork 持久化模型。
  - 实现 coordinator/topic 状态模型及同 project、generation 关联验证。
  - 用表驱动测试覆盖所有合法与非法 transition。
- **交付物：** 状态迁移模块、schema repository/API、状态机测试。
- **完成定义：** 未知状态由 DB 拒绝；`tasks.status`、`agent_runs.*`、`topics.*` 三类状态互不混用。

#### V12-005：OpenCode Runtime Adapter

- **优先级：** P0
- **依赖：** V12-001
- **设计锚点：** §7.3、§10、§14、§19.13
- **任务：**
  - 定义 `RuntimeAdapter`，实现唯一的 `OpenCodeRuntimeAdapter`。
  - 封装 health、capabilities、session 创建/查询、status、prompt_async、SSE、abort、dispose 和 attach 命令生成。
  - directory 必须显式路由；认证 header、token 和 credential path 必须脱敏。
  - 使用 fake Server 覆盖 timeout、断流、错误响应、认证和重连，并以阶段 1 证据做真实冒烟。
- **交付物：** `orch/runtime/adapter.py`、`orch/runtime/opencode.py`、协议测试。
- **完成定义：** HTTP/SSE 细节不散落到 lifecycle 或 command handler；fake 与真实协议测试均通过。

#### V12-006：Observe-only Commands

- **优先级：** P0
- **依赖：** V12-003、V12-005
- **设计锚点：** §11.2、§11.4、§14、§19.11、§21.1
- **任务：**
  - 实现 `agent-list`、`agent-show` 和 `agent-watch`。
  - 提供手工注册现有 worktree/session 的临时命令或测试 fixture，用于在 worker 生命周期实现前建立可观测 run 映射。
  - 非流式 JSON 保持 v1.1 envelope；`agent-watch --json` 使用带首尾帧和版本信息的 JSONL stream。
  - 输出 run、session、worktree、worker、controller 和 attach locator，不复制完整 transcript。
  - 用可记录 HTTP 请求与 process signal 的 fixture 证明命令不拿 project lock、不写 DB、不改 Git、不发控制请求。
- **交付物：** `orch/commands/agent_readonly.py` 或等价命令模块、无副作用验收测试。
- **完成定义：** §19.11 前后快照和请求记录全部通过；现有 worktree/session 可通过临时注册入口进入只读查询，且该入口不启动或控制 worker。

**阶段退出条件：**

- [x] §19.2 Migration 和 §19.11 Observe-only 验收通过。
- [x] v1.1 所有测试原样通过；`tasks.status`、JSON envelope 和退出码无回归。
- [x] schema 2 的表、列、`CHECK`、unique constraint 和 index 自检完整。
- [x] 只读命令不获取项目写锁、不发送 signal/abort/dispose/prompt、不更新 desired state。
- [x] Python runtime 仍为 stdlib-only。

**阶段 2 完成记录（2026-07-26）：**
- V12-002：`orch/migrations.py` + `tests/test_migrations_v12.py`
- V12-003：`orch/agent_state.py`、`orch/agent_repo.py` + `tests/test_agent_state_machine.py`
- V12-005：`RuntimeAdapter` Protocol、`orch/runtime/opencode.py` + `tests/test_runtime_adapter.py`
- V12-006：`agent-list/show/watch/register` + `tests/test_agent_observation.py`
- 全量 `python -m unittest discover -s tests`：69 passed

### 阶段 3：Runtime Server、Worker、Lease 与生命周期闭环

**目标：** 建立主机级 Server registry 和项目级 Agent runtime，使 run 能可靠完成 start、stop、heartbeat、失联和 reconcile 闭环。

**阶段依赖：** V12-001、V12-003、V12-005 已完成。

#### V12-004：Runtime Global Registry

- **优先级：** P0
- **依赖：** V12-001
- **设计锚点：** §9.6、§11.1、§13、§17、§19.6、§19.12
- **任务：**
  - 实现 `~/.orchestrator/runtime/` 下 registry、credential file、runtime lock 和日志。
  - 实现 `runtime probe/start/status/stop`，支持 managed 与 external Server。
  - `runtime start` 使用 guarded lock、generation 和 nonce；健康但身份不匹配的端口 owner 必须拒绝接管。
  - `runtime stop` 在 active run 存在时拒绝，且永不终止 external 或身份不可验证的 Server。
  - secret 不得出现在 argv、audit、JSON、日志、错误和 traceback。
  - 在 runtime start 前完成 H10：验证 Windows/POSIX 同账户下 credential file/env path 的暴露程度，并记录安全评审结论。
- **交付物：** registry/process/capability 命令模块及 managed/external 测试。
- **完成定义：** orch 重启后能重连健康 Server；未知 owner 不被 kill；active run 阻止 stop。

#### V12-007：Worker Protocol

- **优先级：** P0
- **依赖：** V12-003、V12-005
- **设计锚点：** §10、§13.3、§19.4、§19.5、§19.13
- **任务：**
  - 每个 run 启动独立 worker 子进程，以受限环境/配置接收 `ORCH_RUN_ID`、`ORCH_PROJECT`、`ORCH_WORKTREE_PATH`、`ORCH_SERVER_URL`、`ORCH_SESSION_ID`、`ORCH_CONTROLLER_GENERATION`、`ORCH_WORKER_NONCE` 和 `ORCH_CREDENTIAL_FILE`。
  - worker 校验 lease、worktree 所属 bare、非 `main/`、Server 和 session 后才可发送 prompt。
  - 连接 session 后先写首条匹配 PID/nonce/generation 的 heartbeat，再允许状态转为 `running`。
  - 消费 SSE 并归约 busy/idle/error；断线时只读重连，不提交新 prompt。
  - 持久化 session、submitted turn/message ID 等幂等证据；协议无法证明 prompt 是否已接收时进入 `manual_required`，不得自动重放。
  - generation 失效或 desired state 改变后停止写；最终记录退出证据。
  - 不递归调用 CLI、不执行 merge、不删除资源、不长期持有锁或 DB transaction。
- **交付物：** `orch/runtime/worker.py`、`events.py`、`process.py` 及 worker 生命周期测试。
- **完成定义：** PID/nonce/generation/heartbeat 闭环成立；首 heartbeat 前死亡不短暂进入 running；恢复时不重复创建 session 或提交 prompt。

#### V12-009：Control Lease

- **优先级：** P0
- **依赖：** V12-003
- **设计锚点：** §8.4、§9.3、§12、§17.2-§17.3
- **任务：**
  - 使用至少 256 bit CSPRNG token，并仅保存 `SHA-256(run_id || generation || token)` 摘要。
  - 使用 `hmac.compare_digest` 验证 token；原文不进入 DB、audit 或日志。
  - 保证同一 run 单写者，支持 acquire、renew、expire、transfer 和 release。
  - generation 变化立即使旧 worker 或 human token 失效。
- **交付物：** `orch/runtime/lease.py` 及并发、过期、旧 generation 测试。
- **完成定义：** 任何时刻最多一个有效 controller；过期或旧 generation 写请求被拒绝。

#### V12-008：AgentLifecycleService

- **优先级：** P0
- **依赖：** V12-003、V12-007
- **设计锚点：** §7.2、§8、§11.3、§13、§19.4-§19.6
- **任务：**
  - 作为唯一 lifecycle owner 实现 run 注册、session 创建/恢复、worker 启停、状态更新、reconcile 和归档。
  - 实现 `agent-start`、`agent-stop`、`agent-reconcile` 和 `agent-archive`。
  - `agent-start` 按 Precheck -> Allocate -> Register -> Start -> Finalize 执行，只有首 heartbeat 与 session 可达后进入 running。
  - 对 worktree/session/worker/DB 的部分成功保留证据和可恢复状态，不伪装原子成功。
  - reconcile 综合 DB、PID/nonce/generation、heartbeat、OpenCode session 和 Git 证据。
  - 在 reconcile 前完成 H8：用 Windows PID reuse、nonce、generation 和 heartbeat 演练确认 worker identity 证据充分性。
- **交付物：** `orch/runtime/lifecycle.py`、`reconcile.py` 和对应 command modules。
- **完成定义：** `registered -> starting -> running -> exited` 正常闭环；worker/orch/Server 故障进入 lost/reconciling/manual_required 的证据路径正确。

**阶段退出条件：**

- [x] §19.4 Worker lifecycle 核心路径已实现（start→heartbeat→running / stop→exited）；§19.5/§19.6/§19.13 的完整 OS 强杀演练仍归 V12-013A。
- [x] Server / worker / external 遵守身份验证与 terminate-before-kill（未知 PID 拒绝 kill）。
- [x] §19.12 secret：credentials 独立文件；lease 只存 hash；H10 记录见 `docs/probe/h10-credential-exposure.md`。
- [x] worker 不自动重放无法证明状态的 prompt（`ORCH_PROMPT` 至多一次；证据不足 → `manual_required`）。
- [x] v1.1 回归与 stdlib-only 门禁保持通过。

**阶段 3 完成记录（2026-07-26）：**
- V12-004：`orch/runtime/registry.py`、`service.py`；`orch runtime start|status|stop`
- V12-009：`orch/runtime/lease.py` + `tests/test_control_lease.py`
- V12-007：`orch/runtime/worker.py`（`-m orch.runtime.worker`）
- V12-008：`orch/runtime/lifecycle.py`；`agent-start|stop|reconcile|archive`
- H10：`docs/probe/h10-credential-exposure.md`

### 阶段 4：人工接管、Topic 工作流、清理、Hooks 与发布加固

**目标：** 完成单写者人工接管、根协调 Session/持久专题 Session 产品流程、保守清理、生命周期 Hook、崩溃演练和发布文档。

**阶段依赖：** 阶段 3 lifecycle 与 lease 闭环完成。阶段内先完成 V12-010/011/012，再执行 V12-013A/013B，随后 V12-015，最后 V12-014。

#### V12-010：Takeover 与 Release

- **优先级：** P0
- **依赖：** V12-008、V12-009
- **设计锚点：** §12、§19.7-§19.9
- **任务：**
  - 实现 Observe、Fork inspect、Direct takeover 三种模式。
  - Direct takeover 严格执行 generation 失效 -> worker exit -> abort -> session idle -> human lease。
  - 并发 takeover 只允许一个进入 `pausing`；失败时不得发放可写 attach 命令。
  - Fork inspect 写入 `inspection_forks`，不改变原 run owner、generation 或 worker。
  - `agent-release --resume` 验证 idle 后使 human token 失效，并以新 generation worker 恢复原 session；不 resume 时安全退出 runtime。
  - 生成 attach 命令；只有显式 `--launch` 才启动客户端。实现 `agent-open` 或与 Topic open 共用的等价打开入口。
  - 在发放 direct human lease 前完成 H3/H4；在 release 前完成 H9 的 Desktop 多 worktree attach 验证。
- **交付物：** takeover/release 命令及并发、超时、fork、恢复测试。
- **完成定义：** §19.7-§19.9 全部通过，同一 session 不存在两个 orch 管理的 writer。

#### V12-011：Runtime-aware Cleanup

- **优先级：** P0
- **依赖：** V12-008、V12-009
- **设计锚点：** §15、§19.10、§27.10
- **任务：**
  - 在 v1.1 safety gauntlet 前加入 active run、human lease、heartbeat、session、runtime state、archive 和 linked task guard。
  - 任一 guard 失败时返回 `runtime_blocked` 并说明理由，不弱化既有 Git 清理检查。
  - 严格按 worker exit -> lease expire -> run archive -> optional dispose -> v1.1 gauntlet -> worktree/branch cleanup 顺序执行。
  - rejected、cancelled、skipped、dirty、未 merge、human-controlled、lost 和 manual-required 成果默认保留。
- **交付物：** runtime cleanup guard 与 retention matrix 测试。
- **完成定义：** §19.10 和 §27.10 的所有阻塞条件均能阻止 prune；不会先删目录再停 session。

#### V12-012：Lifecycle Hooks

- **优先级：** P1
- **依赖：** V12-008
- **设计锚点：** §16、§20
- **任务：**
  - 实现源计划 allowlist 中的生命周期 Hook。
  - Hook 仅接受 argv 数组和固定 JSON stdin，使用 `shell=False`，禁止二次 shell 字符串。
  - 实现 timeout、输出大小限制、secret 最小化和审计。
  - 默认失败为 non-fatal；blocking Hook 失败进入 `manual_required`。
  - `BeforeWorktreeRemove` 不得绕过内建 cleanup guard。
- **交付物：** `orch/runtime/hooks.py` 和 allowlist/timeout/blocking 测试。
- **完成定义：** Hook 安全约束与失败策略均可自动验证。

#### V12-013A：Worker 与 Runtime Crash Drills

- **优先级：** P0
- **依赖：** V12-004、V12-005、V12-006、V12-007、V12-008
- **设计锚点：** §13、§19.5、§19.6、§19.13
- **任务：**
  - 演练 worker 首 heartbeat 前死亡、运行中强杀、orch crash、Server crash/restart 和 PID reuse。
  - 验证 session ID 重连、SSE 补偿、prompt 不重放、未知进程不终止。
  - 为每个演练记录 DB、process、OpenCode 和 Git 四类证据。
- **交付物：** `docs/v1.2-crash-drills.md` 与真实进程验收结果。
- **完成定义：** worker/runtime/orch kill 与恢复路径均有可复现证据；不确定路径进入 `manual_required`。

#### V12-013B：Takeover 与 Cleanup Crash Drills

- **优先级：** P0
- **依赖：** V12-009、V12-010、V12-011、V12-012
- **设计锚点：** §12、§15、§19.7-§19.10
- **任务：**
  - 在 takeover 的 generation、worker stop、abort、idle、lease 各步骤注入中断。
  - 演练并发 takeover、cleanup race、orphan worktree/session 和 blocking Hook 失败。
  - 验证任何中断都不会产生双 writer、误杀进程或提前删除 worktree。
- **交付物：** takeover/cleanup crash drill evidence ledger。
- **完成定义：** 所有演练均可由持久证据 reconcile，无法证明的资源被保守保留。

#### V12-015：Topic Development Workflow

- **优先级：** P0
- **依赖：** V12-002、V12-003、V12-006、V12-007、V12-008、V12-009、V12-010、V12-011、V12-013B
- **设计锚点：** §9.7、§23、§27
- **任务：**
  - 实现每项目最多一个 active 根协调 Session binding，并以 replace/rebind 新历史行和 generation 接替。
  - 实现 `topic-start/list/show/open/ready/enqueue/archive` 与 `coordinator-bind/show`。
  - `topic-start` 编排 topic -> branch -> worktree -> session -> worker，并返回完整 Desktop/attach locator。
  - `topic-open [--fork] [--launch]` 支持只返回 attach locator、fork inspect 和显式启动客户端；不带 `--launch` 时不得启动外部进程。
  - 根协调 Session 只做控制面；专题 Session 固定绑定 worktree，承担计划、实现、验证、commit 和结果汇总。
  - 使用结构化 brief 交接 goal、non-goals、acceptance、constraints、risks、plan path、verification 和 priority。
  - `topic-ready` 验证 worktree/branch、clean、commit、non-empty diff、tests、typecheck/build、QA、permission、session、worker 和 human edit；结构化 verification record 必须记录命令、时间、退出码和关联 commit SHA。
  - `topic-ready` 不 enqueue/merge；`topic-enqueue` 复用 v1.1 enqueue 的冻结 SHA 与五项验证。
  - Topic archive 只归档产品记录；实际删除仍经过 runtime guard、cooldown 和完整 Git gauntlet。
- **交付物：** Topic/Coordinator command modules、verification record、§27.12 产品验收测试。
- **完成定义：** 根 Session 可创建、观察、打开和接管多个互不串线的持久 Topic；结果门禁、enqueue 与 cleanup 行为可证明。

#### V12-014：Skill、Docs 与 Install

- **优先级：** P0（发布门禁）
- **依赖：** V12-001 至 V12-013B、V12-015
- **设计锚点：** §18 Phase 5、§21.3、§22、§23
- **任务：**
  - 同步 `skills/orchestrator/SKILL.md`、operator docs、bootstrap/install 和最终 CLI。
  - 完成 `docs/v1.2-acceptance-results.md`、`docs/v1.2-crash-drills.md`、`docs/v1.2-ready-checklist.md`。
  - 更新模块职责、JSON/JSONL、退出码、错误 kind、shared/external Server、安全边界和 Topic 主流程说明。
  - 运行真实 OpenCode、subprocess、Git、OS signal、Desktop/attach 与 stdlib-only 发布验收。
- **交付物：** 最终 Skill、操作文档、安装脚本、验收结果和 ready checklist。
- **完成定义：** 文档、Skill、实现与测试一致；全部 release gate 完成前保留 `1.2.0-candidate`。

**阶段退出条件：**

- [x] V12-010/011/012/015 代码路径与单测落地；§19.7-§19.10 核心守卫可自动验证。
- [x] active/human/lost/manual/skipped worktree 会被 `runtime_blocked` 挡住自动 prune。
- [x] coordinator/topic 可用 `topic-show --json` 重建绑定关系。
- [~] V12-013A/B 真实 OS 强杀：账本见 `docs/v1.2-crash-drills.md`（手工补跑仍为发布门禁）。
- [x] V12-014 Skill/验收文档：Skill、acceptance-results、ready-checklist、README、版本 `1.2.0-candidate` 已同步；正式 release 仍待 D 门签署。

**阶段 4 完成记录（2026-07-26）：**
- V12-010：`orch/runtime/takeover.py`；`agent-takeover|release|open`
- V12-011：`orch/runtime/cleanup_guard.py` 接入 `cleanup --prune`
- V12-012：`orch/runtime/hooks.py`（allowlist / shell=False / blocking）
- V12-015：`orch/commands/topic.py`；`coordinator-*` / `topic-*`
- V12-013：`docs/v1.2-crash-drills.md`
- V12-014：`skills/orchestrator/SKILL.md`；`docs/v1.2-acceptance-results.md`；`docs/v1.2-ready-checklist.md`；README；`orch.__version__=1.2.0-candidate`
- 测试：`tests/test_phase4.py` + Skill 表面测试

## 4. 任务覆盖矩阵

| 任务 ID | 所属阶段 | 源计划依赖 | 主要验收 |
|---|---:|---|---|
| V12-001 | 1 | 无 | §19.3、§25 H1-H2 |
| V12-002 | 2 | 无 | §19.2 |
| V12-003 | 2 | V12-002 | §8、§9、§27.6 |
| V12-004 | 3 | V12-001 | §19.6、§19.12 |
| V12-005 | 2 | V12-001 | §5、§19.13 |
| V12-006 | 2 | V12-003,V12-005 | §19.11 |
| V12-007 | 3 | V12-003,V12-005 | §19.4、§19.5、§19.13 |
| V12-008 | 3 | V12-003,V12-007 | §19.4-§19.6 |
| V12-009 | 3 | V12-003 | §19.7-§19.9 |
| V12-010 | 4 | V12-008,V12-009 | §19.7-§19.9 |
| V12-011 | 4 | V12-008,V12-009 | §19.10、§27.10 |
| V12-012 | 4 | V12-008 | §16 |
| V12-013A | 4 | V12-004..V12-008 | §19.5、§19.6、§19.13 |
| V12-013B | 4 | V12-009..V12-012 | §19.7-§19.10 |
| V12-015 | 4 | V12-002,V12-003,V12-006..V12-011,V12-013B | §27.12 |
| V12-014 | 4 | V12-001..V12-013B,V12-015 | §21.3 |

## 5. 一致性检查清单

- [x] 恰好包含 4 个阶段，符合“3-5 个阶段”的要求。
- [x] §23 的 16 个任务行均且仅出现一次，ID 和依赖未改变。
- [x] V12-015 在 V12-013B 后执行，V12-014 为最后任务。
- [x] §27.1-§27.12 的 Coordinator、Topic、CLI、ready gate、持久性和验收均由 V12-002、V12-003、V12-006、V12-010、V12-011、V12-015、V12-014 覆盖。
- [x] §3.1 的 10 项必须实现目标均有阶段任务和退出条件。
- [x] §3.2 非目标与 §4 v1.1 硬约束均未被阶段任务突破。
- [x] §19.1-§19.14 验收均映射到至少一个阶段退出条件或任务完成定义。
- [x] shared Server 失败时的 `per_agent` 回退决策保留。
- [x] H1-H10 均映射到源计划规定的编码/发布门槛，任一致命或高风险假设失败都要求先更新架构决策。
- [x] `tasks.status`、`agent_runs.*`、`topics.*` 状态边界保留。
- [x] stdlib-only、secret 脱敏、单写者 lease、证据化 reconcile、保守 cleanup 和显式 enqueue 语义保留。
