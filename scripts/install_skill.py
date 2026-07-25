#!/usr/bin/env python3
"""Copy repo SKILL.md to ~/.orchestrator/skills/orchestrator/SKILL.md."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "orchestrator" / "SKILL.md"
DST = Path.home() / ".orchestrator" / "skills" / "orchestrator" / "SKILL.md"


def main() -> int:
    if not SRC.is_file():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, DST)
    print(f"installed: {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
