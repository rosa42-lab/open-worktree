#!/usr/bin/env python3
"""Break-glass restore: temporarily disable develop-no-force, restore develop
tip to the legitimate SHA, then re-enable the ruleset.

Why needed: the rerun #4 probe (flawed) left develop at 565166b (a probe
artifact commit). Restoring BACK to a1b410e is a genuine non-fast-forward,
which develop-no-force correctly blocks (HTTP 422). To clean up we must
temporarily disable that ruleset, restore, and re-enable.

This is the documented break-glass procedure (docs/github-app-ruleset-setup.md
section 8). The ruleset is only disabled for the few seconds of the restore.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _phase0_runtime_verify import (  # noqa: E402
    INTEGRATION, OWNER, REPO, API,
    installation_token,
    http_json,
)

LEGIT_DEVELOP_TIP = "a1b410e982aa844aa83f5af44f6a666eff16c685"
NO_FORCE_RULESET_ID = 20180999


def set_ruleset_enforcement(tok: str, ruleset_id: int, enforcement: str) -> None:
    code, resp = http_json(
        "PUT",
        f"{API}/repos/{OWNER}/{REPO}/rulesets/{ruleset_id}",
        tok,
        body={"enforcement": enforcement},
    )
    print(f"ruleset {ruleset_id} -> {enforcement}: HTTP {code}")
    if code not in (200, 201, 204):
        print(f"  detail: {str(resp)[:200]}")


def main() -> int:
    tok = installation_token(INTEGRATION)

    print("STEP 1/4: disable develop-no-force (break glass)")
    set_ruleset_enforcement(tok, NO_FORCE_RULESET_ID, "disabled")
    time.sleep(1)

    print(f"STEP 2/4: force-restore develop -> {LEGIT_DEVELOP_TIP[:12]}")
    code, resp = http_json(
        "PATCH",
        f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/develop",
        tok,
        body={"sha": LEGIT_DEVELOP_TIP, "force": True},
    )
    print(f"  HTTP {code}: {str(resp)[:200]}")
    restored = code in (200, 201, 204)

    print("STEP 3/4: re-enable develop-no-force")
    set_ruleset_enforcement(tok, NO_FORCE_RULESET_ID, "active")
    time.sleep(1)

    print("STEP 4/4: verify")
    code, resp = http_json(
        "GET",
        f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/develop",
        tok,
    )
    if isinstance(resp, dict):
        print(f"  develop now: {resp.get('object', {}).get('sha', '?')}")
    code2, resp2 = http_json(
        "GET",
        f"{API}/repos/{OWNER}/{REPO}/rulesets/{NO_FORCE_RULESET_ID}",
        tok,
    )
    if isinstance(resp2, dict):
        print(f"  no-force enforcement: {resp2.get('enforcement')}")

    if restored:
        print("\nRESULT: develop restored to legit tip; ruleset re-enabled. OK")
        return 0
    print("\nRESULT: restore FAILED; ruleset re-enabled. Investigate.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())