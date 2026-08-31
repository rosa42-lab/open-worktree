# Topic 连续开发 V20 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 V19 注入之上接 **Class B**（先 Claude `-p --resume`），用假二进制夹具证明 resume argv；禁止 PATH 名 `agent` 误调度。不接 Grok/Codex adapter，不写永续 CLI 壳。

**Architecture:** 配置为 **绝对路径 + kind**。Class B 不走 OpenCode SSE worker 循环：切片事件时 spawn、等待退出、记下 resume id。产品路径无 session 仍合法。未知 kind = fail-closed。

**Tech Stack:** Python 3 stdlib、unittest。权威：[topic-continuous-dev-final.md](topic-continuous-dev-final.md)、[probe/claude-protocol.md](probe/claude-protocol.md)、修正案 C3。

**Files:**
- Create: `docs/probe/claude-protocol.md`, `orch/runtime/binary.py`, `orch/runtime/claude.py`, `tests/test_runtime_v20.py`
- Modify: `orch/runtime/factory.py`, `orch/runtime/lifecycle.py`（按 Class 分支；B 不 Popen `orch.runtime.worker` SSE 环）

**禁止：** `heartbeat`/`/goal` 投 prompt；spawn PATH `agent`；`--bg`；第二 Class A；跳过本计划直接改 Topic 队列。

---

### Task 1: 二进制身份（TDD）

- Test: `tests/test_runtime_v20.py`
- Create: `orch/runtime/binary.py`

- [x] `resolve_executor(kind, path)`：kind 不在 `{opencode,claude,grok,codex}` → `runtime_unknown_kind`
- [x] path 必须是存在的文件；禁止只给命令名 `agent` / `claude`
- [x] 文件名 `agent`/`agent.exe` 且 kind ≠ `grok` → `runtime_binary_identity`
- [x] env allowlist：spawn 环境只有允许的键 + 显式 PATH 到该二进制目录

### Task 2: Class B 假二进制夹具

- Create: `orch/runtime/claude.py`

- [x] 第一次 spawn argv 含 `-p` 与 `--session-id`；写出 resume id
- [x] 第二次 spawn argv 含 `--resume <id>`，不含新的随机 session
- [x] 非 0 退出 fail-closed；进程已退且无 session 证据时不得标 `running`
- [x] 不调用 `OpenCodeHttpClient`

### Task 3: lifecycle 按 Class 分支

- [x] kind=claude：不启动 `python -m orch.runtime.worker` 心跳环
- [x] kind=opencode：现有 Class A 路径不变（回归 `test_runtime_v19`）
- [x] Topic `--start-session` 仍是唯一 session 开关

### Task 4: 文档与全量测试

- [x] architecture §7.5 Class 表：Claude Class B 标注「夹具已接 / 真二进制未付费验证」
- [x] `python -m unittest discover -s tests -v`
