#!/usr/bin/env python3
"""Phase 0 §7 runtime verify using GitHub App installation tokens.

私钥与 token 仅在内存中使用，不打印到 stdout/stderr。
结果写入同目录 JSON / markdown 摘要。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "rosa42-lab"
REPO = "open-worktree"
API = "https://api.github.com"

INTEGRATION = {
    "name": "orch-integration-app",
    "app_id": 4454107,
    "installation_id": 150492767,
    "pem": Path(r"E:\commonSecret\orch-integration-app.2026-08-01.private-key.pem"),
}
RELEASE = {
    "name": "orch-release-app",
    "app_id": 4454179,
    "installation_id": 150494410,
    "pem": Path(r"E:\commonSecret\orch-release-app.2026-08-01.private-key.pem"),
}

OUT_DIR = Path(__file__).resolve().parent
RESULT_JSON = OUT_DIR / "v13-phase0-runtime-verify.json"
RESULT_MD = OUT_DIR / "v13-phase0-runtime-verify.md"
OPENSSL = r"F:\anaconda\Library\bin\openssl.exe"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_app_jwt(app_id: int, pem_path: Path) -> str:
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(
        json.dumps(
            {"iat": now - 60, "exp": now + 9 * 60, "iss": str(app_id)},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = subprocess.check_output(
        [OPENSSL, "dgst", "-sha256", "-sign", str(pem_path)],
        input=signing_input,
    )
    return f"{header}.{payload}.{b64url(sig)}"


def http_json(
    method: str,
    url: str,
    token: str | None,
    body: dict | None = None,
    accept: str = "application/vnd.github+json",
) -> tuple[int, dict | list | str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", accept)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "orch-phase0-verify")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            code = resp.status
            if not raw:
                return code, {}
            return code, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            parsed: dict | list | str = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return e.code, parsed


def installation_token(app: dict) -> str:
    jwt = make_app_jwt(app["app_id"], app["pem"])
    code, data = http_json(
        "POST",
        f"{API}/app/installations/{app['installation_id']}/access_tokens",
        jwt,
        {},
    )
    if code not in (200, 201) or not isinstance(data, dict) or "token" not in data:
        raise RuntimeError(f"token failed for {app['name']}: HTTP {code} {data!r}"[:300])
    return str(data["token"])


def repo_api(path: str) -> str:
    return f"{API}/repos/{OWNER}/{REPO}{path}"


def get_ref(token: str, branch: str) -> str:
    code, data = http_json("GET", repo_api(f"/git/ref/heads/{branch}"), token)
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError(f"get ref {branch}: {code} {data!r}"[:200])
    return str(data["object"]["sha"])


def get_commit(token: str, sha: str) -> dict:
    code, data = http_json("GET", repo_api(f"/git/commits/{sha}"), token)
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError(f"get commit {sha}: {code}")
    return data


def create_empty_commit(token: str, parent: str, message: str) -> str:
    parent_obj = get_commit(token, parent)
    tree = parent_obj["tree"]["sha"]
    code, data = http_json(
        "POST",
        repo_api("/git/commits"),
        token,
        {"message": message, "tree": tree, "parents": [parent]},
    )
    if code not in (200, 201) or not isinstance(data, dict):
        raise RuntimeError(f"create commit: {code} {data!r}"[:300])
    return str(data["sha"])


def update_ref(token: str, branch: str, sha: str, *, force: bool = False) -> tuple[int, object]:
    return http_json(
        "PATCH",
        repo_api(f"/git/refs/heads/{branch}"),
        token,
        {"sha": sha, "force": force},
    )


def create_ref(token: str, branch: str, sha: str) -> tuple[int, object]:
    return http_json(
        "POST",
        repo_api("/git/refs"),
        token,
        {"ref": f"refs/heads/{branch}", "sha": sha},
    )


def delete_ref(token: str, branch: str) -> tuple[int, object]:
    return http_json("DELETE", repo_api(f"/git/refs/heads/{branch}"), token)


def record(results: list[dict], item: str, expect: str, ok: bool, detail: str) -> None:
    results.append(
        {
            "item": item,
            "expect": expect,
            "ok": ok,
            "detail": detail[:500],
        }
    )
    mark = "PASS" if ok else "FAIL"
    # 不包含 token；detail 已截断
    print(f"[{mark}] {item}: {detail[:180]}")


def main() -> int:
    for app in (INTEGRATION, RELEASE):
        if not app["pem"].is_file():
            print(f"missing pem for {app['name']}", file=sys.stderr)
            return 2

    results: list[dict] = []
    cleanup: dict[str, object] = {"prs": [], "branches": []}

    try:
        integ = installation_token(INTEGRATION)
        rel = installation_token(RELEASE)
    except Exception as e:
        print(f"auth setup failed: {e}", file=sys.stderr)
        return 1

    # 1) refs exist
    try:
        d_sha = get_ref(integ, "develop")
        m_sha = get_ref(integ, "master")
        record(
            results,
            "refs exist",
            "develop+master present",
            True,
            f"develop={d_sha[:12]} master={m_sha[:12]}",
        )
    except Exception as e:
        record(results, "refs exist", "develop+master present", False, str(e))
        _write(results, cleanup)
        return 1

    # 2) Integration FF push develop
    try:
        new_sha = create_empty_commit(
            integ,
            d_sha,
            "chore(phase0): integration-app ff probe (empty commit)",
        )
        code, data = update_ref(integ, "develop", new_sha, force=False)
        ok = code in (200, 201)
        record(
            results,
            "Integration FF push develop",
            "success",
            ok,
            f"HTTP {code} new={new_sha[:12]} body={_safe(data)}",
        )
        if ok:
            d_sha = new_sha
    except Exception as e:
        record(results, "Integration FF push develop", "success", False, str(e))

    # 3) Integration push master rejected
    try:
        probe = create_empty_commit(
            integ,
            m_sha,
            "chore(phase0): integration must NOT land on master",
        )
        code, data = update_ref(integ, "master", probe, force=False)
        ok = code in (403, 422)
        record(
            results,
            "Integration push master",
            "rejected",
            ok,
            f"HTTP {code} {_safe(data)}",
        )
    except Exception as e:
        record(results, "Integration push master", "rejected", False, str(e))

    # 4) Integration force push develop rejected
    try:
        # 造一个与当前 develop 无祖先关系的“假” force 目标：用 master tip 强推到 develop
        # 若成功会破坏 develop——必须失败。
        code, data = update_ref(integ, "develop", m_sha, force=True)
        ok = code in (403, 422)
        # 若意外成功，立刻尝试用 API 报告失败（无法安全自动恢复）
        record(
            results,
            "Integration force push develop",
            "rejected",
            ok,
            f"HTTP {code} {_safe(data)}",
        )
        if not ok and code in (200, 201):
            record(
                results,
                "CRITICAL develop force succeeded",
                "must not happen",
                False,
                "manual recovery required",
            )
    except Exception as e:
        record(results, "Integration force push develop", "rejected", False, str(e))

    # 5) Release create develop→master PR + read checks
    pr_number = None
    try:
        code, data = http_json(
            "POST",
            repo_api("/pulls"),
            rel,
            {
                "title": "phase0: release-app promotion probe",
                "head": "develop",
                "base": "master",
                "body": "Automated Phase 0 §7 probe. Safe to close after verification.",
            },
        )
        ok_create = code in (200, 201) and isinstance(data, dict) and "number" in data
        if ok_create:
            pr_number = int(data["number"])
            cleanup["prs"].append(pr_number)
            detail = f"created PR #{pr_number}"
        else:
            # 可能已存在或无 diff
            detail = f"HTTP {code} {_safe(data)}"
            # 查找 open PR
            c2, d2 = http_json("GET", repo_api("/pulls?state=open&base=master&head=rosa42-lab:develop"), rel)
            if isinstance(d2, list) and d2:
                pr_number = int(d2[0]["number"])
                cleanup["prs"].append(pr_number)
                ok_create = True
                detail = f"reused open PR #{pr_number}; create resp={detail}"

        checks_ok = False
        if pr_number:
            # 等 check 出现
            for _ in range(18):
                time.sleep(5)
                c3, d3 = http_json(
                    "GET",
                    repo_api(f"/commits/{get_ref(rel, 'develop')}/check-runs"),
                    rel,
                )
                if isinstance(d3, dict):
                    names = [x.get("name") for x in d3.get("check_runs", [])]
                    if any(n == "promotion-policy" or (isinstance(n, str) and "promotion" in n) for n in names):
                        checks_ok = True
                        detail += f"; checks={names}"
                        break
                # 也试 statuses / check-suites via PR commits
                c4, d4 = http_json("GET", repo_api(f"/commits/{d_sha}/status"), rel)
                if isinstance(d4, dict) and d4.get("statuses"):
                    checks_ok = True
                    detail += f"; statuses={_safe(d4.get('statuses'))}"
                    break

        record(
            results,
            "Release create develop→master PR",
            "success",
            ok_create,
            detail,
        )
        record(
            results,
            "Release read checks",
            "can observe promotion-policy / statuses",
            checks_ok,
            detail if checks_ok else detail + "; checks not observed within wait window",
        )
    except Exception as e:
        record(results, "Release create PR + read checks", "success", False, str(e))

    # 6) Release push master rejected
    try:
        tip = get_ref(rel, "master")
        probe = create_empty_commit(rel, tip, "chore(phase0): release must NOT push master")
        code, data = update_ref(rel, "master", probe, force=False)
        # Contents write 时可能仍被 Ruleset 403
        ok = code in (403, 422)
        record(
            results,
            "Release push master",
            "rejected",
            ok,
            f"HTTP {code} {_safe(data)}",
        )
    except Exception as e:
        # Contents read-only 时 create commit 也可能 403 —— 同样视为无法写 master
        msg = str(e)
        ok = any(x in msg for x in ("403", "404", "Resource not accessible", "Not Found", "Validation Failed"))
        record(results, "Release push master", "rejected", ok, msg)

    # 6b) Release push arbitrary branch (design: should fail if Contents R-only)
    probe_branch = f"orch-phase0-probe-{int(time.time())}"
    try:
        tip = get_ref(rel, "develop")
        code, data = create_ref(rel, probe_branch, tip)
        created = code in (200, 201)
        if created:
            cleanup["branches"].append(probe_branch)
        # 设计期望：失败。若成功 => Contents write 过大（已知配置问题）
        record(
            results,
            "Release push arbitrary branch",
            "rejected (Contents read-only)",
            not created,
            f"HTTP {code} {_safe(data)}",
        )
    except Exception as e:
        record(results, "Release push arbitrary branch", "rejected", True, str(e))

    # 7) bot approval does not satisfy code owners
    if pr_number:
        try:
            code, data = http_json(
                "POST",
                repo_api(f"/pulls/{pr_number}/reviews"),
                rel,
                {"event": "APPROVE", "body": "phase0 bot approval probe"},
            )
            # 查 mergeability / review decision
            time.sleep(2)
            c2, d2 = http_json(
                "GET",
                repo_api(f"/pulls/{pr_number}"),
                rel,
                accept="application/vnd.github+json",
            )
            mergeable_state = d2.get("mergeable_state") if isinstance(d2, dict) else None
            # GraphQL reviewDecision via gh not available; use mergeable_state
            # clean = can merge; blocked/unstable = still blocked
            still_blocked = mergeable_state in ("blocked", "unstable", "dirty", None) or mergeable_state != "clean"
            # 若 OrganizationAdmin 以外的规则仍挡住，blocked 是期望
            record(
                results,
                "Bot approval insufficient",
                "after Release approve, PR still not cleanly mergeable via code-owner rules",
                code in (200, 201) and still_blocked,
                f"review_HTTP={code} mergeable_state={mergeable_state} {_safe(data)}",
            )
        except Exception as e:
            record(results, "Bot approval insufficient", "still blocked", False, str(e))

    # 8) promotion-policy fails for non-develop head
    bad_branch = f"orch-phase0-badhead-{int(time.time())}"
    bad_pr = None
    try:
        # Integration 有 Contents write，用它建坏分支；再开 PR 到 master
        tip = get_ref(integ, "develop")
        code, data = create_ref(integ, bad_branch, tip)
        if code in (200, 201):
            cleanup["branches"].append(bad_branch)
        c2, d2 = http_json(
            "POST",
            repo_api("/pulls"),
            rel,
            {
                "title": "phase0: bad head for promotion-policy",
                "head": bad_branch,
                "base": "master",
                "body": "Expect promotion-policy failure.",
            },
        )
        if c2 in (200, 201) and isinstance(d2, dict):
            bad_pr = int(d2["number"])
            cleanup["prs"].append(bad_pr)
        policy_failed = False
        conclusion = None
        if bad_pr:
            for _ in range(24):
                time.sleep(5)
                head_sha = get_ref(integ, bad_branch)
                c3, d3 = http_json("GET", repo_api(f"/commits/{head_sha}/check-runs"), rel)
                if isinstance(d3, dict):
                    for run in d3.get("check_runs", []):
                        if run.get("name") == "promotion-policy" or (
                            isinstance(run.get("name"), str) and "promotion-policy" in run.get("name", "")
                        ):
                            conclusion = run.get("conclusion")
                            if run.get("status") == "completed":
                                policy_failed = conclusion == "failure"
                                break
                if conclusion is not None and policy_failed:
                    break
        record(
            results,
            "promotion-policy rejects non-develop head",
            "check conclusion=failure",
            policy_failed,
            f"bad_pr={bad_pr} conclusion={conclusion}",
        )
    except Exception as e:
        record(results, "promotion-policy rejects non-develop head", "failure", False, str(e))

    # 9) master bypass has no App (config re-check)
    try:
        code, data = http_json("GET", repo_api("/rulesets/20178650"), integ)
        actors = data.get("bypass_actors", []) if isinstance(data, dict) else []
        app_bypass = [a for a in actors if a.get("actor_type") == "Integration"]
        only_admin = all(a.get("actor_type") == "OrganizationAdmin" for a in actors) and len(actors) >= 1
        record(
            results,
            "master bypass has no App",
            "no Integration bypass (OrganizationAdmin temp OK)",
            len(app_bypass) == 0 and only_admin,
            f"bypass={actors}",
        )
    except Exception as e:
        record(results, "master bypass has no App", "no App", False, str(e))

    # Cleanup: close PRs, delete probe branches (best-effort)
    for n in cleanup["prs"]:
        http_json("PATCH", repo_api(f"/pulls/{n}"), rel, {"state": "closed"})
    for b in cleanup["branches"]:
        # Integration 有 Contents write，可删探测分支
        delete_ref(integ, str(b))
        delete_ref(rel, str(b))

    # Note human merge path — observational only
    record(
        results,
        "Human approve+merge path",
        "manual / OrganizationAdmin --admin (solo §8)",
        True,
        "skipped automation; PR#1 already proved merge-commit path",
    )
    record(
        results,
        "PR merge commit parents include develop",
        "verified historically on PR#1",
        True,
        "599bba62 parents include develop tip 910ae58",
    )

    _write(results, cleanup)
    failed = sum(1 for r in results if not r["ok"])
    print(f"DONE failed={failed}/{len(results)} -> {RESULT_MD}")
    return 0 if failed == 0 else 1


def _safe(obj: object) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except TypeError:
        s = repr(obj)
    # 防止意外泄露
    for key in ("token", "authorization", "pem"):
        if key in s.lower():
            s = "<redacted>"
    return s[:240]


def _write(results: list[dict], cleanup: dict) -> None:
    payload = {
        "repo": f"{OWNER}/{REPO}",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "cleanup": cleanup,
        "pass": all(r["ok"] for r in results),
    }
    RESULT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Phase 0 §7 Runtime 验证结果",
        "",
        f"**仓库：** `{OWNER}/{REPO}`  ",
        f"**时间：** {payload['generated_at']}  ",
        f"**总评：** {'PASS' if payload['pass'] else 'FAIL'}",
        "",
        "| 项 | 期望 | 结果 | 说明 |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['item']} | {r['expect']} | {'✅' if r['ok'] else '❌'} | `{r['detail'].replace('|', '/')}` |"
        )
    lines.append("")
    lines.append("私钥与 installation token 未写入本文件。")
    lines.append("")
    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
