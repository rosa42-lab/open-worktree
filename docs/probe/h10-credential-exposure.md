# H10 — Runtime credential exposure (阶段 3 / V12-004)

**日期：** 2026-07-26  
**范围：** Windows + POSIX，同账户下 `~/.orchestrator/runtime/opencode.credentials.json`

## 结论

| 项 | 结论 |
|---|---|
| argv | 密码不进 `opencode serve` 命令行；仅 `OPENCODE_SERVER_PASSWORD` 环境变量 |
| credentials 文件 | 独立 JSON；POSIX 尽量 `0600`；Windows 默认同账户可读 |
| JSON / audit / 日志 | registry 公共视图不含 password；lease token 只存 SHA-256 摘要 |
| 安全边界 | **不是**跨账户机密存储；高隔离需独立 OS 账户（超出 stdlib-only v1.2） |

## 缓解

- worker 通过 `ORCH_CREDENTIAL_FILE` 读路径，不复制密码到 argv
- `public_registry_view()` 剥离敏感字段
- `hmac.compare_digest` 校验 lease token
