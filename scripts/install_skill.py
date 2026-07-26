#!/usr/bin/env python3
"""Install orchestrator SKILL.md to design path + OpenCode/agent discovery paths."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "orchestrator" / "SKILL.md"


def destinations() -> list[Path]:
    home = Path.home()
    return [
        # Design §16 path
        home / ".orchestrator" / "skills" / "orchestrator" / "SKILL.md",
        # OpenCode global
        home / ".config" / "opencode" / "skills" / "orchestrator" / "SKILL.md",
        # Common agent skill locations OpenCode also scans
        home / ".agents" / "skills" / "orchestrator" / "SKILL.md",
        home / ".claude" / "skills" / "orchestrator" / "SKILL.md",
        # Repo-local OpenCode discovery (when working inside this repo)
        ROOT / ".opencode" / "skills" / "orchestrator" / "SKILL.md",
    ]


def main() -> int:
    if not SRC.is_file():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    for dst in destinations():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, dst)
        print(f"installed: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
