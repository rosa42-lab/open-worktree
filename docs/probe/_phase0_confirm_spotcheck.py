#!/usr/bin/env python3
"""Independent spot-check after Phase 0 claimed PASS. No token printed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "v", Path(r"E:\open-worktree\docs\probe\_phase0_runtime_verify.py")
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

integ = mod.installation_token(mod.INTEGRATION)
rel = mod.installation_token(mod.RELEASE)

d = mod.get_ref(integ, "develop")
m = mod.get_ref(integ, "master")
print("tips", d[:12], m[:12])

# True non-FF force: try move develop to master tip with force=true
code, data = mod.update_ref(integ, "develop", m, force=True)
msg = ""
if isinstance(data, dict):
    msg = str(data.get("message", data))[:160]
print("force_develop", code, msg)
print("force_ok_expect_reject", code in (403, 422))

# After force attempt, tip must be unchanged
d2 = mod.get_ref(integ, "develop")
print("develop_unchanged", d2 == d, d2[:12])

# Release cannot create commit (contents read-only)
try:
    mod.create_empty_commit(rel, d, "phase0 confirm: release must not write")
    print("release_create_commit", "UNEXPECTED_SUCCESS")
except Exception as e:
    print("release_create_commit_blocked", str(e)[:200])

# Release can read pulls
code3, data3 = mod.http_json("GET", mod.repo_api("/pulls?state=closed&per_page=1"), rel)
print("release_read_pulls", code3)

# Integration still cannot update master
probe = None
try:
    probe = mod.create_empty_commit(integ, m, "phase0 confirm: integ must not write master")
    code4, data4 = mod.update_ref(integ, "master", probe, force=False)
    print("integ_push_master", code4, str(data4)[:120] if data4 else "")
except Exception as e:
    print("integ_push_master_err", str(e)[:200])

print("final_develop", mod.get_ref(integ, "develop")[:12])
print("final_master", mod.get_ref(integ, "master")[:12])
