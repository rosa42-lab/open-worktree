#!/usr/bin/env python3
"""Restore develop to the legitimate tip AND test non_fast_forward enforcement.

The rerun #4 test was flawed: it created a child of the current tip, so the
update was actually a fast-forward (200 was expected even with force=true).

This script does a TRUE non-fast-forward update:
  develop: 565166b (probe artifact) -> a1b410e (legitimate tip)
Going BACK removes the probe commit => genuinely non-fast-forward.

Observations:
  HTTP 422/403 => develop-no-force ruleset IS enforced on REST refs updates.
                  develop stays at 565166b; break-glass restore needed.
  HTTP 200     => ruleset NOT enforced on REST force updates; develop restored.
                  Must note: REST can bypass force protection; real git push
                  test required on a network that allows git protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _phase0_runtime_verify import (  # noqa: E402
    INTEGRATION,
    installation_token,
    get_ref,
    update_ref,
)

LEGIT_DEVELOP_TIP = "a1b410e982aa844aa83f5af44f6a666eff16c685"


def main() -> int:
    tok = installation_token(INTEGRATION)
    try:
        current = get_ref(tok, "develop")
    except Exception as e:
        print(f"could not read develop: {e}")
        return 1
    print(f"current develop : {current}")
    print(f"target  develop : {LEGIT_DEVELOP_TIP}")

    code, resp = update_ref(tok, "develop", LEGIT_DEVELOP_TIP, force=True)
    print(f"\nforce update develop -> {LEGIT_DEVELOP_TIP[:12]}  HTTP {code}")
    print(f"response: {str(resp)[:300]}")

    if code in (403, 404, 422):
        print("\nVERDICT: develop-no-force enforced on REST force updates. ✓")
        print("develop still at 565166b — break-glass restore required.")
        return 2
    if code in (200, 201, 204):
        print("\nVERDICT: REST force update succeeded (develop restored to a1b410e).")
        print("WARNING: non_fast_forward NOT enforced on REST refs PATCH with force=true.")
        print("Real git-protocol push test needed to confirm protection.")
        return 0
    print(f"\nVERDICT: unexpected HTTP {code}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())