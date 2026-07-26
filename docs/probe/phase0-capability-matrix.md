# orch v1.2 Phase 0 — Capability Probe 证据

**任务：** V12-001  
**日期：** 2026-07-26  
**OpenCode：** CLI/Server `1.18.5`  
**命令：** `orch runtime probe --json`  
**原始结果：** `docs/probe/phase0-result.json`

## 结论

| 项 | 结果 |
|---|---|
| `phase0_pass` | **true** |
| 架构决定 (D1) | **shared** Server + per-directory InstanceContext |
| 最低 supported 版本 | **1.18.5**（与候选基线一致） |
| §19.3 多目录隔离 | **通过** |
| 是否回退 per_agent | **否** |

## Capability 矩阵

| Capability | 结果 |
|---|---|
| `global_health` | pass |
| `directory_header` (`x-opencode-directory`) | pass |
| `directory_query` (`?directory=`) | pass |
| `create_session` | pass |
| `get_session` / list | pass（且列表不串目录） |
| `session_status` | pass |
| `event_sse` | pass（stdlib raw-socket chunked SSE） |
| `abort` | pass |
| `instance_dispose` | pass（dispose B 后 Server 与 A 仍健康） |
| `prompt_async` | pass（endpoint 可达） |
| `session_fork_api` | pass |
| `attach_cli_dir/session/fork` | pass |
| `basic_auth` | pass（错误密码 HTTP 401） |
| `path_api` / `vcs_api` | pass |
| `shell_api` | fail（shell 未落盘；隔离改由 worktree 写入 + `/file/content` 目录作用域证明） |

## §19.3 Shared Server 多目录隔离

在**一个** Server 上创建两个真实 Git worktree（独立 branch）：

- A/B 的 `/path.directory` 与 `/vcs.branch` 不同且正确。
- A/B 各自 `POST /session`，session id 不同；`GET /session` 列表不交叉。
- 各自写入 marker 文件；磁盘与 `/file/content` 均不串读。
- `/event?directory=` 各自收到 `server.connected`；B 流未泄露 A 新建 session id。
- `POST /instance/dispose` 释放 B 后，共享 Server 仍 healthy，A 仍可路由。

## H1–H7

| ID | 风险 | 状态 | 说明 |
|---|---|---|---|
| H1 | 致命 | **pass** | directory routing + 文件/session 隔离成立 |
| H2 | 致命 | **deferred** | 自动化前置已就绪（见 `h2-live-session.json`）；Desktop UI 待用户签署 |
| H3 | 高 | partial | abort 可用；busy→idle 时序留待 takeover 门槛 |
| H4 | 高 | partial | attach CLI 标志 + `/session/:id/fork` 可用；交互 attach E2E 留待 takeover |
| H5 | 高 | **pass** | SSE 断开后 health/status 可补偿 |
| H6 | 高 | partial | session id 稳定；prompt 幂等留待 worker 门槛 |
| H7 | 高 | **pass** | stdlib HTTP Basic Auth + raw-socket SSE 对真实 Server 可用 |

## Desktop 人工验收（H2）— 已签署

**完整逐步操作：** 见 [`docs/probe/h2-desktop-acceptance.md`](./h2-desktop-acceptance.md)。  
**现场速查（真实 id）：** 见 [`docs/probe/h2-desktop-now.md`](./h2-desktop-now.md) / [`docs/probe/h2-live-session.json`](./h2-live-session.json)。

自动化探针**不会**改 Desktop 内部存储；默认探针还会删除临时 worktree。做 H2 时请按该文档使用固定目录 `E:\orch-h2-probe\worktree-a|b`，不要打开父目录 `E:\orch-h2-probe`（会 `UnsupportedContentType`），也不要复用普通 `runtime probe` 的 temp 路径。

**自动化 + Desktop 实测（2026-07-26）：**  
- Server：`http://127.0.0.1:14196`（因 Desktop Local Server 占默认 4096 / 添加框焦点问题改端口；无密码；健康 `1.18.5`）  
- Desktop 已 Add Server 一次并选中 `127.0.0.1:14196`  
- 用户在 `E:\orch-h2-probe\worktree-a` 侧栏可见多个 `h2-manual-a`，并打开 `h2-manual-b`（同 Server，未二次 Add Server）  
- session A：`ses_0618ebb9fffeORHaiDZdSdql6V`；session B：`ses_0618ebb62ffe9Hi9Y2JeswaW65`  
- API get A/B = 200；attach 短超时启动无鉴权失败；`runtime probe` `phase0_pass=true`

**H2 签署：**

- [x] Desktop 一次 Add Server 可观察多目录 sessions  
- [x] attach 后备命令可用  
- 签署人：用户（Desktop 截图确认）+ agent 复核  日期：2026-07-26

H2 人工：Server http://127.0.0.1:14196；session A=`ses_0618ebb9fffeORHaiDZdSdql6V`；session B=`ses_0618ebb62ffe9Hi9Y2JeswaW65`；Desktop 单次 Add Server 通过；attach 通过。

## 约束遵守

- Python stdlib only  
- 未写 project DB、未拿项目锁、未改 orch 管理的 Git  
- 未自动修改 Desktop 内部存储  

## 阶段 1 退出条件对照

| 条件 | 状态 |
|---|---|
| §19.3 通过 | 满足 |
| H1 有实测结论 | pass |
| H2 有实测结论 | **pass（Desktop + attach）** |
| H3–H7 前置证据 | 已记录（partial 项有后续门槛） |
| shared 致命假设失败则回退 | 未触发；保持 D1 |
| v1.1 回归 + stdlib-only | 见测试记录 |

## 实现入口

- CLI：`orch runtime probe [--json] [--base-url] [--port] [--password] [--keep-server]`
- 模块：`orch/runtime/{probe,adapter,http_client,process}.py`
- 命令：`orch/commands/runtime.py`
