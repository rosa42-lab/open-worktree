# H2 Desktop — 已通过

## 检查结论（2026-07-26）

| 项 | 结果 |
|---|---|
| Server `http://127.0.0.1:14196` | healthy `1.18.5` |
| Desktop 只 Add Server 一次 | 通过（选中 `127.0.0.1:14196`） |
| `E:\orch-h2-probe\worktree-a` 可见 `h2-manual-a` | 通过（用户截图） |
| 同 Server 打开 `h2-manual-b` | 通过（用户截图） |
| API session A/B | HTTP 200 |
| 父目录 `E:\orch-h2-probe` | 会 `UnsupportedContentType`，忽略即可 |

签署已写入 `docs/probe/phase0-capability-matrix.md`。阶段 1 退出条件满足，可进入阶段 2。
