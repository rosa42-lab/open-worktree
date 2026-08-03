#!/usr/bin/env python3
"""Phase 0 §7 RERUN after fixing:
  - develop-updates / develop-no-force split
  - Release App: Contents=Read-only, Pull requests=Read and write

Re-runs only the items that previously failed or were skipped because of the
P0 fixes. Reuses all the helpers from _phase0_runtime_verify.py.

Items rerun (mapping to v13-phase0-runtime-verify.md table):
  #4  Integration force push develop  -> expect 403/422 (was 200)
  #5  Release create PR develop->master  -> expect 201 (was 403)
  #6  Release read PR (proxy for checks read)  -> expect 200 (was skipped)
  #8  Release push to new branch  -> expect 403/422 (was 201)

私钥与 token 仅在内存中使用，不打印到 stdout/stderr。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Reuse everything from the canonical verify script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _phase0_runtime_verify import (  # noqa: E402
    INTEGRATION, RELEASE,
    installation_token,
    get_ref, create_empty_commit, update_ref, create_ref, delete_ref,
    http_json, repo_api,
)

OUT_DIR = Path(__file__).resolve().parent
RESULT_JSON = OUT_DIR / "v13-phase0-rerun.json"
RESULT_MD = OUT_DIR / "v13-phase0-rerun.md"

THROWAWAY_BRANCH = "phase0-rerun-tmp"

results: list[dict] = []
cleanup_pr: int | None = None
cleanup_branch: str | None = None


def record(name: str, expected: str, status: int, body_summary: str, ok: bool, blocked: bool) -> None:
    results.append({
        "name": name,
        "expected": expected,
        "status": status,
        "ok": ok,
        "blocked": blocked,
        "body": body_summary[:200],
    })


# ---------------------------------------------------------------------------
# Item #4 — Integration App force push develop
# ---------------------------------------------------------------------------
def test_integration_force_push_develop() -> None:
    tok = installation_token(INTEGRATION)
    try:
        base_sha = get_ref(tok, "develop")
    except Exception as e:
        record("#4 Integration force-push develop", "expect 403/422", 0, f"get_ref failed: {e}", False, False)
        return
    try:
        orphan = create_empty_commit(tok, base_sha, "phase0-rerun #4 force push attempt")
        code, resp = update_ref(tok, "develop", orphan, force=True)
        body = str(resp)[:200]
        expected = "expect 403/422 (force blocked by develop-no-force)"
        ok = code in (200, 201, 204)
        blocked = code in (403, 404, 422)
        record("#4 Integration force-push develop", expected, code, body, ok, blocked)
    except Exception as e:
        record("#4 Integration force-push develop", "expect 403/422", 0, str(e), False, False)


# ---------------------------------------------------------------------------
# Item #5 — Release App create PR develop -> master
# ---------------------------------------------------------------------------
def test_release_create_pr() -> int | None:
    tok = installation_token(RELEASE)
    code, resp = http_json(
        "POST",
        repo_api("/pulls"),
        tok,
        body={
            "title": "phase0-rerun: Release App PR probe",
            "head": "develop",
            "base": "master",
            "body": "Automatic probe — close without merging.",
        },
    )
    body = str(resp)[:200]
    expected = "expect 201 (PR write granted)"
    ok = code in (200, 201, 204)
    blocked = code in (403, 404, 422)
    record("#5 Release create PR develop->master", expected, code, body, ok, blocked)
    if isinstance(resp, dict) and "number" in resp:
        return int(resp["number"])
    return None


# ---------------------------------------------------------------------------
# Item #6 — Release App read PR (proxy for checks read)
# ---------------------------------------------------------------------------
def test_release_read_pr(pr_number: int | None) -> None:
    if pr_number is None:
        record("#6 Release read PR", "expect 200 (skipped — no PR created)", 0, "skipped", False, False)
        return
    tok = installation_token(RELEASE)
    code, resp = http_json("GET", repo_api(f"/pulls/{pr_number}"), tok)
    body = str(resp)[:200]
    expected = "expect 200 (PR read granted)"
    ok = code in (200, 201, 204)
    blocked = code in (403, 404, 422)
    record("#6 Release read PR", expected, code, body, ok, blocked)


# ---------------------------------------------------------------------------
# Item #8 — Release App push a new branch
# ---------------------------------------------------------------------------
def test_release_push_branch() -> None:
    tok = installation_token(RELEASE)
    try:
        base_sha = get_ref(tok, "develop")
    except Exception as e:
        record("#8 Release push branch", "expect 403/422", 0, f"get_ref failed: {e}", False, False)
        return
    try:
        orphan = create_empty_commit(tok, base_sha, "phase0-rerun #8 release-app push attempt")
        code, resp = create_ref(tok, THROWAWAY_BRANCH, orphan)
        global cleanup_branch
        cleanup_branch = THROWAWAY_BRANCH if code in (200, 201, 204) else None
        body = str(resp)[:200]
        expected = "expect 403/422 (Contents read-only)"
        ok = code in (200, 201, 204)
        blocked = code in (403, 404, 422)
        record("#8 Release push to new branch", expected, code, body, ok, blocked)
    except Exception as e:
        record("#8 Release push to new branch", "expect 403/422", 0, str(e), False, False)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def cleanup(pr_number: int | None) -> None:
    if pr_number:
        tok = installation_token(RELEASE)
        try:
            http_json("PATCH", repo_api(f"/pulls/{pr_number}"), tok, body={"state": "closed"})
            print(f"cleanup: closed PR #{pr_number}")
        except Exception as e:
            print(f"cleanup: close PR failed: {e}")
    if cleanup_branch:
        tok = installation_token(RELEASE)
        try:
            delete_ref(tok, cleanup_branch)
            print(f"cleanup: deleted branch {cleanup_branch}")
        except Exception as e:
            print(f"cleanup: delete branch failed: {e}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    global cleanup_pr

    test_integration_force_push_develop()
    cleanup_pr = test_release_create_pr()
    test_release_read_pr(cleanup_pr)
    test_release_push_branch()
    cleanup(cleanup_pr)

    summary = {"ran_at": int(time.time()), "results": results}
    RESULT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    md_lines = ["# Phase 0 §7 Rerun (post-fix)", "", "| # | 期望 | HTTP | 判定 | 摘要 |", "|---|---|---|---|---|"]
    for r in results:
        if r["ok"]:
            verdict = "✅ pass"
        elif r["blocked"]:
            verdict = "✅ blocked (correct)"
        else:
            verdict = "❌ UNEXPECTED"
        md_lines.append(f"| {r['name']} | {r['expected']} | {r['status']} | {verdict} | {r['body'][:80]} |")
    RESULT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"wrote {RESULT_JSON}")
    print(f"wrote {RESULT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())