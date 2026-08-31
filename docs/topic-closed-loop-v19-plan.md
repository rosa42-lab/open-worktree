# Topic 闭环 V19 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注入 `RuntimeAdapter`，未知能力 = false，按 operation 门禁，写入复合绑定，禁止双锁，runtime stop 先 draining 再按项目 fencing。不实现第二执行器。

**Architecture:** lifecycle / worker / takeover / probe / agent_readonly 只通过 `RuntimeAdapter` 发协议。默认 `capabilities()` 除已观察字段外全 false。`assert_runtime_gate(operation)` 未知 operation 失败。Git / Topic 产品路径不变。V20 不在本计划。

**Tech Stack:** Python 3 stdlib、SQLite、unittest。权威：[topic-closed-loop-v17-amendment.md](topic-closed-loop-v17-amendment.md) C3。

**Files:**
- Create: `orch/runtime/factory.py`, `orch/runtime/gate.py`, `tests/test_runtime_v19.py`
- Modify: `orch/runtime/opencode.py`, `orch/runtime/adapter.py`, `orch/runtime/lifecycle.py`, `orch/runtime/worker.py`, `orch/runtime/takeover.py`, `orch/runtime/probe.py`, `orch/runtime/service.py`, `orch/commands/runtime.py`, `orch/commands/agent_readonly.py`, `orch/locks.py`, `orch/cli.py`, `tests/test_runtime_adapter.py`, `tests/test_runtime_service.py`, `docs/current-architecture.md`

**禁止：** Claude/Grok/Codex adapter；改 Topic 生命周期；永续 CLI 壳；heartbeat `send_prompt`。

---

### Task 1: 未知能力 = false

- Test: `tests/test_runtime_adapter.py` + `tests/test_runtime_v19.py`

- [x] 无 `known_capabilities` 时：`global_health` 来自 health()；`basic_auth` 来自是否有 password；**其余字段 false**
- [x] `CapabilityMatrix.unknown()`（或等价）全 false；`required_pass` 不再作为 start 门禁
- [x] 未知 operation → `runtime_capability_unknown`（fail-closed）

### Task 2: `assert_runtime_gate` + 分 operation 门禁

- Create: `orch/runtime/gate.py`

- [x] `agent-list` / `show` / `topic-list` / `show`：测例证明 **零 HTTP**（不构造 adapter / 不 health）
- [x] `agent-stop` 本地 worker：gate **不**要求 `abort`
- [x] `agent-start` 无 prompt：不要求 `prompt_async`；有 prompt 才要求
- [x] `create_session` 要求 `create_session`；缺则 `runtime_capability_missing`
- [x] fork POST 要求 `session_fork_api`；`--launch` fork 要求 `attach_cli_fork`

### Task 3: 注入 adapter（删除硬编码路径）

- Create: `orch/runtime/factory.py`
- Modify: lifecycle / worker / takeover / probe / agent_readonly

- [x] `AgentLifecycleService(project, adapter=...)` 走注入的 adapter，**不** `OpenCodeHttpClient` / **不** import 死绑到 start 路径
- [x] 假 adapter 单测：start（mock Popen + 写 heartbeat）→ get_session → stop；requests 列表为空（无 HTTP）
- [x] worker 经 factory 取 adapter；默认仍是 OpenCode
- [x] grep：`lifecycle.py` / `worker.py` / `takeover.py` 不再出现 `from orch.runtime.opencode import OpenCodeRuntimeAdapter`

### Task 4: 复合绑定写入

- [x] `agent-start` 写入 `capability_digest` + `runtime_version`（digest = canonical JSON of matrix）
- [ ] 绑定身份 `{server_id, generation, nonce, capability_digest, version}`；digest 变了不得静默当同一次运行时（列已写；heartbeat 比对留 V20 若需要）

### Task 5: 禁双锁 + runtime stop draining

- [x] 同线程已持 `project.lock` 再 `acquire(runtime.lock)` → `dual_lock_forbidden`（反之亦然）
- [x] `runtime stop`：持 runtime.lock 写 `draining` → **释放** → 按项目名排序拿 `project.lock` fencing → 再拿 runtime.lock kill
- [x] 有活 run 且非 `--force` 仍 `runtime_stop_blocked`（现有测例保持）
- [x] `--probe-full` 才跑 mutating 检查；默认 probe 只 health/read-only；external 默认不得 full，除非显式授权

### Task 6: 文档与全量测试

- [x] architecture §12.3 / §13：V19 已接注入与未知=false；V20 仍未开工
- [x] `python -m unittest discover -s tests -v`
