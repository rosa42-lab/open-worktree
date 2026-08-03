#!/usr/bin/env python3
"""紧急恢复 develop（Phase0 force 误推后）。不打印 token。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "v", Path(r"E:\open-worktree\docs\probe\_phase0_runtime_verify.py")
)
v = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(v)

integ = v.installation_token(v.INTEGRATION)
print("develop_before", v.get_ref(integ, "develop"))

target = None
code, data = v.http_json("GET", v.repo_api("/commits/a1b410e982aa"), integ)
if code == 200 and isinstance(data, dict):
    target = data["sha"]
    print("restore_target", "a1b410e", target)
else:
    target = "910ae581de6820e0cdd40f2e216f77117c82bec5"
    print("restore_target", "910ae58", target)

code2, _ = v.update_ref(integ, "develop", target, force=True)
print("restore_http", code2)
print("develop_after", v.get_ref(integ, "develop"))
print("master", v.get_ref(integ, "master"))

# 清理探测分支
for b in (
    "orch-phase0-probe-1785581559",
):
    c, _ = v.delete_ref(integ, b)
    print("delete", b, c)
