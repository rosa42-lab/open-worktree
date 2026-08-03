#!/usr/bin/env python3
"""One-off: force-restore develop using the Integration App token.

Precondition: develop-no-force is DISABLED (done via admin token).
develop-updates (Restrict updates) allows ONLY the Integration App to push
develop, so the Integration token is the one that can perform the restore
now that the force-block ruleset is off.

After this succeeds, re-enable develop-no-force with the admin token.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _phase0_runtime_verify import (  # noqa: E402
    INTEGRATION,
    installation_token,
    get_ref,
    http_json,
)

LEGIT_DEVELOP_TIP = "a1b410e982aa844aa83f5af44f6a666eff16c685"


def main() -> int:
    tok = installation_token(INTEGRATION)
    try:
        current = get_ref(tok, "develop")
    except Exception as e:
        print(f"read develop failed: {e}")
        return 1
    print(f"current develop : {current}")
    print(f"target  develop : {LEGIT_DEVELOP_TIP}")

    code, resp = http_json(
        "PATCH",
        f"https://api.github.com/repos/rosa42-lab/open-worktree/git/refs/heads/develop",
        tok,
        body={"sha": LEGIT_DEVELOP_TIP, "force": True},
    )
    print(f"force update (Integration token): HTTP {code}")
    print(f"  {str(resp)[:200]}")
    return 0 if code in (200, 201, 204) else 2


if __name__ == "__main__":
    raise SystemExit(main())