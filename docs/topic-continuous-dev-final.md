# 专题连续开发 — 最终方案（已验证）

**日期：** 2026-08-30  
**状态：** 可行性 **通过**（架构级）；多维度审视 **通过**（R1–R3）。**禁止本波实现。**  
**前序：** [plan](topic-continuous-dev-plan.md) · [feasibility](topic-continuous-dev-feasibility.md) · [analysis](topic-continuous-dev-analysis.md)  
**依赖：** V17 / V18 / V19 已落地；本方案是 V20 执行器波次。架构总览：[current-architecture.md](current-architecture.md) §7.5。开发预备：[topic-continuous-dev-tasks.md](topic-continuous-dev-tasks.md)。

---

## 一句话

专题连续开发 = **Topic 与执行会话 id 永续 + worker 按需 + conductor `/goal` 催 orch CLI**。  
不是「每个子任务一个永续 CLI，用 goal 心跳去开关子 CLI」。

---

## Environment（本机已核实）

| 工具 | 版本 | 脚本入口 | 续会话 | 长驻后端 |
|------|------|----------|--------|----------|
| orch | 1.3.0 | CLI | n/a | Python worker + 可选 `opencode serve` |
| opencode | 1.18.5 | `run` | `--session` / `--continue` | `serve` + `attach` |
| claude | 2.1.214 | `-p` 后退出 | `--resume` / `--session-id` | 无对等 HTTP；`--bg` 不采用 |
| grok | 0.2.112 | `-p/--single` 后退出 | `--resume` / `--continue` | `agent leader` / `agent serve` / `stdio` |
| codex | 0.145.0 | `exec` | `exec resume` / `resume` | `app-server`（experimental） |

PATH 陷阱：`agent.exe` = grok，不是 Cursor。

---

## Goal 与非目标

**要解决：** 一个专题跨切片、冲突、进程退出后仍能接着开发；执行器可替换；空闲能被催促。

**不要：**

- 每子任务常驻 CLI
- 用 `/goal` 或 heartbeat 当 worker 的定时思考
- 父 CLI 进程树当编排器
- TUI 当协议
- 跳过 V18/V19 直接写第二个 adapter
- 绕过 merge 队列

---

## 三层身份（不得合并）

```text
Topic (id, branch, canonical worktree, lifecycle)
  └── execution_ref (kind, session_id or resume_id, capability_digest)
        └── run (pid, nonce, generation, heartbeat_at, lease)
```

| 问题 | 看哪一层 |
|------|----------|
| 专题是否还在、能否 enqueue | Topic |
| 能否接着聊 | execution_ref |
| 这个进程还是不是原写者 | run + heartbeat |

心跳：**只更新 `heartbeat_at`。禁止在心跳里 send_prompt。**

---

## 角色

| 角色 | 负责 | 不负责 |
|------|------|--------|
| Conductor + `/goal` | 空闲后续跑 orch 命令、逼验收、写 checkpoint | 不进 worker；不 spawn 业务 CLI 员工 |
| orch 核心 | WT、Topic、队列、lease、attest | 不定「代码是否做好」 |
| RuntimeAdapter | 按 Class 创建/恢复/投递/中止/探活 | 不碰 merge |
| Class A 后端 | 真正的长会话 | 不持有 project.lock |
| Class B 进程 | 一次切片的 headless 跑完 | 不常驻 |
| 工具子进程 | 测试/git | 不是 agent |

粒度：**一个 Topic ≤ 一个活 execution_ref。** 切片不是新员工。

---

## 执行器分档

探测结果只许 true/false，未知 = false。

**Class A（attach）** — 仅当存在可探活的长驻后端，且能把 prompt 投进 **已有** session：

- OpenCode：现有 `serve` + HTTP `/session` + worker（已实现）
- Grok：`grok agent leader --no-exit-on-disconnect` + `stdio`/`serve`（协议未接）
- Codex：`app-server`（experimental，失败则降级）

**Class B（resume）** — headless 退出，orch 在这些 **Topic 事件** spawn，并传入同一 resume id：

- `topic-start --start-session`（首轮）
- 冲突/retry 需要模型再改时
- conductor 显式再投 prompt 时  
不是 timer，不是 heartbeat。

**Class C** — 人类 `attach` TUI。orch 只给命令字符串。

**Class 0** — 禁止 `--start-session`。无 session 的 Topic 闭环仍然合法（V17）。

---

## 控制面（谁开谁关）

只有 orch lifecycle：

```text
agent-start / topic-start --start-session
  → probe Class
  → 创建或复用 execution_ref
  → 启动 worker（A）或 headless 子进程（B）
  → 首条存活证据（A: heartbeat；B: pid+exit 记录）才许标 running

agent-stop / abort
  → generation++
  → 停 worker/子进程
  → A 可 abort session；B 只杀进程，保留 resume id
```

**没有**「永续父 CLI 再开关子 CLI」这一层。

Conductor 伪代码（概念，不是新命令）：

```text
/goal 推进 Topic T 直到验收
loop when idle:
  orch topic-show T
  if conflict: 在源 WT 修或 agent-start 再投 prompt
  if dirty+on branch: 人/agent 提交 → topic-ready → topic-enqueue
  if pending: merge --once
  if 验收+证据: update_goal complete
```

---

## 与现有代码的关系

**保持：**

- Topic 闭环无 Server
- worker 不自动重放 prompt
- nonce + generation + lease
- 未知能力在 V19 必须变 false（今天 `OpenCodeRuntimeAdapter.capabilities()` 仍有硬编码 True，属 V19 债）
- 禁止同时持有 `project.lock` 与 `runtime.lock`

**V19 必须先做完才能接第二执行器：**

- 复合身份 `{server_id, generation, nonce, capability_digest, version}`
- `assert_runtime_gate(operation, expected)`
- worker/lifecycle **注入** `RuntimeAdapter`，删除硬编码 import

**本方案落地时（V19 之后）才做：**

- `ExecutorClass` 探测
- `ClaudeResumeAdapter` / `GrokLeaderAdapter` 等
- spawn env allowlist（清 `OPENCODE_*`，不继承随意 PATH 别名）
- 配置项是 **二进制绝对路径 + kind**，禁止写死命令名 `agent`

---

## File / 模块（预告，不施工）

| 区域 | 将来变化 | 现在 |
|------|----------|------|
| `orch/runtime/adapter.py` | Class + 按 operation 的 gate | OpenCode 形状的 CapabilityMatrix |
| `orch/runtime/worker.py` | 依赖注入 adapter | 硬编码 OpenCode |
| `orch/runtime/lifecycle.py` | 同上 | 硬编码 |
| Topic/queue | **不改** | V17 |
| schema | 不加「永续 CLI」表；执行器 kind 若需要则加性列，跟 V19 身份走 | schema 4 |

---

## Anti-patterns（禁止再提案）

1. 每子任务永续 CLI + `/goal` 心跳 + 子 CLI 员工  
2. 心跳里 `send_prompt`  
3. worker 挂 omo `/goal`  
4. 用 Cursor `/goal` 当 orch 调度器（它可以催人类侧会话写 orch 命令，但不是 runtime）  
5. `Popen(["agent", ...])` 不解析 PATH 真实身份  
6. 把 Claude `--bg` / 产品 Agent Teams 当隔离基础  
7. 为了连续开发要求产品路径必须有 session  
8. 跳过 V18 写 Grok adapter  

---

## Implementation checklist（冻结顺序）

- [x] V18：schema 5、图 CAS、Saga 列（见 [topic-closed-loop-v18-plan.md](topic-closed-loop-v18-plan.md)；C1 部分写路径仍待接）
- [x] V19：runtime 复合身份、capability 未知=false、adapter 注入、双锁禁令（见 [topic-closed-loop-v19-plan.md](topic-closed-loop-v19-plan.md)）
- [x] Probe：Claude `docs/probe/claude-protocol.md`（旗标级；付费 `--resume` 未跑）。Grok/Codex protocol 未写。
- [x] Class B adapter 一条路径（Claude `-p --resume` 夹具；见 [topic-closed-loop-v20-plan.md](topic-closed-loop-v20-plan.md)）
- [ ] Class A 第二条路径（Grok leader **或** 继续只支持 OpenCode）
- [x] env allowlist + PATH 解析测试（`agent`=grok 回归）
- [x] 文档：architecture §7.5 Class 表；omo-goal-quickstart 保持「goal 仅 coord」

**本 goal 不勾选上述实现项。**

---

## Verification checklist

- [x] 本机四套 CLI 版本与 headless/resume/长驻旗标已读 help
- [x] orch worker 心跳与「不重放 prompt」已读源码
- [x] 三种 goal/heartbeat 已区分
- [x] PATH `agent` 身份已核实
- [x] RuntimeAdapter 硬编码已 grep
- [x] Option A 已用失败假设否决
- [x] 调整后再验 B1–B7
- [x] R1 架构 / R2 契约 / R3 防御 已过
- [ ] 真实模型 `--resume` 往返（编码前，非本 goal）
- [ ] Grok leader / Codex app-server 字节协议（编码前，非本 goal）

---

## 验收标准（本 goal）

1. 有一份明确否决 Option A 的证据表。  
2. 有一份可实施的分层方案，且不要求本周改代码。  
3. 可行性与多维度审视均判定通过，剩余未知降为「实现前 probe」，不降为「应该可以」。  

三条均已满足。
