# Feasibility Validation Report: 专题连续开发

**日期：** 2026-08-30  
**机器：** Windows 10，PowerShell  
**范围：** 不调用付费模型；验证 **旗标、进程模型、代码路径、PATH 身份**。未做「真跑一轮 agent 改代码」的 e2e。  
**结论入口：** [topic-continuous-dev-final.md](topic-continuous-dev-final.md)

---

## Summary

测了 **13** 条假设（A1–A13）。**8 条已证实或证伪到足以改设计**；**3 条部分**（需 adapter 层消化）；**2 条刻意不烧模型**（live inject 的运行时往返）。

- **Blocking（Option A 崩塌）：** A4 ❌、A5 ❌、A6 ❌、A7 ❌、A11 ❌
- **Blocking（「所有执行器都像 OpenCode Server」崩塌）：** A9 ❌、A10 ❌
- **支撑连续开发的事实：** A1 ✅、A2 ✅、A3 ⚠️（仅部分执行器）、A8 ✅（产品路径）

**Verdict：** Option A **不可实施**。调整后的分档方案（Class A attach / Class B resume）**可以进入架构终稿**；**不可以**在 V18 前写代码。

---

## Environment

| 工具 | PATH | 版本 |
|------|------|------|
| opencode | `...\npm\opencode.ps1` | 1.18.5 |
| claude | `...\npm\claude.ps1` | 2.1.214 (Claude Code) |
| grok | `~\.grok\bin\grok.exe` | 0.2.112 |
| agent | `~\.grok\bin\agent.exe` | **与 grok 同一二进制** |
| orch | `~\.local\bin\orch.ps1` | 1.3.0 |
| codex | `...\npm\codex.ps1` | 0.145.0 |
| cursor-agent / aider / copilot | 不在 PATH | — |

环境意外：

- `agent` **不是** Cursor Agent CLI，是 Grok 的别名。任何「spawn agent」设计若按名字猜，会打到错误运行时。
- `grok doctor` 报告 `NO_COLOR` 已设置（本会话继承）。编排子进程必须显式清理/设定环境，不能假设交互 TTY 能力。
- OpenCode `--help` 经 npm `.ps1` 包装把 banner 写到 stderr，PowerShell 显示为 `NativeCommandError`。解析 help 必须以 exit 0 + stdout/stderr 合并为准，不能把 stderr 当失败。

---

## Assumptions

| # | Assumption | Risk | Result | Evidence |
|---|------------|------|--------|----------|
| A1 | headless CLI 一次 prompt 后退出 | 致命 | ✅ | claude `-p`「Print response and exit」；grok `-p/--single`「prints … and exits」；`opencode run [message]` |
| A2 | 对话可在进程退出后续上 | 致命 | ✅ | opencode `-s/--session`、`-c/--continue`；claude `-r/--resume`、`--session-id`；grok `-r/--resume`、`-c/--continue`；codex `exec resume` / `codex resume` |
| A3 | 进程活着时可注入下一轮 | 致命 | ⚠️ | OpenCode: `serve` + `attach`/`run --attach` + 已实现 `send_prompt_async`。Grok: `agent leader` / `agent serve` / `agent stdio`。Codex: `app-server`（experimental）。Claude: `-p` 退出；`--bg` 是产品后台 agent，不是 orch 协议。**未做付费往返** |
| A4 | orch heartbeat 会叫醒思考 | 致命 | ❌ | `orch/runtime/worker.py`：heartbeat 只 `UPDATE agent_runs`；prompt 最多一次且 `prompt = None  # never auto-replay`；间隔 2s |
| A5 | 三种「goal/heartbeat」是同一机制 | 致命 | ❌ | omo `/goal`：Desktop session 空闲续跑；Cursor `/goal`：对话状态 CreateGoal；orch heartbeat：PID/nonce 存活证据。`orch --help` 无 goal 子命令 |
| A6 | 父 CLI spawn 子 CLI 共享会话 | 高 | ❌ | 各 CLI 会话靠 `--session`/`--resume` 显式传递；spawn 默认新进程新会话。Grok `--session-id` 明确「must not already exist… Does not resume」 |
| A7 | PATH 上的 `agent` 是通用执行器 | 高 | ❌ | `agent --version` → `grok 0.2.112` |
| A8 | Topic 产品路径不依赖 Server | 高 | ✅ | `docs/current-architecture.md` §8；`topic-start --start-session` 才是 session 开关；V17 已落地无 Server 闭环 |
| A9 | RuntimeAdapter 配置即可换执行器 | 高 | ❌ | `worker.py`/`lifecycle.py`/`takeover.py` **直接 import** `OpenCodeRuntimeAdapter`；Protocol 存在但唯一实现 + 硬编码 |
| A10 | Claude/Grok/Codex = OpenCode HTTP session | 高 | ❌ | Claude：CLI + `-p` stream-json，无 `opencode serve` 对等物。Grok：leader unix socket + `agent serve` WS（默认 `127.0.0.1:2419`），不是 OpenCode `/session`。Codex：`app-server` stdio/unix/ws，experimental |
| A11 | 永续 TUI 可被 orch 无 TTY 编排 | 高 | ❌ | 三家默认入口都是 interactive TUI；脚本面是 `-p` / `run` / `exec`。无 TTY 的 TUI attach 不在已验证能力里 |
| A12 | `/goal` 跨关窗复活 | 中 | ❌ | `docs/omo-goal-quickstart.md`：「关 Desktop / 换 session，Goal 不会跨 session 复活」 |
| A13 | 每子任务常驻进程成本可接受 | 中 | ⚠️ | 未测账单；架构上 N 个 TUI = N 份上下文。设计改为按需 spawn 后本条不再阻塞 |

---

## Tests

### A1 / A2 — CLI 进程模型与 resume 旗标

```powershell
opencode --version          # 1.18.5
opencode run --help         # --session, --continue, --attach, --format json
claude --version            # 2.1.214
claude --help               # -p print and exit; -r --resume; --session-id
grok --version              # 0.2.112
grok --help                 # -p/--single exits; -r --resume
codex --version             # 0.145.0
codex exec --help           # resume subcommand
```

**Expected：** 存在 headless 且退出；存在 resume。  
**Actual：** 与 Expected 一致。  
**Status：** ✅（旗标级）。未跑真实 `--resume` 往返（避免烧模型）。

### A3 — live inject

```powershell
opencode serve --help       # headless server
opencode attach --help      # --dir --session --fork
grok agent --help           # stdio | headless | serve | leader
grok agent leader --help    # --no-exit-on-disconnect
codex app-server --help     # experimental; stdio:// unix:// ws://
```

代码：`OpenCodeRuntimeAdapter.send_prompt_async` + worker 循环在 session 仍可达时保活。

**Status：** ⚠️ OpenCode 路径已实现；Grok/Codex 有对等 **长驻后端** 但协议不同；Claude 无对等 HTTP server。

### A4 — heartbeat 叫醒

读 `orch/runtime/worker.py` 第 94–150 行。  
**Actual：** 心跳是 SQLite `heartbeat_at`；思考只在 `ORCH_PROMPT` 且 `prompted` 尚未置位时发生一次。  
**Status：** ❌ Option A 的「用心跳定期激活」在 orch 里不存在。

### A5 / A12 — goal 机制

读 `docs/omo-goal-quickstart.md`；本会话 Cursor `CreateGoal`；`orch --help` 无 goal。  
**Status：** ❌ 三者不可互换。A12 按 omo 文档为 ❌。

### A7 — PATH 身份

```powershell
Get-Command agent   # ~/.grok/bin/agent.exe
agent --version     # grok 0.2.112
```

**Status：** ❌

### A9 — 可插拔

`rg OpenCodeRuntimeAdapter orch/` → worker/lifecycle/takeover/probe/agent_readonly 全硬编码。  
`CapabilityMatrix.required_pass` 仍按 OpenCode shared-server 能力一刀切。  
**Status：** ❌ 配置切换不存在；换执行器必须改 adapter 注入（V19 方向）。

---

## Anti-Patterns (Verified)

### 错误：用 orch heartbeat 当 `/goal` 续跑

- **Why：** heartbeat 是存活证据，禁止自动重放 prompt。
- **Observed：** `never auto-replay`；无 goal 子命令。
- **Correct：** 下一轮思考由 conductor 调 `agent-start` / 新 prompt，或 Class A adapter 的显式 `send_prompt`。

### 错误：每个子任务一个永续 CLI，再 spawn 子 CLI 当员工

- **Why：** 子进程不继承 session；会话必须显式 id。
- **Observed：** grok `--session-id` 拒绝已存在 id；claude/opencode resume 都要旗标。
- **Correct：** orch 持有 `session_id`；worker/spawn 传入，不靠进程树。

### 错误：假定 `agent` 是 Cursor/通用运行时

- **Why：** 本机 PATH 上是 grok。
- **Correct：** 执行器按 **配置的二进制路径 + 探测到的 profile** 启动，禁止按命令名猜。

### 错误：把 Claude `-p` 当长连接员工

- **Why：** help 写明 print and exit。
- **Correct：** Class B：`--resume` 按切片再 spawn。要 live inject 就不要选 Claude 当 Class A。

### 错误：把 grok TUI 当 orch 协议

- **Why：** 默认可交互；编排面是 `grok agent leader|stdio|serve` 或 `-p`。
- **Correct：** TUI 只给人类 attach；orch 只打 leader/stdio/headless。

### 错误：OPENCODE_* / NO_COLOR 继承

- 历史：`OPENCODE_RUN_ID` 会让 `opencode run` 误以为附在父 session。
- 本机：`NO_COLOR` 让 grok doctor 报 limited-color。
- **Correct：** spawn 时显式构造 env allowlist。

---

## Round 2（方案调整后）

调整后的假设（见 analysis / final）：

| # | 新假设 | Result |
|---|--------|--------|
| B1 | 连续单位 = Topic 的 `session_id`（或 resume id），不是子任务 PID | ✅ 与 A2 一致 |
| B2 | Class A 仅在探测到 serve/leader/app-server 时启用 | ✅ 旗标存在；OpenCode 已实现 |
| B3 | Class B 足以支撑「切片边界连续」：commit/conflict/retry 时再 spawn | ✅ 与 Topic 闭环事件对齐；无 live inject |
| B4 | Conductor `/goal` 只驱动 orch CLI，不进 worker | ✅ omo 文档 + worker 无 goal |
| B5 | 产品路径无 session 仍可闭环 | ✅ A8 |
| B6 | 换执行器只改 adapter，不改 Topic/queue | ⚠️ 今天代码未做到；**设计约束**，实现排 V19 后 |
| B7 | 不需要自研「永续 CLI 壳」 | ✅ OpenCode worker 与 grok leader 已覆盖 Class A 形态 |

**Round 2 verdict：** 调整后方案 **可行性通过（架构级）**。剩余未知是各 Class A 协议字段级对接，属实现期 probe，不阻塞终稿冻结。

**Ready to implement?** **No** — V18 未做；本方案是 V19 之后的执行器波次。Ready to **adopt as architecture**? **Yes**.
