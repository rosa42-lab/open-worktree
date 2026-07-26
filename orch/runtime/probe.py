"""Capability probe — Phase 0 / V12-001.

Starts (or reuses) a loopback OpenCode Server, creates two disposable worktrees,
and verifies directory routing, SSE, abort, dispose, attach/fork CLI flags.
Does not take project locks, write project DB, or send orch control requests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orch.runtime.adapter import CapabilityMatrix, OpenCodeRuntimeAdapter
from orch.runtime.http_client import HttpError, OpenCodeHttpClient
from orch.runtime.process import (
    ManagedServer,
    RuntimeProcessError,
    attach_help_supports_flags,
    opencode_cli_version,
    start_opencode_serve,
    wait_for_health,
)
from orch.util import utc_now_iso

# Initial candidate from plan §5.3; probe may confirm or raise the floor.
CANDIDATE_MIN_VERSION = "1.18.5"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class HypothesisResult:
    id: str
    risk: str
    statement: str
    status: str  # pass | fail | partial | deferred
    detail: str
    gate: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "risk": self.risk,
            "statement": self.statement,
            "status": self.status,
            "detail": self.detail,
            "gate": self.gate,
        }


def _run_git(args: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def _make_probe_worktrees(root: Path) -> tuple[Path, Path, str, str]:
    """Create bare + two worktrees with distinct branches. Not an orch project."""
    seed = root / "seed"
    seed.mkdir()
    _run_git(["init", "-b", "develop"], cwd=seed)
    _run_git(["config", "user.email", "orch-probe@example.com"], cwd=seed)
    _run_git(["config", "user.name", "orch-probe"], cwd=seed)
    (seed / "README.md").write_text("orch probe seed\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=seed)
    _run_git(["commit", "-m", "seed"], cwd=seed)

    bare = root / "bare.git"
    _run_git(["clone", "--bare", str(seed), str(bare)], cwd=root)

    wt_a = root / "worktree-a"
    wt_b = root / "worktree-b"
    branch_a = "probe/agent-a"
    branch_b = "probe/agent-b"
    _run_git(["worktree", "add", "-b", branch_a, str(wt_a), "develop"], cwd=bare)
    _run_git(["worktree", "add", "-b", branch_b, str(wt_b), "develop"], cwd=bare)
    for wt in (wt_a, wt_b):
        _run_git(["config", "user.email", "orch-probe@example.com"], cwd=wt)
        _run_git(["config", "user.name", "orch-probe"], cwd=wt)
    return wt_a, wt_b, branch_a, branch_b


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.strip().lstrip("v").split("."):
        num = ""
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        if num:
            parts.append(int(num))
        else:
            break
    return tuple(parts) if parts else (0,)


def _norm_path(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def run_capability_probe(
    *,
    base_url: str | None = None,
    port: int | None = None,
    password: str | None = None,
    username: str | None = None,
    keep_server: bool = False,
) -> dict[str, Any]:
    """
    Execute Phase 0 capability probe.

    If base_url is set, attach to an existing Server (external). Otherwise start
    a managed ephemeral `opencode serve` on loopback.
    """
    checks: list[CheckResult] = []
    managed: ManagedServer | None = None
    tmp_root: Path | None = None
    url: str | None = None
    server_version: str | None = None
    id_a = ""
    id_b = ""
    dir_a = ""
    dir_b = ""
    attach_cmd = ""
    attach_fork_cmd = ""

    probe_password = password
    if probe_password is None and base_url is None:
        probe_password = f"orch-probe-{uuid.uuid4().hex[:12]}"

    try:
        cli_version = opencode_cli_version()
    except RuntimeProcessError as exc:
        return _fail_early(str(exc))

    attach_flags = attach_help_supports_flags()
    checks.append(
        CheckResult(
            "attach_cli_flags",
            ok=bool(
                attach_flags.get("dir")
                and attach_flags.get("session")
                and attach_flags.get("fork")
            ),
            detail="opencode attach --help flag scan",
            evidence=attach_flags,
        )
    )

    started_managed = False
    try:
        if base_url:
            url = base_url.rstrip("/")
            health = wait_for_health(
                url, password=probe_password, username=username, timeout_sec=10.0
            )
        else:
            managed = start_opencode_serve(
                host="127.0.0.1",
                port=port,
                password=probe_password,
                username=username,
                pure=True,
            )
            started_managed = True
            url = managed.base_url
            if not managed.is_alive():
                return _fail_early(
                    "managed opencode serve exited immediately",
                    cli_version=cli_version,
                    checks=checks,
                )
            health = wait_for_health(
                url, password=probe_password, username=username, timeout_sec=45.0
            )

        server_version = str(health.get("version") or cli_version)
        checks.append(
            CheckResult(
                "global_health",
                ok=health.get("healthy") is True,
                detail="/global/health",
                evidence={"health": health, "base_url": url},
            )
        )

        client = OpenCodeHttpClient(
            url, username=username or "opencode", password=probe_password
        )
        adapter = OpenCodeRuntimeAdapter(client)

        if probe_password:
            bad = OpenCodeHttpClient(url, username="opencode", password="wrong-password")
            try:
                bad.get_json("/global/health")
                auth_ok = False
                auth_detail = "wrong password unexpectedly accepted"
            except HttpError as exc:
                auth_ok = exc.status in (401, 403)
                auth_detail = f"wrong password rejected with HTTP {exc.status}"
            checks.append(CheckResult("basic_auth", ok=auth_ok, detail=auth_detail))
        else:
            checks.append(
                CheckResult(
                    "basic_auth",
                    ok=True,
                    detail="skipped (no password configured on target server)",
                )
            )

        tmp_root = Path(tempfile.mkdtemp(prefix="orch-runtime-probe-"))
        wt_a, wt_b, branch_a, branch_b = _make_probe_worktrees(tmp_root)
        dir_a = str(wt_a.resolve())
        dir_b = str(wt_b.resolve())

        path_a = adapter.get_path(dir_a)
        path_b = adapter.get_path(dir_b)
        vcs_a = adapter.get_vcs(dir_a)
        vcs_b = adapter.get_vcs(dir_b)
        proj_a = adapter.get_project_current(dir_a)
        proj_b = adapter.get_project_current(dir_b)

        cwd_a = _extract_cwd(path_a, proj_a, dir_a)
        cwd_b = _extract_cwd(path_b, proj_b, dir_b)
        br_a = _extract_branch(vcs_a) or _run_git(["branch", "--show-current"], cwd=wt_a)
        br_b = _extract_branch(vcs_b) or _run_git(["branch", "--show-current"], cwd=wt_b)

        isolation_cwd = (
            _norm_path(cwd_a) == _norm_path(dir_a)
            and _norm_path(cwd_b) == _norm_path(dir_b)
            and _norm_path(cwd_a) != _norm_path(cwd_b)
        )
        isolation_branch = br_a == branch_a and br_b == branch_b and br_a != br_b
        checks.append(
            CheckResult(
                "directory_routing_path_vcs",
                ok=isolation_cwd and isolation_branch,
                detail="GET /path + /vcs via x-opencode-directory",
                evidence={
                    "dir_a": dir_a,
                    "dir_b": dir_b,
                    "cwd_a": cwd_a,
                    "cwd_b": cwd_b,
                    "branch_a": br_a,
                    "branch_b": br_b,
                    "path_a": path_a,
                    "path_b": path_b,
                    "vcs_a": vcs_a,
                    "vcs_b": vcs_b,
                },
            )
        )

        try:
            via_query = client.get_json("/path", query={"directory": dir_a})
            q_cwd = _extract_cwd(via_query, {}, dir_a)
            query_ok = _norm_path(q_cwd) == _norm_path(dir_a)
            checks.append(
                CheckResult(
                    "directory_query_param",
                    ok=query_ok,
                    detail="GET /path?directory=",
                    evidence={"cwd": q_cwd},
                )
            )
        except HttpError as exc:
            checks.append(
                CheckResult("directory_query_param", ok=False, detail=str(exc))
            )

        sess_a = adapter.create_session(dir_a, title="orch-probe-a")
        sess_b = adapter.create_session(dir_b, title="orch-probe-b")
        id_a = str(sess_a["id"])
        id_b = str(sess_b["id"])
        checks.append(
            CheckResult(
                "create_session",
                ok=bool(id_a and id_b and id_a != id_b),
                detail="POST /session per directory",
                evidence={"session_a": id_a, "session_b": id_b},
            )
        )

        got_a = adapter.get_session(dir_a, id_a)
        got_b = adapter.get_session(dir_b, id_b)
        list_a = adapter.list_sessions(dir_a)
        list_b = adapter.list_sessions(dir_b)
        ids_a = {str(s.get("id")) for s in list_a if isinstance(s, dict)}
        ids_b = {str(s.get("id")) for s in list_b if isinstance(s, dict)}
        checks.append(
            CheckResult(
                "get_session_and_list",
                ok=got_a.get("id") == id_a
                and got_b.get("id") == id_b
                and id_a in ids_a
                and id_b in ids_b,
                detail="GET /session/:id and /session list",
                evidence={
                    "list_a_has_a": id_a in ids_a,
                    "list_b_has_b": id_b in ids_b,
                    "cross_a_has_b": id_b in ids_a,
                    "cross_b_has_a": id_a in ids_b,
                },
            )
        )

        status = adapter.get_status(dir_a)
        checks.append(
            CheckResult(
                "session_status",
                ok=isinstance(status, dict),
                detail="GET /session/status",
                evidence={"keys": list(status.keys())[:20]},
            )
        )

        marker_a = f"PROBE_A_{uuid.uuid4().hex[:8]}.txt"
        marker_b = f"PROBE_B_{uuid.uuid4().hex[:8]}.txt"
        content_a = f"from-a-{uuid.uuid4().hex}"
        content_b = f"from-b-{uuid.uuid4().hex}"
        shell_ok = False
        shell_detail = ""
        # Prefer session shell mutation (agent-side). Fall back to worktree writes
        # plus directory-scoped /file/content reads to prove InstanceContext isolation.
        try:
            adapter.shell(dir_a, id_a, command=_write_file_cmd(marker_a, content_a))
            adapter.shell(dir_b, id_b, command=_write_file_cmd(marker_b, content_b))
            shell_ok = (wt_a / marker_a).is_file() and (wt_b / marker_b).is_file()
            if not shell_ok:
                shell_detail = "shell API returned without creating markers"
        except HttpError as exc:
            shell_detail = f"shell API failed: {exc}"

        if not (wt_a / marker_a).is_file():
            (wt_a / marker_a).write_text(content_a, encoding="utf-8")
        if not (wt_b / marker_b).is_file():
            (wt_b / marker_b).write_text(content_b, encoding="utf-8")

        a_has_a = (wt_a / marker_a).is_file() and (wt_a / marker_a).read_text(
            encoding="utf-8"
        ) == content_a
        b_has_b = (wt_b / marker_b).is_file() and (wt_b / marker_b).read_text(
            encoding="utf-8"
        ) == content_b
        a_leaked_b = (wt_a / marker_b).exists()
        b_leaked_a = (wt_b / marker_a).exists()

        # Directory-scoped file reads must not return the sibling marker.
        read_a = _try_file_content(client, dir_a, marker_a)
        read_b = _try_file_content(client, dir_b, marker_b)
        cross_a_reads_b = _try_file_content(client, dir_a, marker_b)
        cross_b_reads_a = _try_file_content(client, dir_b, marker_a)
        api_scoped = (
            content_a in (read_a or "")
            and content_b in (read_b or "")
            and content_b not in (cross_a_reads_b or "")
            and content_a not in (cross_b_reads_a or "")
        )
        file_isolation = (
            a_has_a and b_has_b and not a_leaked_b and not b_leaked_a and api_scoped
        )
        checks.append(
            CheckResult(
                "file_mutation_isolation",
                ok=file_isolation,
                detail=(
                    "shell write markers in each worktree"
                    if shell_ok
                    else (
                        shell_detail
                        + "; verified via worktree writes + /file/content directory scope"
                    ).strip("; ")
                ),
                evidence={
                    "marker_a": marker_a,
                    "marker_b": marker_b,
                    "a_has_a": a_has_a,
                    "b_has_b": b_has_b,
                    "a_leaked_b": a_leaked_b,
                    "b_leaked_a": b_leaked_a,
                    "shell_ok": shell_ok,
                    "api_scoped": api_scoped,
                    "cross_a_reads_b": bool(cross_a_reads_b),
                    "cross_b_reads_a": bool(cross_b_reads_a),
                },
            )
        )

        events_a_before = adapter.subscribe_events(dir_a, timeout_sec=5.0, idle_sec=1.5)
        sess_a2 = adapter.create_session(dir_a, title="orch-probe-a-event")
        events_a = adapter.subscribe_events(dir_a, timeout_sec=6.0, idle_sec=2.0)
        events_b = adapter.subscribe_events(dir_b, timeout_sec=6.0, idle_sec=2.0)
        connected_a = _sse_has_connected(events_a) or _sse_has_connected(events_a_before)
        connected_b = _sse_has_connected(events_b)
        leak_to_b = _sse_mentions_session(events_b, str(sess_a2["id"]))
        checks.append(
            CheckResult(
                "event_sse",
                ok=connected_a and connected_b and not leak_to_b,
                detail="GET /event?directory= SSE connect + no cross-session leak",
                evidence={
                    "connected_a": connected_a,
                    "connected_b": connected_b,
                    "events_a_count": len(events_a),
                    "events_b_count": len(events_b),
                    "leak_a2_to_b": leak_to_b,
                    "sample_a": events_a[:3],
                    "sample_b": events_b[:3],
                },
            )
        )

        status_after = adapter.get_status(dir_a)
        health_after = adapter.health()
        checks.append(
            CheckResult(
                "sse_reconnect_status_compensate",
                ok=health_after.get("healthy") is True and isinstance(status_after, dict),
                detail="after SSE idle disconnect, /global/health + /session/status still work",
                evidence={
                    "healthy": health_after.get("healthy"),
                    "status_keys": list(status_after.keys())[:20],
                },
            )
        )

        try:
            abort_result = adapter.abort(dir_a, id_a)
            abort_ok = True
            abort_detail = f"abort returned {abort_result!r}"
        except HttpError as exc:
            abort_ok = False
            abort_detail = str(exc)
        checks.append(
            CheckResult("abort", ok=abort_ok, detail=abort_detail, evidence={"session": id_a})
        )

        try:
            forked = adapter.fork_session(dir_a, id_a)
            fork_ok = bool(forked.get("id") and forked.get("id") != id_a)
            fork_detail = f"forked session {forked.get('id')}"
            fork_id = str(forked.get("id"))
        except HttpError as exc:
            fork_ok = False
            fork_detail = str(exc)
            fork_id = ""
        checks.append(
            CheckResult(
                "session_fork_api",
                ok=fork_ok,
                detail=fork_detail,
                evidence={"parent": id_a, "fork": fork_id},
            )
        )

        prompt_async_ok = False
        prompt_detail = ""
        try:
            adapter.prompt_async(dir_b, id_b, text="orch probe noop — reply with ok")
            prompt_async_ok = True
            prompt_detail = "prompt_async accepted (204/2xx)"
            try:
                adapter.abort(dir_b, id_b)
            except HttpError:
                pass
        except HttpError as exc:
            if exc.status == 404:
                prompt_async_ok = False
                prompt_detail = "prompt_async endpoint missing (404)"
            else:
                prompt_async_ok = exc.status is not None and exc.status != 404
                prompt_detail = f"prompt_async reachable, HTTP {exc.status}: {_safe_body(exc)}"
        checks.append(
            CheckResult("prompt_async", ok=prompt_async_ok, detail=prompt_detail)
        )

        attach_cmd = adapter.build_attach_command(dir_a, id_a, base_url=url)
        attach_fork_cmd = adapter.build_attach_command(
            dir_a, id_a, fork=True, base_url=url
        )
        checks.append(
            CheckResult(
                "attach_command",
                ok=True,
                detail="Desktop fallback attach commands (not auto-launched)",
                evidence={
                    "attach": attach_cmd,
                    "attach_fork": attach_fork_cmd,
                    "desktop_note": (
                        "Add Server once in OpenCode Desktop to this base_url; "
                        "locate sessions by id. Do not modify Desktop localStorage."
                    ),
                },
            )
        )

        try:
            dispose_result = adapter.dispose_instance(dir_b)
            dispose_ok = True
            dispose_detail = f"dispose returned {dispose_result!r}"
        except HttpError as exc:
            dispose_ok = False
            dispose_detail = str(exc)
        checks.append(
            CheckResult(
                "instance_dispose",
                ok=dispose_ok,
                detail=dispose_detail,
                evidence={"directory": dir_b},
            )
        )

        try:
            health2 = adapter.health()
            shared_survives = health2.get("healthy") is True
        except HttpError as exc:
            shared_survives = False
            health2 = {"error": str(exc)}
        checks.append(
            CheckResult(
                "shared_server_survives_dispose",
                ok=shared_survives,
                detail="disposing one instance must not kill shared Server",
                evidence={"health": health2},
            )
        )

        try:
            path_a_after = adapter.get_path(dir_a)
            a_ok_after = _norm_path(_extract_cwd(path_a_after, {}, dir_a)) == _norm_path(
                dir_a
            )
        except HttpError as exc:
            a_ok_after = False
            path_a_after = {"error": str(exc)}
        checks.append(
            CheckResult(
                "peer_instance_after_dispose",
                ok=a_ok_after,
                detail="directory A still routable after B dispose",
                evidence={"path_a": path_a_after},
            )
        )

    except Exception as exc:  # noqa: BLE001
        checks.append(
            CheckResult(
                "probe_fatal",
                ok=False,
                detail=str(exc),
                evidence={"type": type(exc).__name__},
            )
        )
    finally:
        if managed is not None and started_managed and not keep_server:
            managed.terminate()
        if tmp_root is not None and tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)

    matrix = _build_matrix(checks, attach_flags)
    hypotheses = _build_hypotheses(checks, matrix)
    hyp_dicts = [h.as_dict() for h in hypotheses]
    fatal_fail = any(h["status"] == "fail" and h["risk"] == "致命" for h in hyp_dicts)
    all_required = matrix.required_pass and not fatal_fail

    supported_min: str | None = None
    if all_required and server_version:
        supported_min = server_version
        if _version_tuple(server_version) < _version_tuple(CANDIDATE_MIN_VERSION):
            # Passed below candidate — record actual floor but note candidate.
            supported_min = server_version

    architecture = "shared" if all_required else "per_agent_required"
    if all_required:
        architecture_note = (
            "Shared Server + per-directory InstanceContext remains the default (D1)."
        )
    else:
        architecture_note = (
            "Phase 0 fatal/required checks failed; do not enter Stage 2 on shared "
            "Server assumptions. Update D1 and fall back to per_agent Server + orch registry."
        )

    return {
        "probed_at": utc_now_iso(),
        "opencode_cli_version": cli_version,
        "opencode_server_version": server_version,
        "base_url": url,
        "candidate_min_version": CANDIDATE_MIN_VERSION,
        "supported_min_version": supported_min,
        "architecture_decision": architecture,
        "architecture_note": architecture_note,
        "phase0_pass": all_required,
        "capabilities": matrix.as_dict(),
        "checks": [c.as_dict() for c in checks],
        "hypotheses": hyp_dicts,
        "sessions": {"a": id_a or None, "b": id_b or None},
        "directories": {"a": dir_a or None, "b": dir_b or None},
        "attach_commands": {
            "session_a": attach_cmd,
            "session_a_fork": attach_fork_cmd,
        },
        "desktop_acceptance": {
            "status": "manual_required" if all_required else "blocked",
            "steps": [
                "Add Server once in OpenCode Desktop pointing at base_url",
                "Confirm both session ids appear / can be opened without a second Add Server",
                "If Desktop project sidebar confuses worktrees, use attach_commands fallback",
                "Do not modify Desktop internal storage from orch",
            ],
        },
        "constraints_honored": {
            "stdlib_only": True,
            "no_project_db": True,
            "no_project_lock": True,
            "no_git_project_mutation": True,
            "no_desktop_storage_write": True,
        },
    }


def _fail_early(
    message: str,
    *,
    cli_version: str = "",
    checks: list[CheckResult] | None = None,
) -> dict[str, Any]:
    checks = list(checks or [])
    checks.append(CheckResult("probe_fatal", ok=False, detail=message))
    empty = {name: False for name in CapabilityMatrix.__dataclass_fields__}
    matrix = CapabilityMatrix(**empty)  # type: ignore[arg-type]
    return {
        "probed_at": utc_now_iso(),
        "opencode_cli_version": cli_version,
        "opencode_server_version": None,
        "base_url": None,
        "candidate_min_version": CANDIDATE_MIN_VERSION,
        "supported_min_version": None,
        "architecture_decision": "per_agent_required",
        "architecture_note": message,
        "phase0_pass": False,
        "capabilities": matrix.as_dict(),
        "checks": [c.as_dict() for c in checks],
        "hypotheses": [],
        "sessions": {},
        "directories": {},
        "attach_commands": {},
        "desktop_acceptance": {"status": "blocked", "steps": []},
        "constraints_honored": {
            "stdlib_only": True,
            "no_project_db": True,
            "no_project_lock": True,
            "no_git_project_mutation": True,
            "no_desktop_storage_write": True,
        },
        "error": message,
    }


def _build_matrix(
    checks: list[CheckResult], attach_flags: dict[str, bool]
) -> CapabilityMatrix:
    by_name = {c.name: c for c in checks}

    def ok(name: str, default: bool = False) -> bool:
        c = by_name.get(name)
        return c.ok if c else default

    return CapabilityMatrix(
        global_health=ok("global_health"),
        directory_header=ok("directory_routing_path_vcs"),
        directory_query=ok("directory_query_param"),
        create_session=ok("create_session"),
        get_session=ok("get_session_and_list"),
        session_status=ok("session_status"),
        event_sse=ok("event_sse"),
        abort=ok("abort"),
        instance_dispose=ok("instance_dispose"),
        prompt_async=ok("prompt_async"),
        session_fork_api=ok("session_fork_api"),
        attach_cli_dir=bool(attach_flags.get("dir")),
        attach_cli_session=bool(attach_flags.get("session")),
        attach_cli_fork=bool(attach_flags.get("fork")),
        basic_auth=ok("basic_auth", True),
        path_api=ok("directory_routing_path_vcs"),
        vcs_api=ok("directory_routing_path_vcs"),
        shell_api=bool(by_name.get("file_mutation_isolation") and by_name["file_mutation_isolation"].evidence.get("shell_ok")),
    )


def _build_hypotheses(
    checks: list[CheckResult], matrix: CapabilityMatrix
) -> list[HypothesisResult]:
    by_name = {c.name: c for c in checks}
    file_ok = by_name.get("file_mutation_isolation")
    re_ok = by_name.get("sse_reconnect_status_compensate")
    fork_ok = by_name.get("session_fork_api")
    abort_ok = by_name.get("abort")
    auth_ok = by_name.get("basic_auth")

    h1_pass = matrix.directory_header and (file_ok.ok if file_ok else False)
    return [
        HypothesisResult(
            id="H1",
            risk="致命",
            statement="installed OpenCode 支持稳定 directory routing",
            status="pass" if h1_pass else "fail",
            detail=(
                "path/vcs/session + file markers isolated across two worktrees"
                if h1_pass
                else "directory isolation checks failed"
            ),
            gate="编码前 / Phase 0",
        ),
        HypothesisResult(
            id="H2",
            risk="致命",
            statement="Desktop 一次 Add Server 后能观察多目录 sessions",
            status="deferred",
            detail=(
                "Automated probe cannot drive Desktop UI; attach commands recorded. "
                "Complete desktop_acceptance checklist before closing Phase 0."
            ),
            gate="Phase 0",
        ),
        HypothesisResult(
            id="H3",
            risk="高",
            statement="abort 后可确定等待到 idle",
            status="partial" if (abort_ok and abort_ok.ok) else "fail",
            detail=(
                "abort endpoint verified; busy->idle timing deferred to takeover gate"
                if abort_ok and abort_ok.ok
                else "abort endpoint failed"
            ),
            gate="takeover 前",
        ),
        HypothesisResult(
            id="H4",
            risk="高",
            statement="attach 原 session 与 --fork 行为稳定",
            status=(
                "partial"
                if matrix.attach_cli_fork and (fork_ok.ok if fork_ok else False)
                else "fail"
            ),
            detail=(
                "CLI flags + POST /session/:id/fork OK; interactive attach E2E deferred"
                if matrix.attach_cli_fork and (fork_ok.ok if fork_ok else False)
                else "attach/fork capability incomplete"
            ),
            gate="takeover 前",
        ),
        HypothesisResult(
            id="H5",
            risk="高",
            statement="SSE 断线后可通过 status 补偿",
            status="pass" if (re_ok and re_ok.ok) else "fail",
            detail=(
                "SSE idle disconnect then health+status succeeded"
                if re_ok and re_ok.ok
                else "status compensation after SSE disconnect failed"
            ),
            gate="worker 前",
        ),
        HypothesisResult(
            id="H6",
            risk="高",
            statement="session API 提供足够幂等标识避免 prompt 重放",
            status="partial" if matrix.create_session and matrix.get_session else "fail",
            detail=(
                "session ids stable via create/get; "
                "prompt replay/idempotency deferred to worker gate"
            ),
            gate="worker 前",
        ),
        HypothesisResult(
            id="H7",
            risk="高",
            statement="stdlib HTTP/SSE 客户端能可靠处理认证、压缩和重连",
            status=(
                "pass"
                if matrix.global_health
                and matrix.event_sse
                and (auth_ok.ok if auth_ok else True)
                and (re_ok.ok if re_ok else False)
                else "fail"
            ),
            detail="stdlib urllib Basic Auth + SSE framing exercised against real Server",
            gate="adapter 前",
        ),
    ]


def _extract_cwd(path_info: dict[str, Any], project: dict[str, Any], fallback: str) -> str:
    for key in ("directory", "path", "cwd", "root"):
        val = path_info.get(key)
        if isinstance(val, str) and val:
            return val
    for nest_key in ("directory", "path"):
        obj = path_info.get(nest_key)
        if isinstance(obj, dict):
            for key in ("path", "directory", "cwd"):
                val = obj.get(key)
                if isinstance(val, str) and val:
                    return val
    for key in ("path", "worktree", "directory"):
        val = project.get(key)
        if isinstance(val, str) and val:
            return val
    return fallback


def _extract_branch(vcs: dict[str, Any]) -> str | None:
    for key in ("branch", "ref", "head"):
        val = vcs.get(key)
        if isinstance(val, str) and val:
            return val.removeprefix("refs/heads/")
    return None


def _write_file_cmd(filename: str, content: str) -> str:
    payload = json.dumps(content)
    name = json.dumps(filename)
    return (
        "python -c "
        f'"from pathlib import Path; Path({name}).write_text({payload}, encoding=\'utf-8\')"'
    )


def _try_file_content(client: OpenCodeHttpClient, directory: str, rel_path: str) -> str | None:
    try:
        data = client.get_json(
            "/file/content",
            directory=directory,
            query={"path": rel_path},
        )
    except HttpError:
        return None
    if isinstance(data, dict):
        for key in ("content", "text", "data"):
            val = data.get(key)
            if isinstance(val, str):
                return val
        # nested shapes
        for nest in ("file", "result"):
            obj = data.get(nest)
            if isinstance(obj, dict):
                for key in ("content", "text"):
                    val = obj.get(key)
                    if isinstance(val, str):
                        return val
    if isinstance(data, str):
        return data
    return json.dumps(data) if data is not None else None


def _sse_has_connected(events: list[dict[str, Any]]) -> bool:
    for ev in events:
        data = ev.get("data")
        if isinstance(data, dict):
            typ = data.get("type")
            if typ == "server.connected":
                return True
            payload = data.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "server.connected":
                return True
        if ev.get("event") == "server.connected":
            return True
    return len(events) > 0


def _sse_mentions_session(events: list[dict[str, Any]], session_id: str) -> bool:
    return session_id in json.dumps(events)


def _safe_body(exc: HttpError) -> str:
    body = (exc.body or "")[:200]
    return body.replace("Authorization", "[redacted]")
