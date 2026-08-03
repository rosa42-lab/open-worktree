"""V13-009：GitHub provider 合约测试（§18.3）；无网络。"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any
from unittest.mock import patch

from orch.remote.auth import GitHubCredentials, ResolvedGitHubAuth, resolve_github_auth
from orch.remote.factory import get_hosting_provider
from orch.remote.fields import KIND_MERGE_NOT_SYNCABLE
from orch.remote.github import GitHubProviderAdapter
from orch.remote.http import (
    KIND_AUTH_FAILED,
    KIND_FORBIDDEN,
    KIND_NOT_FOUND,
    KIND_RATE_LIMITED,
    KIND_SERVER_ERROR,
    KIND_TIMEOUT,
    GitHubHttpClient,
    ProviderHttpError,
    redact_secrets,
)
from orch.remote.manual import ManualProviderAdapter


class _FakeResp:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj).encode()


class TestHttpRedaction(unittest.TestCase):
    def test_redact_bearer_and_ghs(self) -> None:
        text = "Authorization: Bearer ghs_SECRETtoken123 and ghp_ABCDEFG"
        out = redact_secrets(text)
        self.assertNotIn("ghs_SECRETtoken123", out)
        self.assertNotIn("ghp_ABCDEFG", out)
        self.assertIn("***", out)

    def test_error_details_strip_token(self) -> None:
        err = ProviderHttpError(
            "Bearer ghs_LEAKED should vanish",
            kind=KIND_AUTH_FAILED,
            status=401,
            details={"authorization": "Bearer ghs_LEAKED", "body": "token=ghs_LEAKED"},
        )
        self.assertNotIn("ghs_LEAKED", str(err))
        self.assertNotIn("ghs_LEAKED", repr(err))
        self.assertEqual(err.details.get("authorization"), "***")
        self.assertNotIn("ghs_LEAKED", json.dumps(err.details))


class TestHttpStatusMapping(unittest.TestCase):
    def _client_raising(self, status: int) -> GitHubHttpClient:
        def urlopen(req: Any, timeout: float = 0) -> Any:
            raise urllib.error.HTTPError(
                url="https://api.github.com/x",
                code=status,
                msg="err",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"message":"nope","token":"ghs_HIDDEN"}'),
            )

        return GitHubHttpClient("tok", urlopen=urlopen)

    def test_401(self) -> None:
        with self.assertRaises(ProviderHttpError) as ctx:
            self._client_raising(401).get_json("/user")
        self.assertEqual(ctx.exception.kind, KIND_AUTH_FAILED)
        self.assertNotIn("ghs_HIDDEN", str(ctx.exception.details))

    def test_403(self) -> None:
        with self.assertRaises(ProviderHttpError) as ctx:
            self._client_raising(403).get_json("/user")
        self.assertEqual(ctx.exception.kind, KIND_FORBIDDEN)

    def test_404(self) -> None:
        with self.assertRaises(ProviderHttpError) as ctx:
            self._client_raising(404).get_json("/user")
        self.assertEqual(ctx.exception.kind, KIND_NOT_FOUND)

    def test_429(self) -> None:
        with self.assertRaises(ProviderHttpError) as ctx:
            self._client_raising(429).get_json("/user")
        self.assertEqual(ctx.exception.kind, KIND_RATE_LIMITED)

    def test_500(self) -> None:
        with self.assertRaises(ProviderHttpError) as ctx:
            self._client_raising(500).get_json("/user")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_timeout(self) -> None:
        def urlopen(req: Any, timeout: float = 0) -> Any:
            raise TimeoutError("timed out")

        client = GitHubHttpClient("tok", urlopen=urlopen)
        with self.assertRaises(ProviderHttpError) as ctx:
            client.get_json("/user")
        self.assertEqual(ctx.exception.kind, KIND_TIMEOUT)


class _Router:
    """按 path 返回 JSON；可记录调用。"""

    def __init__(self, routes: dict[str, Any]):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def urlopen(self, req: Any, timeout: float = 0) -> _FakeResp:
        method = getattr(req, "get_method", lambda: "GET")()
        full = req.full_url
        path = full.split("api.github.com", 1)[-1] if "api.github.com" in full else full
        path = path.split("?", 1)[0]
        self.calls.append((method, path))
        if path not in self.routes:
            raise urllib.error.HTTPError(
                url=full, code=404, msg="nf", hdrs=None, fp=io.BytesIO(b"{}")  # type: ignore[arg-type]
            )
        payload = self.routes[path]
        if callable(payload):
            payload = payload(method, path)
        if isinstance(payload, tuple):
            status, body = payload
            return _FakeResp(status, _json_bytes(body) if not isinstance(body, bytes) else body)
        return _FakeResp(200, _json_bytes(payload))


class TestGitHubProviderContract(unittest.TestCase):
    def test_probe_capabilities_installation(self) -> None:
        router = _Router(
            {
                "/installation": {"id": 1, "app_id": 2, "account": {"login": "org"}},
                "/repos/acme/shop": {
                    "full_name": "acme/shop",
                    "private": True,
                    "permissions": {"push": True, "pull": True},
                },
            }
        )
        client = GitHubHttpClient("tok", urlopen=router.urlopen)
        adapter = GitHubProviderAdapter(client, repository="acme/shop")
        caps = adapter.probe_capabilities()
        self.assertTrue(caps["ok"])
        self.assertEqual(caps["identity"]["type"], "installation")
        self.assertNotIn("token", json.dumps(caps))

    def test_branch_policy_rulesets(self) -> None:
        router = _Router(
            {
                "/repos/acme/shop/rulesets": [
                    {
                        "id": 10,
                        "conditions": {"ref_name": {"include": ["refs/heads/develop"]}},
                        "bypass_actors": [],
                        "rules": [
                            {"type": "non_fast_forward"},
                            {"type": "deletion"},
                        ],
                    }
                ],
                "/repos/acme/shop": {
                    "allow_merge_commit": True,
                    "allow_squash_merge": False,
                    "allow_rebase_merge": False,
                },
            }
        )
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        pol = adapter.branch_policy("develop")
        self.assertEqual(pol["status"], "verified")
        self.assertIs(pol["allow_force"], False)
        self.assertIs(pol["allow_delete"], False)

    def test_branch_policy_forbidden_not_verified(self) -> None:
        def urlopen(req: Any, timeout: float = 0) -> Any:
            raise urllib.error.HTTPError(
                url=req.full_url, code=403, msg="x", hdrs=None, fp=io.BytesIO(b"{}")  # type: ignore[arg-type]
            )

        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=urlopen), repository="acme/shop"
        )
        pol = adapter.branch_policy("master")
        self.assertNotEqual(pol.get("status"), "verified")
        self.assertEqual(pol.get("kind"), KIND_FORBIDDEN)

    def test_create_pr_idempotent(self) -> None:
        existing = {
            "number": 7,
            "html_url": "https://github.com/acme/shop/pull/7",
            "state": "open",
            "merged": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "merge_commit_sha": None,
            "head": {"ref": "develop", "sha": "abc"},
            "base": {"ref": "master", "sha": "def"},
        }
        created = False

        def pulls_handler(method: str, path: str) -> Any:
            nonlocal created
            if method == "GET":
                return [existing]
            created = True
            return existing

        router = _Router({"/repos/acme/shop/pulls": pulls_handler})
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        pr1 = adapter.create_promotion_pr("develop", "master", "t", "b")
        pr2 = adapter.create_promotion_pr("develop", "master", "t", "b")
        self.assertEqual(pr1["external_id"], "7")
        self.assertEqual(pr2["external_id"], "7")
        self.assertFalse(created)

    def test_get_checks_binds_source_sha(self) -> None:
        router = _Router(
            {
                "/repos/acme/shop/commits/deadbeef/check-runs": {
                    "check_runs": [
                        {
                            "name": "test",
                            "conclusion": "success",
                            "status": "completed",
                            "head_sha": "deadbeef",
                        },
                        {
                            "name": "stale",
                            "conclusion": "success",
                            "status": "completed",
                            "head_sha": "other",
                        },
                    ]
                }
            }
        )
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        out = adapter.get_checks("1", "deadbeef")
        self.assertEqual(len(out["checks"]), 1)
        self.assertEqual(out["checks"][0]["name"], "test")
        self.assertTrue(out["checks"][0]["bound_to_source"])

    def test_get_reviews_bot_not_code_owner(self) -> None:
        # #9 mock：bot 审批不计入
        router = _Router(
            {
                "/repos/acme/shop/pulls/3/reviews": [
                    {
                        "user": {"login": "orch-bot[bot]", "type": "Bot"},
                        "state": "APPROVED",
                        "commit_id": "abc123",
                    },
                    {
                        "user": {"login": "alice", "type": "User"},
                        "state": "APPROVED",
                        "commit_id": "abc123",
                    },
                    {
                        "user": {"login": "bob", "type": "User"},
                        "state": "APPROVED",
                        "commit_id": "other",
                    },
                ]
            }
        )
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        out = adapter.get_reviews("3", "abc123")
        self.assertEqual(out["approved_bound_human_count"], 1)
        bot = next(r for r in out["reviews"] if r["is_bot"])
        self.assertFalse(bot["counts_as_code_owner"])

    def test_mergeable_null_bounded_retry(self) -> None:
        calls = {"n": 0}

        def pr_payload(method: str, path: str) -> Any:
            calls["n"] += 1
            mergeable = None if calls["n"] < 3 else True
            return {
                "number": 1,
                "html_url": "u",
                "state": "open",
                "merged": False,
                "mergeable": mergeable,
                "mergeable_state": "unstable" if mergeable is None else "clean",
                "merge_commit_sha": None,
                "head": {"ref": "develop", "sha": "a"},
                "base": {"ref": "master", "sha": "b"},
            }

        router = _Router({"/repos/acme/shop/pulls/1": pr_payload})
        sleeps: list[float] = []
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen),
            repository="acme/shop",
            sleep=sleeps.append,
        )
        pr = adapter.get_pr("1")
        self.assertIs(pr["mergeable"], True)
        self.assertGreaterEqual(calls["n"], 3)
        self.assertTrue(sleeps)

    def test_squash_merge_not_syncable(self) -> None:
        router = _Router(
            {
                "/repos/acme/shop/pulls/9": {
                    "number": 9,
                    "html_url": "u",
                    "state": "closed",
                    "merged": True,
                    "mergeable": None,
                    "mergeable_state": None,
                    "merge_commit_sha": "squash1",
                    "head": {"ref": "develop", "sha": "a"},
                    "base": {"ref": "master", "sha": "b"},
                },
                "/repos/acme/shop/git/commits/squash1": {
                    "parents": [{"sha": "only-one"}],
                },
            }
        )
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        pr = adapter.get_pr("9")
        self.assertEqual(pr.get("kind"), KIND_MERGE_NOT_SYNCABLE)
        self.assertEqual(pr.get("merge_method"), "squash")

    def test_closed_externally(self) -> None:
        router = _Router(
            {
                "/repos/acme/shop/pulls/2": {
                    "number": 2,
                    "html_url": "u",
                    "state": "closed",
                    "merged": False,
                    "mergeable": False,
                    "mergeable_state": "dirty",
                    "merge_commit_sha": None,
                    "head": {"ref": "feature", "sha": "moved"},
                    "base": {"ref": "master", "sha": "b"},
                }
            }
        )
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        pr = adapter.get_pr("2")
        self.assertEqual(pr["state"], "closed")
        self.assertFalse(pr["merged"])
        self.assertEqual(pr["head_sha"], "moved")

    def test_bad_head_policy_shape(self) -> None:
        # #10 mock：promotion-policy 类失败表现为 required_checks 含 promotion-policy
        # 且 head/base 校验由调用方比对领域字段
        router = _Router(
            {
                "/repos/acme/shop/rulesets": [
                    {
                        "id": 1,
                        "conditions": {"ref_name": {"include": ["refs/heads/master"]}},
                        "bypass_actors": [],
                        "rules": [
                            {"type": "pull_request"},
                            {
                                "type": "required_status_checks",
                                "parameters": {
                                    "required_status_checks": [
                                        {"context": "promotion-policy"}
                                    ]
                                },
                            },
                        ],
                    }
                ],
                "/repos/acme/shop": {
                    "allow_merge_commit": True,
                    "allow_squash_merge": False,
                    "allow_rebase_merge": False,
                },
            }
        )
        adapter = GitHubProviderAdapter(
            GitHubHttpClient("tok", urlopen=router.urlopen), repository="acme/shop"
        )
        pol = adapter.branch_policy("master")
        self.assertIn("promotion-policy", pol["required_checks"])
        self.assertTrue(pol.get("require_pr"))


class TestFactoryAndManual(unittest.TestCase):
    def test_manual_unsupported(self) -> None:
        m = ManualProviderAdapter()
        self.assertEqual(m.probe_capabilities()["kind"], "unsupported")

    def test_factory_none_without_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            # clear may remove too much; ensure no ORCH_GITHUB_*
            env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("ORCH_GITHUB")}
            with patch.dict("os.environ", env, clear=True):
                auth = resolve_github_auth()
                self.assertIsNone(auth.best_for_probe())
                provider = get_hosting_provider(
                    {
                        "provider": "github",
                        "repository": "acme/shop",
                        "api_base_url": "https://api.github.com",
                    },
                    auth=auth,
                )
                self.assertIsNone(provider)

    def test_factory_with_injected_auth(self) -> None:
        auth = ResolvedGitHubAuth(
            default=GitHubCredentials(token="t", role="default", source="env"),
            integration=None,
            release=None,
        )
        provider = get_hosting_provider(
            {
                "provider": "github",
                "repository": "acme/shop",
                "api_base_url": "https://api.github.com",
            },
            auth=auth,
        )
        self.assertIsInstance(provider, GitHubProviderAdapter)

    def test_gitlab_placeholder(self) -> None:
        p = get_hosting_provider({"provider": "gitlab", "repository": "a/b"})
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.probe_capabilities()["kind"], "unsupported")


if __name__ == "__main__":
    unittest.main()
