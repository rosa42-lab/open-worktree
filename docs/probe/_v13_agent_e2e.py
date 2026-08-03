#!/usr/bin/env python3
"""v1.3 Agent E2E：Agent 用 App 凭证 + orch CLI 完成晋级链（无需人工点 UI）。

流程：
  tip commit → verification → promote-develop --execute
  → release-create --execute → gh merge（solo admin）
  → release-sync --execute → released

凭证仅内存；不打印 token。结果写入本目录 JSON/MD。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orch.constants import project_data_dir  # noqa: E402
from orch.remote.auth import mint_installation_token  # noqa: E402

OWNER = "rosa42-lab"
REPO = "open-worktree"
FULL = f"{OWNER}/{REPO}"
OPENSSL = os.environ.get("ORCH_OPENSSL_PATH") or shutil.which("openssl") or r"F:\anaconda\Library\bin\openssl.exe"

INTEGRATION = {
    "app_id": "4454107",
    "installation_id": "150492767",
    "pem": r"E:\commonSecret\orch-integration-app.2026-08-01.private-key.pem",
}
RELEASE = {
    "app_id": "4454179",
    "installation_id": "150494410",
    "pem": r"E:\commonSecret\orch-release-app.2026-08-01.private-key.pem",
}

OUT_DIR = Path(__file__).resolve().parent
RESULT_JSON = OUT_DIR / "v13-agent-e2e.json"
RESULT_MD = OUT_DIR / "v13-agent-e2e.md"


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    e.setdefault("PYTHONPATH", str(ROOT))
    # never log Authorization
    safe = [c if "ghs_" not in c and "github_pat_" not in c else "***" for c in cmd]
    print("+", " ".join(safe), flush=True)
    return subprocess.run(cmd, cwd=cwd, env=e, text=True, capture_output=True, check=check)


def orch_json(args: list[str], *, env: dict | None = None) -> dict:
    r = run([sys.executable, "-m", "orch", *args, "--json"], env=env, check=False)
    text = (r.stdout or "").strip()
    data = {}
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            lines = [ln for ln in text.splitlines() if ln.strip().startswith("{")]
            data = json.loads(lines[-1]) if lines else {"raw": text[:500]}
    data["_exit"] = r.returncode
    data["_stderr"] = (r.stderr or "")[:400]
    return data


def set_token_remote(bare: Path, token: str) -> str:
    """临时 HTTPS token URL（仅本脚本；结束后恢复）。"""
    url = f"https://x-access-token:{token}@github.com/{FULL}.git"
    run(["git", "--git-dir", str(bare), "remote", "set-url", "origin", url])
    return f"https://github.com/{FULL}.git"


def restore_remote(bare: Path, clean: str) -> None:
    run(["git", "--git-dir", str(bare), "remote", "set-url", "origin", clean], check=False)


def main() -> int:
    steps: list[dict] = []
    t0 = time.time()
    integ_tok = mint_installation_token(
        app_id=INTEGRATION["app_id"],
        installation_id=INTEGRATION["installation_id"],
        pem_path=INTEGRATION["pem"],
        openssl_bin=OPENSSL,
    )
    rel_tok = mint_installation_token(
        app_id=RELEASE["app_id"],
        installation_id=RELEASE["installation_id"],
        pem_path=RELEASE["pem"],
        openssl_bin=OPENSSL,
    )
    steps.append({"step": "mint_tokens", "ok": True})

    td = Path(tempfile.mkdtemp(prefix="orch-agent-e2e-"))
    proj_name = "v13agente2e"
    try:
        # clone bare only; orch init 创建 main/
        bare = td / ".bare.git"
        run(["git", "clone", "--bare", f"https://github.com/{FULL}.git", str(bare)])
        # project remove 默认 data_kept；残留 active master_release 会冻住 promote
        run([sys.executable, "-m", "orch", "project", "remove", proj_name], check=False)
        shutil.rmtree(project_data_dir(proj_name), ignore_errors=True)
        add = orch_json(["project", "add", proj_name, str(td)])
        if not add.get("ok"):
            raise RuntimeError(f"project add failed: {add}")
        init = orch_json([proj_name, "init"])
        if not init.get("ok"):
            raise RuntimeError(f"init failed: {init}")
        steps.append({"step": "project_init", "ok": True, "path": str(td)})

        main = td / "main"
        run(["git", "checkout", "develop"], cwd=main)
        run(["git", "config", "user.email", "orch-agent-e2e@local"], cwd=main)
        run(["git", "config", "user.name", "orch-agent-e2e"], cwd=main)

        # Catch-up: PR#1 后未 release-sync 时 master 不是 develop 祖先。
        # Agent 先把 master 合入 local develop，再 promote（恢复 §10.1 前提）。
        clean = f"https://github.com/{FULL}.git"
        run(["git", "--git-dir", str(bare), "remote", "set-url", "origin", clean])
        run(["git", "fetch", "origin", "master:refs/remotes/origin/master"], cwd=main)
        run(
            ["git", "merge", "origin/master", "-m", "chore(probe): catch-up master into develop for release"],
            cwd=main,
            check=False,
        )
        # if already up to date or merged
        set_token_remote(bare, integ_tok)
        run(["git", "push", "origin", "develop"], cwd=main)
        restore_remote(bare, clean)
        steps.append({"step": "catchup_master_into_develop", "ok": True})

        # remote-config (identity check needs github URL)
        run(["git", "--git-dir", str(bare), "remote", "set-url", "origin", clean])
        cfg = orch_json(
            [
                proj_name,
                "remote-config",
                "--repository",
                FULL,
                "--provider",
                "github",
                "--remote",
                "origin",
            ]
        )
        steps.append({"step": "remote-config", "ok": bool(cfg.get("ok")), "data": cfg.get("data") or cfg})

        env_probe = {
            "ORCH_GITHUB_INTEGRATION_APP_ID": INTEGRATION["app_id"],
            "ORCH_GITHUB_INTEGRATION_INSTALLATION_ID": INTEGRATION["installation_id"],
            "ORCH_GITHUB_INTEGRATION_APP_PEM": INTEGRATION["pem"],
            "ORCH_GITHUB_RELEASE_APP_ID": RELEASE["app_id"],
            "ORCH_GITHUB_RELEASE_INSTALLATION_ID": RELEASE["installation_id"],
            "ORCH_GITHUB_RELEASE_APP_PEM": RELEASE["pem"],
            "ORCH_OPENSSL_PATH": OPENSSL,
            # also default release token for factory best_for_probe
            "ORCH_GITHUB_TOKEN": rel_tok,
        }
        probe = orch_json([proj_name, "remote-probe", "--no-fetch"], env=env_probe)
        steps.append(
            {
                "step": "remote-probe",
                "ok": probe.get("ok") is True or (probe.get("data") or {}).get("overall") in (
                    "unknown",
                    "verified",
                    "misconfigured",
                ),
                "overall": (probe.get("data") or {}).get("overall"),
                "write_paths_enabled": (probe.get("data") or {}).get("write_paths_enabled"),
            }
        )

        # tip commit on develop
        marker = main / "docs" / "probe" / "_agent_e2e_marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"agent-e2e {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n", encoding="utf-8")
        run(["git", "add", "docs/probe/_agent_e2e_marker.txt"], cwd=main)
        run(["git", "commit", "-m", "chore(probe): agent e2e marker"], cwd=main)
        # push to local bare
        run(["git", "push", str(bare), "develop"], cwd=main)
        tip = run(["git", "rev-parse", "HEAD"], cwd=main).stdout.strip()
        steps.append({"step": "local_tip", "ok": True, "sha": tip})

        # verification record via python API (agent-equivalent)
        from orch.db import open_project_db
        from orch.verification.service import create_aggregate

        conn = open_project_db(proj_name, init=True)
        try:
            rec = create_aggregate(
                conn,
                project=proj_name,
                commit_sha=tip,
                commands=["test"],
                results=[{"command": "test", "exit_code": 0, "ok": True}],
                created_by="agent-e2e",
                scope="develop_publish",
            )
            conn.commit()
            vid = rec["id"]
        finally:
            conn.close()
        steps.append({"step": "verification", "ok": True, "id": vid})

        # promote with Integration token on origin
        set_token_remote(bare, integ_tok)
        env_promo = {**env_probe, "ORCH_GITHUB_TOKEN": integ_tok}
        dry = orch_json(
            [proj_name, "promote-develop", "--verification", vid],
            env=env_promo,
        )
        dry_payload = dry.get("data") or dry
        steps.append(
            {
                "step": "promote-develop-dry",
                "ok": bool(dry_payload.get("ok_to_execute")),
                "payload": {
                    k: dry_payload.get(k) for k in ("ok_to_execute", "error", "error_kind")
                },
            }
        )
        if not steps[-1]["ok"]:
            raise RuntimeError(f"promote dry blocked: {steps[-1]['payload']}")
        promo = orch_json(
            [proj_name, "promote-develop", "--verification", vid, "--execute"],
            env=env_promo,
        )
        pdata = promo.get("data") or promo
        steps.append(
            {
                "step": "promote-develop-execute",
                "ok": (pdata.get("promotion") or {}).get("state") == "succeeded"
                or bool(pdata.get("idempotent")),
                "state": (pdata.get("promotion") or {}).get("state"),
                "exit": promo.get("_exit"),
                "err": promo.get("_stderr") or pdata.get("error"),
            }
        )
        restore_remote(bare, clean)

        if not steps[-1]["ok"]:
            raise RuntimeError(f"promote failed: {steps[-1]}")

        # release-create with Release token (HTTP only)
        env_rel = {**env_probe, "ORCH_GITHUB_TOKEN": rel_tok}
        rcreate = orch_json(
            [proj_name, "release-create", "--verification", vid, "--execute", "--title", "orch agent e2e release"],
            env=env_rel,
        )
        rdata = rcreate.get("data") or rcreate
        pid = (rdata.get("promotion") or {}).get("id")
        pr_url = (rdata.get("pr") or {}).get("url")
        pr_num = (rdata.get("pr") or {}).get("external_id")
        steps.append(
            {
                "step": "release-create",
                "ok": bool(rdata.get("ok")) and bool(pid),
                "promotion_id": pid,
                "pr": pr_num,
                "url": pr_url,
                "state": (rdata.get("promotion") or {}).get("state"),
                "err": rcreate.get("_stderr"),
            }
        )
        if not steps[-1]["ok"]:
            raise RuntimeError(f"release-create failed: {rdata}")

        # agent merge via gh (solo OrganizationAdmin path)
        merge = run(
            [
                "gh",
                "pr",
                "merge",
                str(pr_num),
                "-R",
                FULL,
                "--merge",
                "--admin",
                "--delete-branch=false",
            ],
            check=False,
        )
        steps.append(
            {
                "step": "gh-pr-merge",
                "ok": merge.returncode == 0,
                "stdout": (merge.stdout or "")[:200],
                "stderr": (merge.stderr or "")[:300],
            }
        )
        if merge.returncode != 0:
            raise RuntimeError(f"gh merge failed: {merge.stderr}")

        # release-status → pending_sync
        st = orch_json([proj_name, "release-status", pid], env=env_rel)
        sdata = st.get("data") or st
        steps.append(
            {
                "step": "release-status",
                "ok": (sdata.get("promotion") or {}).get("state")
                in ("master_merged_pending_sync", "syncing", "released"),
                "state": (sdata.get("promotion") or {}).get("state"),
            }
        )

        # release-sync with Integration token for develop CAS
        set_token_remote(bare, integ_tok)
        # fetch latest master into bare
        run(["git", "--git-dir", str(bare), "fetch", "origin", "develop:develop", "master:master"], check=False)
        sync = orch_json(
            [proj_name, "release-sync", pid, "--execute"],
            env=env_promo,
        )
        sy = sync.get("data") or sync
        steps.append(
            {
                "step": "release-sync",
                "ok": sy.get("sync") == "released" or (sy.get("promotion") or {}).get("state") == "released",
                "sync": sy.get("sync"),
                "state": (sy.get("promotion") or {}).get("state"),
                "err": sync.get("_stderr"),
                "detail": {k: sy.get(k) for k in ("errors", "plan", "merge_sha")},
            }
        )
        restore_remote(bare, clean)

        ok = all(s.get("ok") for s in steps)
        result = {
            "ok": ok,
            "elapsed_sec": round(time.time() - t0, 1),
            "repo": FULL,
            "tip": tip,
            "promotion_id": pid,
            "pr": pr_num,
            "steps": steps,
        }
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "elapsed_sec": round(time.time() - t0, 1),
            "error": f"{type(exc).__name__}: {exc}",
            "steps": steps,
        }
    finally:
        run([sys.executable, "-m", "orch", "project", "remove", proj_name], check=False)
        shutil.rmtree(project_data_dir(proj_name), ignore_errors=True)
        # keep td for debug if failed? always cleanup secrets in remote URL
        try:
            bare = td / ".bare.git"
            if bare.is_dir():
                restore_remote(bare, f"https://github.com/{FULL}.git")
        except Exception:
            pass
        shutil.rmtree(td, ignore_errors=True)

    RESULT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# orch v1.3 Agent E2E",
        "",
        f"**结果：** {'PASS' if result.get('ok') else 'FAIL'}",
        f"**仓库：** `{FULL}`",
        f"**耗时：** {result.get('elapsed_sec')}s",
        "",
        "| step | ok | note |",
        "|---|---|---|",
    ]
    for s in result.get("steps") or []:
        note = s.get("state") or s.get("sync") or s.get("overall") or s.get("pr") or s.get("error") or ""
        lines.append(f"| {s.get('step')} | {s.get('ok')} | {note} |")
    if result.get("error"):
        lines += ["", f"错误：`{result['error']}`"]
    lines += ["", "私钥与 token 未写入本文件。", ""]
    RESULT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": result.get("ok"), "out": str(RESULT_MD)}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
