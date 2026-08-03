"""Continue agent E2E: release-sync after PR #3 merge (admin)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orch.config import read_config, write_config_atomic
from orch.db import open_project_db
from orch.promotion import repo as promo_repo
from orch.promotion.config import validate_promotion_fields
from orch.promotion.release_sync import release_sync
from orch.remote.auth import mint_installation_token
from orch.remote.git import CliRemoteGitAdapter

FULL = "rosa42-lab/open-worktree"
OPENSSL = r"F:\anaconda\Library\bin\openssl.exe"
INTEG_PEM = Path(r"E:\commonSecret\orch-integration-app.2026-08-01.private-key.pem")
REL_PEM = Path(r"E:\commonSecret\orch-release-app.2026-08-01.private-key.pem")
PREV_TARGET = "599bba62cb029a95dc2a897fad9709f59058ecd9"
PR_ID = "3"
PR_URL = "https://github.com/rosa42-lab/open-worktree/pull/3"
PROJECT = "v13synccont"


def gh_sha(token: str, ref: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{FULL}/git/ref/heads/{ref}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "orch-e2e",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)["object"]["sha"]


class FakeMergedProvider:
    def __init__(self, head_sha: str, merge_sha: str) -> None:
        self.head_sha = head_sha
        self.merge_sha = merge_sha

    def get_pr(self, eid: str) -> dict:
        return {
            "external_id": eid,
            "url": PR_URL,
            "head": "develop",
            "base": "master",
            "head_sha": self.head_sha,
            "base_sha": self.merge_sha,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": self.merge_sha,
            "mergeable": None,
            "mergeable_state": None,
            "merge_method": "merge_commit",
        }

    def get_checks(self, *a, **k):
        return {"checks": []}

    def get_reviews(self, *a, **k):
        return {"reviews": [], "approved_bound_human_count": 0}


def main() -> int:
    integ = mint_installation_token(
        app_id="4454107",
        installation_id="150492767",
        pem_path=INTEG_PEM,
        openssl_bin=OPENSSL,
    )
    rel = mint_installation_token(
        app_id="4454179",
        installation_id="150494410",
        pem_path=REL_PEM,
        openssl_bin=OPENSSL,
    )
    develop = gh_sha(rel, "develop")
    master = gh_sha(rel, "master")
    print("before develop", develop)
    print("before master", master)

    td = Path(tempfile.mkdtemp(prefix="orch-sync-"))
    bare = td / ".bare.git"
    try:
        subprocess.run(
            ["git", "clone", "--bare", f"https://github.com/{FULL}.git", str(bare)],
            check=True,
        )
        subprocess.run([sys.executable, "-m", "orch", "project", "remove", PROJECT], check=False)
        subprocess.run([sys.executable, "-m", "orch", "project", "add", PROJECT, str(td)], check=True)
        subprocess.run([sys.executable, "-m", "orch", PROJECT, "init"], check=True)

        entry = validate_promotion_fields(
            {"remote": "origin", "provider": "github", "repository": FULL}
        )
        cfg = read_config()
        cfg.setdefault("promotion", {})[PROJECT] = entry
        write_config_atomic(cfg)

        url = f"https://x-access-token:{integ}@github.com/{FULL}.git"
        subprocess.run(
            ["git", "--git-dir", str(bare), "remote", "set-url", "origin", url],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "--git-dir",
                str(bare),
                "fetch",
                "origin",
                "+refs/heads/develop:refs/heads/develop",
                "+refs/heads/master:refs/heads/master",
            ],
            check=True,
        )

        conn = open_project_db(PROJECT, init=True)
        run = promo_repo.create_run(
            conn,
            project_name=PROJECT,
            kind="master_release",
            mode="promotion_pr",
            remote_name="origin",
            provider="github",
            source_ref="refs/heads/develop",
            target_ref="refs/heads/master",
            source_sha=develop,
            target_sha_before=PREV_TARGET,
            created_by="agent-e2e",
            state="created",
            verification_record_id=None,
        )
        conn.execute(
            "UPDATE promotion_runs SET state=?, published_sha=?, external_id=?, external_url=? WHERE id=?",
            ("master_merged_pending_sync", master, PR_ID, PR_URL, run["id"]),
        )
        conn.commit()
        pid = run["id"]
        conn.close()
        print("promotion", pid)

        os.environ["ORCH_GITHUB_TOKEN"] = integ
        out = release_sync(
            PROJECT,
            pid,
            execute=True,
            fetch=True,
            adapter=CliRemoteGitAdapter(),
            provider=FakeMergedProvider(develop, master),
        )
        print(
            json.dumps(
                {
                    "sync": out.get("sync"),
                    "write_performed": out.get("write_performed"),
                    "state": (out.get("promotion") or {}).get("state"),
                    "error": out.get("error") or out.get("errors"),
                },
                ensure_ascii=False,
            )
        )

        after = gh_sha(rel, "develop")
        print("after develop", after)
        print("sync_ok", after == master)
        return 0 if after == master else 1
    finally:
        try:
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(bare),
                    "remote",
                    "set-url",
                    "origin",
                    f"https://github.com/{FULL}.git",
                ],
                check=False,
            )
        except Exception:
            pass
        subprocess.run([sys.executable, "-m", "orch", "project", "remove", PROJECT], check=False)
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
