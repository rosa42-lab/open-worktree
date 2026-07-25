"""Create temporary bare repo + develop for tests."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def make_bare_with_develop(root: Path | None = None) -> Path:
    """
    Create:
      <root>/
        .bare.git/
        (optional source used to seed)
    Returns project root containing .bare.git with develop branch.
    """
    if root is None:
        root = Path(tempfile.mkdtemp(prefix="orch-proj-"))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    src = root / "_seed"
    src.mkdir()
    run(["git", "init", "-b", "develop"], cwd=src)
    run(["git", "config", "user.email", "test@example.com"], cwd=src)
    run(["git", "config", "user.name", "Test"], cwd=src)
    (src / "README.md").write_text("hello\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=src)
    run(["git", "commit", "-m", "init"], cwd=src)
    bare = root / ".bare.git"
    run(["git", "clone", "--bare", str(src), str(bare)])
    # identity required for merge commits in main/ (no global config assumed)
    run(["git", "--git-dir", str(bare), "config", "user.email", "test@example.com"])
    run(["git", "--git-dir", str(bare), "config", "user.name", "Test"])
    # ensure develop ref exists
    run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/develop"])
    return root
