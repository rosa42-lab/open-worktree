# H2 Desktop 人工验收操作指导（阶段 1 关闭门槛）

**目的：** 验证「OpenCode Desktop 只 Add Server 一次后，能定位同一 Server 上多个目录的 session」；并验证 attach / attach --fork 后备路径。  
**对应假设：** H2（致命）  
**前置自动化：** `orch runtime probe --json` 已 `phase0_pass=true`（§19.3 / H1 等）  
**约束：** 不要手工改 Desktop `localStorage` 或内部存储；不要拿 orch 项目锁。

> 注意：默认 `orch runtime probe` 会删临时 worktree 并停掉 Server。  
> 做 H2 时**不要**依赖那次输出里的 temp 路径。请按本文用**固定目录 + 固定端口**重做一轮。

---

## 0. 环境检查

在 PowerShell：

```powershell
cd E:\open-worktree
$env:PYTHONPATH = "E:\open-worktree"

opencode --version
# 期望: 1.18.5

python -m orch --version
# 期望: orch 1.1.0-candidate（或当前仓库版本）
```

确认已安装 **OpenCode Desktop**（与 CLI 同生态的桌面端）。

建议关闭其它占用端口的 `opencode serve`，避免连错实例：

```powershell
Get-NetTCPConnection -LocalPort 4096 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, State, OwningProcess
```

---

## 1. 准备两个持久 worktree（不要用探针临时目录）

```powershell
$ProbeRoot = "E:\orch-h2-probe"
Remove-Item -Recurse -Force $ProbeRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $ProbeRoot | Out-Null

$Seed = Join-Path $ProbeRoot "seed"
New-Item -ItemType Directory -Force -Path $Seed | Out-Null
Push-Location $Seed
git init -b develop
git config user.email "orch-h2@example.com"
git config user.name "orch-h2"
Set-Content -Encoding utf8 README.md "h2 probe seed`n"
git add README.md
git commit -m "seed"
Pop-Location

$Bare = Join-Path $ProbeRoot "bare.git"
git clone --bare $Seed $Bare

$WtA = Join-Path $ProbeRoot "worktree-a"
$WtB = Join-Path $ProbeRoot "worktree-b"
git --git-dir $Bare worktree add -b h2/agent-a $WtA develop
git --git-dir $Bare worktree add -b h2/agent-b $WtB develop

Write-Host "WT_A=$WtA"
Write-Host "WT_B=$WtB"
```

记下：

- `WT_A` = `E:\orch-h2-probe\worktree-a`
- `WT_B` = `E:\orch-h2-probe\worktree-b`

---

## 2. 启动一台共享 Server（固定端口）

另开一个 PowerShell 窗口，保持运行：

```powershell
$env:OPENCODE_SERVER_PASSWORD = "orch-h2-manual"
# 用户名默认 opencode；如需覆盖：
# $env:OPENCODE_SERVER_USERNAME = "opencode"

opencode serve --hostname 127.0.0.1 --port 4096 --pure
```

健康检查（第三个窗口或原窗口）：

```powershell
curl.exe -u opencode:orch-h2-manual http://127.0.0.1:4096/global/health
# 期望: {"healthy":true,"version":"1.18.5"}
```

**Server URL（后面全程用这个）：** `http://127.0.0.1:4096`

---

## 3. 在同一 Server 上为两个目录创建 session

```powershell
$Base = "http://127.0.0.1:4096"
$Auth = "opencode:orch-h2-manual"
$WtA = "E:\orch-h2-probe\worktree-a"
$WtB = "E:\orch-h2-probe\worktree-b"

# Session A
$RespA = curl.exe -s -u $Auth -X POST "$Base/session" `
  -H "Content-Type: application/json" `
  -H "x-opencode-directory: $WtA" `
  -d "{\"title\":\"h2-manual-a\"}"
$RespA
$IdA = ($RespA | ConvertFrom-Json).id

# Session B
$RespB = curl.exe -s -u $Auth -X POST "$Base/session" `
  -H "Content-Type: application/json" `
  -H "x-opencode-directory: $WtB" `
  -d "{\"title\":\"h2-manual-b\"}"
$RespB
$IdB = ($RespB | ConvertFrom-Json).id

Write-Host "SESSION_A=$IdA"
Write-Host "SESSION_B=$IdB"

# 可选：确认目录未串线
curl.exe -s -u $Auth -H "x-opencode-directory: $WtA" "$Base/path"
curl.exe -s -u $Auth -H "x-opencode-directory: $WtB" "$Base/path"
curl.exe -s -u $Auth -H "x-opencode-directory: $WtA" "$Base/vcs"
curl.exe -s -u $Auth -H "x-opencode-directory: $WtB" "$Base/vcs"
```

期望：

- `$IdA` ≠ `$IdB`
- A 的 path/directory 指向 `worktree-a`，branch `h2/agent-a`
- B 指向 `worktree-b`，branch `h2/agent-b`

把两个 session id 抄到便签（Desktop 里要找它们）。

---

## 4. Desktop：只 Add Server 一次（H2 主路径）

1. 打开 **OpenCode Desktop**。
2. 找到 **Add Server / Connect to Server / Remote Server**（文案因版本可能略有不同）。
3. **只添加一次**，填写：
   - URL：`http://127.0.0.1:4096`
   - Username：`opencode`
   - Password：`orch-h2-manual`
4. 保存后**不要**再为 `worktree-a` / `worktree-b` 各加一次 Server。
5. 在该 Server 的 session 列表 / 会话面板中查找：
   - title `h2-manual-a` 或 id `$IdA`
   - title `h2-manual-b` 或 id `$IdB`
6. 分别打开两个 session，确认：
   - 工作目录分别对应 A/B（或至少能通过 session 区分，不混成一个项目糊掉）
   - 打开 A 后看不到必须依赖第二次 Add Server 才能打开 B

### 4.1 通过标准

| 检查项 | 通过条件 |
|---|---|
| 单次注册 | Desktop 里对该主机只登记了 **一个** Server（4096） |
| 多 session 可见 | 同一 Server 下能看到（或能搜索到）A 与 B |
| 可打开 | 两个 session 都能打开，无需第二个 Server 条目 |
| 无内部黑改 | 全程未编辑 Desktop localStorage / 配置文件 |

### 4.2 若项目侧栏混淆 worktree

这是已知风险（源计划 §5.2 / H9），**不算 H2 失败**，只要：

- 仍能靠 **session id** 打开正确会话，或
- 走下面第 5 节 attach 后备命令成功

---

## 5. attach 后备路径（必须做）

即使 Desktop 侧栏正常，也请验证 CLI attach（接管/打开的稳定后备）：

```powershell
$Base = "http://127.0.0.1:4096"
$WtA = "E:\orch-h2-probe\worktree-a"
# 换成你第 3 步记下的真实 id
$IdA = "ses_xxxxxxxx"

# 5a. 附着原 session（会进入 TUI；验证能连上后 Ctrl+C / 退出即可）
opencode attach $Base --dir $WtA --session $IdA -u opencode -p orch-h2-manual

# 5b. fork 附着（应打开派生会话，不要求改原 session owner）
opencode attach $Base --dir $WtA --session $IdA --fork -u opencode -p orch-h2-manual
```

### 5.1 通过标准

| 检查项 | 通过条件 |
|---|---|
| attach | 能进入 session，无明显鉴权/目录错误 |
| attach --fork | 能打开 fork 会话；原 `$IdA` 仍可通过不带 `--fork` 再 attach |
| 密码 | 错误密码应失败（可选抽查：改 `-p wrong`） |

---

## 6. （可选）用 probe 对已运行 Server 再扫一遍

不替代 H2，只确认自动化仍绿：

```powershell
cd E:\open-worktree
$env:PYTHONPATH = "E:\open-worktree"
$env:OPENCODE_SERVER_PASSWORD = "orch-h2-manual"   # 若 probe 未传 --password，可依赖环境；本命令显式传更稳

python -m orch runtime probe --json `
  --base-url http://127.0.0.1:4096 `
  --password orch-h2-manual
```

期望：`data.phase0_pass == true`。  
该模式**不会**停你的 4096 Server。

---

## 7. 清理

验收通过后：

1. 退出 Desktop 中打开的 session（可选：从 Desktop 移除该 Server 条目）。
2. 在运行 `opencode serve` 的窗口按 `Ctrl+C` 停止 Server。
3. 删除探针目录（可选）：

```powershell
Remove-Item -Recurse -Force E:\orch-h2-probe
```

---

## 8. 签署（关闭阶段 1）

编辑 `docs/probe/phase0-capability-matrix.md` 底部：

```text
**H2 签署：**

- [x] Desktop 一次 Add Server 可观察多目录 sessions
- [x] attach 后备命令可用
- 签署人：<你的名字>  日期：<YYYY-MM-DD>
```

并建议在同目录追加一行笔记（可选）：

```text
H2 人工：Server http://127.0.0.1:4096；session A/B=<id>；Desktop 单次 Add Server 通过；attach/--fork 通过。
```

签署完成后，阶段 1 退出条件满足，可进入 **阶段 2**（Schema / Adapter / 只读观测）。

---

## 9. 失败时怎么判

| 现象 | 判定 | 动作 |
|---|---|---|
| Desktop 无法连 `127.0.0.1:4096` | 环境问题 | 查 serve 是否在跑、密码、防火墙 |
| 必须为每个 worktree Add Server 一次才能工作 | **H2 失败** | 停止进入阶段 2；更新 D1，评估回退 `per_agent` |
| 侧栏项目名混淆，但 session id / attach 可用 | H2 仍可通过；记下 H9 风险 | 继续，release 前再验 H9 |
| attach 鉴权失败但 curl health 成功 | 客户端参数问题 | 核对 `-u/-p` 与 `OPENCODE_SERVER_*` |
| fork 打不开 | H4 相关，记缺陷 | 不阻塞 H2 主结论，但 takeover 前必须修 |

---

## 速查命令卡片

```powershell
# Server
$env:OPENCODE_SERVER_PASSWORD="orch-h2-manual"
opencode serve --hostname 127.0.0.1 --port 4096 --pure

# Health
curl.exe -u opencode:orch-h2-manual http://127.0.0.1:4096/global/health

# Attach
opencode attach http://127.0.0.1:4096 --dir E:\orch-h2-probe\worktree-a --session ses_XXX -u opencode -p orch-h2-manual
opencode attach http://127.0.0.1:4096 --dir E:\orch-h2-probe\worktree-a --session ses_XXX --fork -u opencode -p orch-h2-manual
```
