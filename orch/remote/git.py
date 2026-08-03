"""RemoteGitAdapter CLI 实现（V13-003/007；sync_verified_merge = V13-011）。"""

from __future__ import annotations

from pathlib import Path

from orch.errors import ExitCode, OrchError
from orch.git.ref import run_git_ref


class WritePathDisabledError(OrchError):
    def __init__(self, method: str) -> None:
        super().__init__(
            f"{method} disabled until release-sync / candidate-sync path",
            code=ExitCode.GENERAL,
            kind="remote_write_disabled",
            details={"method": method},
        )


class CliRemoteGitAdapter:
    """通过 orch.git 执行 fetch / ancestry；禁止 Phase 1 远端写。"""

    def fetch_core_refs(
        self,
        bare: Path,
        remote: str,
        develop: str,
        master: str,
    ) -> None:
        # 显式 refspec，避免依赖 push.default / 当前 checkout
        specs = [
            f"+refs/heads/{develop}:refs/remotes/{remote}/{develop}",
            f"+refs/heads/{master}:refs/remotes/{remote}/{master}",
        ]
        result = run_git_ref(["fetch", remote, *specs], bare)
        if not result.ok:
            raise OrchError(
                f"git fetch failed for remote {remote!r}",
                code=ExitCode.GIT,
                kind="remote_fetch_failed",
                details={
                    "remote": remote,
                    "returncode": result.returncode,
                    "stderr": _redact_git_text(result.stderr),
                },
            )

    def remote_head(self, bare: Path, remote: str, branch: str) -> str | None:
        ref = f"refs/remotes/{remote}/{branch}"
        result = run_git_ref(["rev-parse", "--verify", ref], bare)
        if result.ok:
            return result.stdout.strip() or None
        # 回退 ls-remote（未 fetch 时）
        ls = run_git_ref(["ls-remote", remote, f"refs/heads/{branch}"], bare)
        if not ls.ok:
            return None
        line = (ls.stdout or "").strip().splitlines()
        if not line:
            return None
        sha = line[0].split()[0].strip()
        return sha or None

    def local_head(self, bare: Path, branch: str) -> str | None:
        result = run_git_ref(["rev-parse", "--verify", f"refs/heads/{branch}"], bare)
        if not result.ok:
            return None
        return result.stdout.strip() or None

    def is_ancestor(self, bare: Path, older: str, newer: str) -> bool:
        result = run_git_ref(
            ["merge-base", "--is-ancestor", older, newer],
            bare,
        )
        return result.returncode == 0

    def push_fast_forward(
        self,
        bare: Path,
        remote: str,
        source_ref: str,
        target_ref: str,
        expected_old_sha: str,
        new_sha: str,
    ) -> None:
        """非强制 CAS：expected_old -> new；禁止 --force / --force-with-lease。"""
        if not expected_old_sha or not new_sha:
            raise OrchError(
                "CAS push requires expected_old_sha and new_sha",
                code=ExitCode.VALIDATION,
                kind="remote_cas_invalid",
                details={},
            )
        if expected_old_sha == new_sha:
            raise OrchError(
                "CAS push new_sha must differ from expected_old_sha",
                code=ExitCode.VALIDATION,
                kind="remote_cas_invalid",
                details={},
            )
        # 确认对象存在
        for label, sha in (("expected_old_sha", expected_old_sha), ("new_sha", new_sha)):
            chk = run_git_ref(["cat-file", "-e", f"{sha}^{{commit}}"], bare)
            if not chk.ok:
                raise OrchError(
                    f"{label} is not a commit in bare repo",
                    code=ExitCode.GIT,
                    kind="remote_cas_invalid",
                    details={"field": label},
                )
        if not self.is_ancestor(bare, expected_old_sha, new_sha):
            raise OrchError(
                "new_sha is not a fast-forward of expected_old_sha",
                code=ExitCode.VALIDATION,
                kind="remote_non_fast_forward",
                details={
                    "expected_old_sha": expected_old_sha,
                    "new_sha": new_sha,
                },
            )
        # 可选：source_ref 解析应等于 new_sha
        if source_ref:
            src = run_git_ref(["rev-parse", "--verify", source_ref], bare)
            if src.ok and src.stdout.strip() and src.stdout.strip() != new_sha:
                raise OrchError(
                    "source_ref does not resolve to new_sha",
                    code=ExitCode.VALIDATION,
                    kind="remote_cas_invalid",
                    details={"source_ref": source_ref, "new_sha": new_sha},
                )

        tip = self._live_remote_tip(bare, remote, _branch_from_ref(target_ref))
        if tip == new_sha:
            return
        if tip != expected_old_sha:
            raise OrchError(
                "remote tip does not match expected_old_sha (CAS race)",
                code=ExitCode.GIT,
                kind="remote_cas_race",
                details={
                    "expected_old_sha": expected_old_sha,
                    "observed_tip": tip,
                    "new_sha": new_sha,
                },
            )

        # 明确 SHA:refspec；无 --force / --force-with-lease
        refspec = f"{new_sha}:{target_ref}"
        result = run_git_ref(["push", remote, refspec], bare)
        if not result.ok:
            raise OrchError(
                "git push fast-forward failed",
                code=ExitCode.GIT,
                kind="remote_push_failed",
                details={
                    "remote": remote,
                    "refspec": refspec,
                    "returncode": result.returncode,
                    "stderr": _redact_git_text(result.stderr),
                },
            )

        tip2 = self._live_remote_tip(bare, remote, _branch_from_ref(target_ref))
        if tip2 != new_sha:
            raise OrchError(
                "post-push remote tip mismatch",
                code=ExitCode.GIT,
                kind="remote_postcheck_mismatch",
                details={"expected": new_sha, "observed": tip2},
            )

    def _live_remote_tip(self, bare: Path, remote: str, branch: str) -> str | None:
        """始终 ls-remote，避免 stale remote-tracking refs。"""
        ls = run_git_ref(["ls-remote", remote, f"refs/heads/{branch}"], bare)
        if not ls.ok:
            return None
        line = (ls.stdout or "").strip().splitlines()
        if not line:
            return None
        sha = line[0].split()[0].strip()
        return sha or None

    def sync_verified_merge(
        self,
        bare: Path,
        source_sha: str,
        published_sha: str,
    ) -> None:
        """
        证明 source_sha → published_sha 为 FF，并将 local develop 更新到 published_sha。
        不 force；不推远端（CAS push 由调用方编排）。
        """
        if not source_sha or not published_sha:
            raise OrchError(
                "sync_verified_merge requires source_sha and published_sha",
                code=ExitCode.USAGE,
                kind="remote_sync_invalid",
            )
        if source_sha == published_sha:
            # already at tip
            tip = self.local_head(bare, "develop")
            if tip == published_sha:
                return
        if not self.is_ancestor(bare, source_sha, published_sha):
            raise OrchError(
                "published_sha is not a descendant of source_sha",
                code=ExitCode.GIT,
                kind="remote_sync_not_ff",
                details={"source_sha": source_sha, "published_sha": published_sha},
            )
        # update local refs/heads/develop to published_sha without checkout
        result = run_git_ref(
            ["update-ref", "refs/heads/develop", published_sha, source_sha],
            bare,
        )
        if not result.ok:
            # allow if current tip already published or is source (retry)
            tip = self.local_head(bare, "develop")
            if tip == published_sha:
                return
            if tip == source_sha:
                # unconditional update when old matches source but update-ref raced
                result2 = run_git_ref(
                    ["update-ref", "refs/heads/develop", published_sha],
                    bare,
                )
                if result2.ok:
                    return
            raise OrchError(
                "failed to update local develop to published_sha",
                code=ExitCode.GIT,
                kind="remote_sync_ref_update_failed",
                details={
                    "stderr": _redact_git_text(result.stderr),
                    "source_sha": source_sha,
                    "published_sha": published_sha,
                    "tip": tip,
                },
            )
        tip2 = self.local_head(bare, "develop")
        if tip2 != published_sha:
            raise OrchError(
                "post-sync local develop tip mismatch",
                code=ExitCode.GIT,
                kind="remote_sync_postcheck_mismatch",
                details={"expected": published_sha, "observed": tip2},
            )


def _branch_from_ref(ref: str) -> str:
    ref = ref.strip()
    prefix = "refs/heads/"
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return ref


def _redact_git_text(text: str) -> str:
    """脱敏可能含 URL 凭据的 git 输出（保留长度上限）。"""
    if not text:
        return ""
    redacted = text
    # user:token@host → user:***@host
    import re

    redacted = re.sub(
        r"(://[^:@/\s]+):([^@/\s]+)@",
        r"\1:***@",
        redacted,
    )
    return redacted.strip()[:800]


def classify_ref_relation(
    adapter: CliRemoteGitAdapter,
    bare: Path,
    local_sha: str | None,
    remote_sha: str | None,
) -> str:
    """in_sync / local_ahead / remote_ahead / diverged / unknown。"""
    if not local_sha or not remote_sha:
        return "unknown"
    if local_sha == remote_sha:
        return "in_sync"
    remote_is_anc = adapter.is_ancestor(bare, remote_sha, local_sha)
    local_is_anc = adapter.is_ancestor(bare, local_sha, remote_sha)
    if remote_is_anc and not local_is_anc:
        return "local_ahead"
    if local_is_anc and not remote_is_anc:
        return "remote_ahead"
    return "diverged"
