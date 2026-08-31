# 专题连续开发 — 初版方案（Pass 1）

**日期：** 2026-08-30  
**状态：** 已由后续可行性与对抗审查取代；保留作为起点与假设清单。  
**权威终稿：** [topic-continuous-dev-final.md](topic-continuous-dev-final.md)

---

## Goal

### 问题

专题（Topic）开发是多切片、跨冲突/重试、跨进程重启的。当前 orch 产品路径可以把 Git/队列做完，但「员工式」连续执行面绑死在 OpenCode HTTP Session + Python worker 上。用户提出：每个独立子任务启动一个永续 CLI，用 goal 心跳定期激活，再由它开启/关闭子 CLI，以支撑专题连续开发。

### 必须做到

- 一个专题在多次切片、冲突修复、进程退出后仍能接着干，而不是每次从零开对话。
- orch 继续拥有 worktree、Topic 生命周期、merge 队列、lease；执行器可替换。
- 空闲时能被定期叫醒去检查/推进，而不是人肉盯着 TUI。

### 不得做

- 不绕过 merge 队列。
- 不把 TUI 当编排协议。
- 不在 V18（schema 5 / 图 / Saga）之前实现本方案。
- 不让 `/goal` 进 worker。
- 不把「子任务」做成可复用的永久员工进程树。

---

## 用户原案（Option A，起点）

```text
每个独立子任务
  └─ 永续 CLI 进程（不退出）
        ├─ goal 心跳 → 定期激活思考
        └─ spawn / kill 子 CLI → 做具体工作
```

意图翻译成可检验句子：

1. 连续性挂在 **进程还活着**。
2. 激活机制是 **goal 心跳**（空闲后续跑）。
3. 编排机制是 **父 CLI 开关子 CLI**。
4. 粒度是 **每个独立子任务** 一个永续实体。

---

## Options

| Option | 做法 | 稳定性 | 复杂度 | 与 orch 契合 |
|--------|------|--------|--------|----------------|
| A 永续 CLI + goal 心跳 + 子 CLI | 每子任务一个常驻进程，goal 定时叫醒，再 spawn 执行 CLI | 未知 | 中 | 与 Topic/lease 双编排 |
| B 长会话 attach（现 OpenCode 路径） | Server/leader 上 session 永续；orch worker 短命；heartbeat 只证活 | 已有代码 | 低（已实现） | 高，但绑 OpenCode |
| C 磁盘 resume + 按切片 spawn | `session_id` 落盘；ready/conflict/新 prompt 时再跑 headless | 各 CLI 均有 resume 旗标 | 中 | 高；无 live inject |
| D 混合：按执行器分档 | Class A attach / Class B resume / Class C TUI 人类 / fail-closed | 取决于探测 | 中 | 高；V19 capability 同向 |
| E 把 orch 做成通用 supervisor 壳 | 自研永续进程、心跳、子进程树，不依赖任何 agent session | 自研风险 | 高 | 重复 OpenCode worker + grok leader |

**Pass 1 暂选 D** 作为要验证的方向（不是已证明）。A 作为必须证伪或证实的对照。

---

## 关键决策（未验证）

| 决策 | 理由（待证） |
|------|----------------|
| 连续单位是 session，不是 CLI PID | 各 CLI 的 `--resume` / `--session` 暗示对话在磁盘或 server |
| `/goal` 只属于 conductor | omo 文档已写 worker 无 `/goal` |
| orch heartbeat ≠ 叫醒思考 | worker 循环只写 DB |
| 子任务 ≠ 新员工 | Topic 已有切片语义（commit → ready → enqueue） |
| 子 CLI 只跑工具 | 嵌套 agent 会双编排 |

---

## Assumptions（必须在 Pass 2 验证）

| # | 假设 | 风险 |
|---|------|------|
| A1 | 本机存在可脚本化的 headless CLI，且默认会在一次 prompt 后退出 | 致命 |
| A2 | 至少一种执行器能把对话续在 **进程退出之后**（`--session` / `--resume`） | 致命 |
| A3 | 至少一种执行器能 **进程活着时** 注入下一轮 prompt（server/leader/attach） | 致命（若要 live 员工） |
| A4 | orch worker heartbeat 会触发新一轮模型思考 | 致命（Option A 依赖此） |
| A5 | omo `/goal` 与 Cursor `/goal` 与 orch heartbeat 是同一机制 | 致命（Option A 混用） |
| A6 | 父 CLI spawn 子 CLI 会继承同一会话身份 | 高 |
| A7 | `agent` PATH 命令是可编排的 Cursor/通用 agent | 高 |
| A8 | Topic 产品路径不依赖 OpenCode Server | 高（KEEP vs 旧 plan 冲突） |
| A9 | `RuntimeAdapter` 已可插拔，换 Claude/Grok 只改配置 | 高 |
| A10 | grok/claude/codex 提供与 OpenCode serve 对等的 HTTP session API | 高 |
| A11 | 永续 TUI 可被 orch 无 TTY 地 start/stop 并中途投 prompt | 高 |
| A12 | `/goal` 能跨 Desktop 关窗 / 换 session 复活 | 中 |
| A13 | 每个子任务一个常驻进程的成本可接受 | 中 |

---

## Open questions

1. 「独立子任务」是 Topic，还是 Topic 内的一次切片？
2. 连续开发是否要求 **中途注入**（冲突文件已在 WT），还是切片边界 spawn 即可？
3. conductor 用 omo `/goal` 还是人类/Cursor `/goal` 驱动 orch CLI？
4. 本波是否只冻结架构，实现排在 V19 之后？
