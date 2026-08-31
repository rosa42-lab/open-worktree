# 专题连续开发 — 风险分析与方案调整（Pass 2）

**日期：** 2026-08-30  
**输入：** [plan](topic-continuous-dev-plan.md)、[feasibility](topic-continuous-dev-feasibility.md)  
**输出：** 调整后的架构；多维度审查在本文 R1–R3，终稿在 [final](topic-continuous-dev-final.md)

---

## Assumption verification（相对 Pass 1）

| Assumption | Verified? | Reality |
|------------|-----------|---------|
| A1 headless 退出 | ✅ | 三家脚本入口都是 one-shot |
| A2 resume 存在 | ✅ | 旗标齐全；未付费往返 |
| A3 live inject 普遍 | ❌ 普遍 / ⚠️ 部分 | 仅 OpenCode 已接；Grok leader、Codex app-server 形态在；Claude 无 |
| A4 heartbeat=叫醒 | ❌ | 只写 DB |
| A5 goal 机制同一 | ❌ | 三套东西 |
| A6 spawn 继承会话 | ❌ | 必须显式 session id |
| A7 `agent` 通用 | ❌ | 是 grok |
| A8 无 Server 产品路径 | ✅ | V17 |
| A9 配置可插拔 | ❌ | 硬编码 OpenCode |
| A10 协议同构 | ❌ | HTTP vs `-p` vs leader socket vs app-server |
| A11 TUI 可编排 | ❌ | 不能当协议 |
| A12 goal 跨窗 | ❌ | omo 明文不行 |
| A13 常驻成本 | ⚠️ | 改为按需后非阻塞 |

---

## Gaps found

| Gap | Impact | Priority |
|-----|--------|----------|
| Option A 把三种心跳当成一种 | 整条「永续 CLI + goal」不可用 | P0 |
| 「子任务」粒度与 Topic 切片冲突 | N 个永续进程 vs 一个 Topic 身份 | P0 |
| 父 CLI 开关子 CLI 绕过 lease/generation | 双写、误杀、无法 takeover | P0 |
| worker 硬编码 OpenCode | 「换 Claude」不是配配置 | P0（实现波次） |
| CapabilityMatrix 对未知能力默认 True | 与 V17 修正案冲突；换执行器会假绿 | P0（已在 V19 范围） |
| Claude 无 Class A | 若把 live 员工当硬需求，Claude 不能当默认执行器 | P1 |
| 旧 plan.md「产品路径必须供给 session」与 V17 冲突 | 文档误导实现 | P1（以 V17 + architecture 为准） |
| 未做真实 `--resume` e2e | 磁盘会话格式/env 污染仍可能炸 | P1（实现前 probe） |

---

## Design changes

### Change 1: 丢掉 Option A

- **Before：** 每子任务永续 CLI + goal 心跳 + 子 CLI。
- **After：** Topic 持有执行会话 id；进程按需；goal 只在 conductor。
- **Why：** A4/A5/A6/A11/A12 全部失败。

### Change 2: 连续性三层拆开

| 层 | 谁拥有 | 生死 |
|----|--------|------|
| 工作产物 | Topic + worktree + branch + Git | 专题期间永续 |
| 对话 | executor `session_id` / resume id | 跨进程永续（磁盘或 server） |
| 存活证据 | orch worker PID+nonce+generation+heartbeat | 进程级，可死可拉 |

### Change 3: 执行器分档，未知 = fail-closed

| Class | 能力 | 本机证据 | orch 用法 |
|-------|------|----------|-----------|
| A attach | 长驻后端 + 中途投 prompt | OpenCode serve；Grok leader/serve；Codex app-server | 现 worker 模型；换协议只换 adapter |
| B resume | headless 退出 + `--resume` | claude `-p -r`；grok `-p -r`；opencode `run -s`；codex `exec resume` | 切片事件时 spawn |
| C human TUI | 交互 | 三家默认 | 只 attach 给人类，orch 不驱动 |
| 0 none | 无脚本面 | — | 禁止 `--start-session` |

### Change 4: 「子任务」= Topic 切片，不是新员工

一个 Topic / 一个 worktree / 至多一个活执行会话。切片走已有闭环：commit → `topic-ready` → `topic-enqueue` → `merge --once`。冲突在源 WT 修，不新开永续 CLI。

### Change 5: 子进程只许是工具

pytest / git / linter 可以。嵌套 `claude`/`grok`/`opencode run` 当员工不行。执行器若自带 subagent（grok `--no-subagents`、claude agents），默认关掉或 fail-closed，避免第二套编排。

### Change 6: 不自研 supervisor 壳

OpenCode 已有 Python worker；Grok 已有 `agent leader --no-exit-on-disconnect`。orch 只做 **lease + 身份 + 按 Class 调 adapter**，不复制一套进程树编排器。

### Change 7: 实现顺序冻结

V18 schema 5 / 图 / Saga → V19 runtime 身份与 capability → **然后** 才允许第二个 RuntimeAdapter。本方案现在只冻结架构。

---

## Updated architecture

```text
Conductor（唯一 /goal 面）
  omo Desktop / 人类 / Cursor 会话
  只调用 orch CLI：topic-* / agent-* / merge / doctor
           │
           ▼
orch 核心（与执行器无关）
  Topic 生命周期 · worktree · queue · lease · generation
           │
           ▼
RuntimeAdapter（按探测到的 Class）
  Class A: attach + send_prompt + abort + heartbeat worker
  Class B: spawn headless --resume <id> + wait + 记 attestation
  Class C: 返回 attach 命令给人类
           │
           ▼
执行器后端（OpenCode serve / Grok leader / Codex app-server / Claude -p）
```

调用链（新人改「换 Grok」应能停在 5 个符号内）：

1. `cli.py` / topic-start `--start-session`
2. `AgentLifecycleService`
3. `RuntimeAdapter`（协议）
4. `GrokRuntimeAdapter`（新）
5. grok leader/stdio 或 `-p --resume`

禁止再从 worker 直接 `import OpenCodeRuntimeAdapter`。

---

## Risk heatmap

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| `--resume` 实际不续工具状态 | 中 | 高 | 实现前按 cli-protocol-discovery 付费最小往返 |
| Grok leader socket 权限/残留 | 中 | 高 | 每 Topic 独立 socket 路径；stop 时卸；未知能力 false |
| Codex app-server experimental | 高 | 中 | Class 探测失败则降 B 或拒绝 start-session |
| 双锁（project.lock + runtime.lock） | 已知 | 高 | 已在 V19：禁止同时持有 |
| Conductor `/goal` 关窗丢失 | 高 | 中 | checkpoint 落盘；重启后人类重挂 `/goal`，不自动伪造 |
| 执行器 subagent 乱改其它 WT | 中 | 高 | 默认 `--no-subagents`；worktree 边界已有 cleanup guard |
| 过早实现本方案跳过 V18 | 中 | 高 | 终稿写死依赖；本波不写代码 |

---

## 多维度审视（R1 / R2 / R3）

审查轮次：R1 架构合理性 → R2 实现可落地性 → R3 防御性。R1 不过不进 R2。

### R1 架构合理性 — **通过**

**选型：** 不引入新中间件。连续性用执行器已有的 session/resume；存活用已有 worker heartbeat；催促用已有 conductor `/goal`。拒绝自研永续 CLI 壳（过度设计）。拒绝「所有执行器当 OpenCode HTTP」（信仰驱动，A10 已伪）。

**分层：** 核心 Topic/queue 不依赖 OpenCode HTTP（V17 产品路径已证明）。执行细节停在 adapter。换 Grok 不应改 `topic-enqueue`。

**职责：**

| 模块 | 唯一变化原因 |
|------|----------------|
| Topic | 专题供给与闭环 |
| Queue/merge | 合入策略 |
| Lease/generation | 单写者 |
| Adapter | 某执行器协议 |
| Conductor `/goal` | 人类侧空闲续跑，**不是** orch 子系统 |

**过度 vs 不足：**

| 判断 | 状态 |
|------|------|
| 为遥不可及需求做抽象？ | 否。Class 分档对应本机已装的四套 CLI，不是假想平台 |
| 自研 supervisor？ | 明确不做 |
| 硬编码 OpenCode？ | 现状不足；终稿要求注入 adapter（V19 后） |
| 降级？ | Class A 不可用 → B；B 不可用 → 禁止 start-session，产品路径仍可无 session 闭环 |

**对抗问答（节选）：**

- 若 A3 对 Claude 为假？退路：只许 Class B。
- 换掉 OpenCode？应只改 adapter + worker 注入；今天还不行，故实现排期在 V19 解耦之后。
- 外部执行器挂了？run → lost/reconciling；不自动重放 prompt（已有）。
- 明天不做多执行器？成本几乎为零——终稿是文档约束，不改代码。

### R2 实现可落地性 — **通过（作为下一波契约，非本周编码）**

最小契约（adapter 必须回答，未知=false）：

| operation | Class A | Class B |
|-----------|---------|---------|
| create_or_resume_session | 是 | resume id 或新 id |
| send_prompt | 异步投递 | spawn 一次进程 |
| abort | 是 | kill 子进程 + 记录 |
| health | serve/leader 探活 | 二进制 `--version` |
| attach_for_human | CLI 字符串 | 无或 `--resume` TUI |
| env_allowlist | 必填 | 必填 |

消费路径：唯一消费者是 `AgentLifecycleService` + worker。没有第三条「父 CLI 开关子 CLI」入口。

降级：probe 失败不得标 running。产品路径 `provision_session=false` 保持。

缺口（不阻塞终稿，阻塞编码）：各 Class A 的字节级协议。编码前必须再跑 cli-protocol-discovery（允许最小付费往返）。

### R3 防御性 — **通过（约束级）**

| 题 | 终稿约束 |
|----|----------|
| 这步重启？ | Git + Topic 行 + session id 在 DB；worker 可丢；不重放 prompt |
| 恶意 prompt 注入？ | 不把不可信 brief 当 shell；`--brief-file` 已限制在 project root |
| 上下文膨胀？ | Class B 本身按切片截断；Class A 由执行器自己的 session；orch 不存对话全文 |
| JSON 格式错误？ | 未知能力 false；解析失败 fail-closed |
| 同时兼容旧字段？ | `session_id` 列保持；新列仅加性（执行器 kind / resume id），随 V19 复合身份 |
| PID reuse？ | 已有 nonce+generation+heartbeat；禁止只认 PID |
| 双编排？ | 禁止执行器默认 subagent；禁止 conductor 以外的 `/goal` |

**R1 + R2 + R3：通过。** 条件：不把本方案插入 V18 实现；编码前补 Class A/B 最小往返 probe。

---

## 与 Pass 1 的差异（必须能列出）

1. 连续挂 session id，不挂永续 CLI PID。
2. goal 只催 conductor，不催 worker。
3. heartbeat 只证活，不思考。
4. 子任务不是员工；Topic 才是。
5. 执行器分档，不假设 HTTP 同构。
6. 不自研进程树编排器。
7. `agent` 按路径配置，不按名字。
8. 实现排在 V18 → V19 之后。
