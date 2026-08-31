# 专题连续开发 — 开发方案预备（V20）

> **不是可开工的 TDD 切片清单（Class B/A）。** V18 / V19 / V20 Class B 夹具已落地。Grok/Codex 与付费 Claude `--resume` 仍未接。本文只把终稿拆成可被 `writing-plans` 消费的模块边界、缺口和门禁。
>
> **规格权威：** [topic-continuous-dev-final.md](topic-continuous-dev-final.md)  
> **现状锚点：** [current-architecture.md](current-architecture.md) §7.5、§13、不变量 19–21  
> **证据：** [topic-continuous-dev-feasibility.md](topic-continuous-dev-feasibility.md)、[topic-continuous-dev-analysis.md](topic-continuous-dev-analysis.md)

写细化开发方案时从本文 + 终稿 + architecture 对代码，**不要**从聊天记忆开写。V19 落地前把 V20 写成「现在就 `agent-start` 接 Claude」即违规。

---

## 冻结顺序

```text
V18 schema 5 + 图 CAS + Saga 列
  → V19 复合身份 + capability 未知=false + lifecycle/worker 注入 RuntimeAdapter + 禁双锁
    → V20-P0 各目标执行器 protocol probe（最小 --resume / leader 往返）
      → V20-B Class B adapter（建议 Claude `-p --resume`）
        → V20-A Class A 第二条路径（Grok leader 或明确只保留 OpenCode）
```

V19 的 adapter 注入是 V20 的 **前置模块**，不要在 V20 计划里再发明一套注入。

---

## 代码 vs 终稿（预备用缺口表）

| # | 终稿要求 | 代码现状 | 状态 | 归属 |
|---|---------|----------|------|------|
| 1 | Topic 闭环无 Server | V17 产品路径已落地 | ✅ 已做 | — |
| 2 | heartbeat 不 `send_prompt` | `worker.py` `never auto-replay` | ✅ 已做 | 保持 |
| 3 | `/goal` 不进 worker | worker 无 goal；omo 文档已写 | ✅ 文档 | 保持 |
| 4 | `RuntimeAdapter` Protocol | `orch/runtime/adapter.py` + `factory.py` 注入 | ✅ V19 | — |
| 5 | 未知能力 = false | 默认矩阵除 health/auth 外 false；`assert_runtime_gate` | ✅ V19 | — |
| 6 | lifecycle/worker 注入 adapter | `AgentLifecycleService(..., adapter=)`；worker 走 factory | ✅ V19 | — |
| 7 | 禁双锁 | `dual_lock_forbidden`；stop draining+fencing | ✅ V19 | — |
| 8 | Class B spawn + resume id | Claude 假二进制夹具 + lifecycle 按 Class 分支 | ✅ V20 夹具 | 真二进制未付费验证 |
| 9 | Class A 非 OpenCode | 无 | ❌ | V20-A |
| 10 | env allowlist / PATH 解析 | `orch/runtime/binary.py`；`agent.exe` + kind≠grok 拒绝 | ✅ V20 | — |
| 11 | `docs/<tool>-protocol.md` | Claude 旗标级；付费 `--resume` 未跑 | ⚠ 部分 | V20-P0 |
| 12 | 永续 CLI 壳 / 父 CLI 开关子 CLI | 明确不做 | ✅ 否决 | 禁止项 |

---

## 模块（细化计划时按此拆，勿打散 Topic/queue）

### M0 — 不改（V20 禁止碰的内核）

Topic 生命周期、merge queue、attestation、`canonical_worktree_path`、UNIQUE 墓碑。连续开发不改 §8 产品路径。

### M1 — V19 注入（本预备的依赖，不在 V20 开工）

- **文件：** `orch/runtime/lifecycle.py`、`worker.py`、`takeover.py`、`probe.py`、`agent_readonly.py`
- **做：** 唯一通过 `RuntimeAdapter` 发协议；删除硬编码 import；`assert_runtime_gate(operation, expected)`
- **完成定义：** 换假 adapter 的单测能跑完 start/heartbeat/stop，不碰 OpenCode HTTP

### M2 — V20-P0 Protocol probe

- **做：** 每个目标二进制一份 `docs/<tool>-protocol.md`：版本、headless 退出码、resume 真往返、env 污染、错误串
- **门禁：** 无此文件不得开 M3/M4 实现 PR
- **风险：** 付费模型往返；`OPENCODE_*` / `NO_COLOR` 继承

### M3 — V20-B Class B

- **Purpose：** 切片事件时 spawn headless + 同一 resume id
- **Depends：** M1、M2（至少 Claude）
- **事件：** `topic-start --start-session`、冲突/retry、conductor 再投 prompt——**不是 timer，不是 heartbeat**
- **预告文件：** 新 `orch/runtime/claude.py`（或等价）；lifecycle 按 Class 分支；spawn env allowlist
- **完成定义：** 假二进制夹具证明：第二次调用 argv 含 resume id；失败 fail-closed；不标 running 当进程已退且无 session 可达证据

### M4 — V20-A Class A（可选第二条）

- **Depends：** M1、M2（Grok leader **或** Codex app-server）
- **做：** 对等 send_prompt/abort/health；每 Topic 独立 socket/url；stop 卸残留
- **不做：** 自研 supervisor 壳；绑定 Claude `--bg` / Agent Teams

### M5 — 横切

- 配置：**二进制绝对路径 + kind**，禁止写死命令名 `agent`
- 回归：`Get-Command agent` 指向 grok 时不得误调度
- 文档：Skill 不把 `/goal` 写进 worker；architecture §7.5 与实现同步

---

## 写细化方案时必须带上的门

1. **TDD：** 先失败测试再实现（与仓库既有 Topic 波次相同）。
2. **双标签：** 架构通过 ≠ 实施就绪；M2 未过不得宣称 M3 可合入。
3. **KEEP：** 不 ALTER `tasks`；不建 `topic_events`；Git 不进 `BEGIN`；产品路径仍允许无 session。
4. **反模式：** 见终稿 Anti-patterns；写入细化计划的「禁止项」一节，不要只链过去。
5. **切片粒度：** 一 PR 一类执行器或一次注入，不把 V18 图约束和 Claude spawn 捆在一起。

---

## 建议的 V20 任务 ID（落地细化计划时展开，现在不要填测试名）

| ID | 模块 | 开工前提 |
|----|------|----------|
| V20-P0a | Claude `-p --resume` protocol | V19 合入 **或** 明确与 V19 并行只写 docs/probe |
| V20-P0b | Grok leader/stdio protocol | 同上 |
| V20-P0c | Codex app-server：探测失败则标记 Class 0/B-only | 同上 |
| V20-001 | env allowlist + 二进制身份 | V19 |
| V20-002 | Class B Claude adapter + 夹具 | V19 + V20-P0a |
| V20-003 | lifecycle 按 Class 分支（B 不走 SSE heartbeat 循环） | V20-002 |
| V20-004 | Class A Grok（可选） | V19 + V20-P0b + 产品确认要第二条 |

V20-P0 允许在 V18/V19 **编码期并行** 只追加 `docs/probe/`，不改 `orch/runtime/`。

---

## 停机（本文完成定义）

接手人能凭 architecture §7.5 + 终稿 + 本表写出 V20 的 `writing-plans`，且不会把 V18 与 Class B 写成同一刀。本文本身 **没有** 实现、没有新测试。
