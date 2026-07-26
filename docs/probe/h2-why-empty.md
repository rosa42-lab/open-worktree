# 你为什么看到「空会话」

截图里选中的是左侧本地项目 **`agentA-feat__bootstrap`**。  
那是本机某个 worktree 的本地视图，**本来就没有**我们为 H2 建的 `h2-manual-a/b` 会话，所以中间显示「这里还没有内容」是正常的。

另外：桌面快捷方式曾设 `OPENCODE_PORT=4096`，Desktop 自己占了 4096，把之前的外部 H2 `opencode serve`（以及上面的 session）顶掉了。  
**H2 要验的是：Desktop 作为客户端，只 Add Server 一次，去连外部 Server。**  
因此快捷方式已改回默认 `OpenCode.exe`（不再强制 4096）。若仍要 Desktop 自托管 4096，可用同目录下的 `OpenCode-port4096.cmd`（不推荐用于 H2）。

---

## 正确操作（请按顺序）

### 1. 完全退出 OpenCode Desktop
托盘图标也退出，确保 4096 不再被 `OpenCode.exe` 占用。

### 2. 告诉我「已退出」
我会重新启动：

- `opencode serve --hostname 127.0.0.1 --port 4096`
- 密码：`orch-h2-manual`
- 在 `E:\orch-h2-probe\worktree-a/b` 再建两个 session

### 3. 再打开 Desktop（用已恢复的默认快捷方式）
不要点左侧那些本地项目去找 H2 session。

在 Desktop 里找 **Add Server / 添加服务器 / Connect**（通常在设置或服务器列表），**只加一次**：

| 项 | 值 |
|---|---|
| URL | `http://127.0.0.1:4096` |
| Username | `opencode` |
| Password | `orch-h2-manual` |

然后在该 Server 下找 title：`h2-manual-a` / `h2-manual-b`。

### 4. 后备验证（不必靠侧栏）
```powershell
opencode attach http://127.0.0.1:4096 --dir E:\orch-h2-probe\worktree-a --session <SESSION_A> -u opencode -p orch-h2-manual
```

---

**一句话：** 空会话是因为看错了入口（本地项目 ≠ 共享 Server）。先退出 Desktop，我重建 4096 上的 H2 Server 后再做 Add Server。
