"""Control lease helpers (V12-009). Token plaintext never stored in DB/audit."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from orch.errors import DbError, ValidationError
from orch.util import utc_now_iso


def generate_lease_token() -> str:
    """At least 256-bit CSPRNG token (urlsafe ~256+ bits)."""
    return secrets.token_urlsafe(32)


def hash_lease_token(run_id: str, generation: int, token: str) -> str:
    material = f"{run_id}|{generation}|{token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def verify_lease_token(
    run_id: str, generation: int, token: str, token_hash: str
) -> bool:
    expected = hash_lease_token(run_id, generation, token)
    return hmac.compare_digest(expected, token_hash)


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def acquire_lease(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    controller: str,
    generation: int,
    ttl_sec: int = 3600,
) -> str:
    """
    Create/replace lease for run. Returns plaintext token (caller must not log it).
    """
    if controller not in {"agent", "human"}:
        raise ValidationError(
            f"invalid lease controller: {controller}",
            kind="invalid_lease_controller",
            details={"controller": controller},
        )
    token = generate_lease_token()
    token_hash = hash_lease_token(run_id, generation, token)
    now = utc_now_iso()
    expires = (
        datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn.execute("DELETE FROM control_leases WHERE run_id = ?", (run_id,))
        conn.execute(
            """
            INSERT INTO control_leases(
              run_id, controller, generation, token_hash,
              acquired_at, renewed_at, expires_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (run_id, controller, generation, token_hash, now, now, expires),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise DbError(f"acquire lease failed: {exc}", details={"error": str(exc)}) from exc
    return token


def renew_lease(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    generation: int,
    token: str,
    ttl_sec: int = 3600,
) -> None:
    row = get_lease(conn, run_id)
    if row is None:
        raise ValidationError("lease not found", kind="lease_not_found")
    if int(row["generation"]) != int(generation):
        raise ValidationError(
            "lease generation mismatch",
            kind="lease_generation_mismatch",
            details={"expected": row["generation"], "got": generation},
        )
    if not verify_lease_token(run_id, generation, token, row["token_hash"]):
        raise ValidationError("lease token invalid", kind="lease_token_invalid")
    if lease_expired(row):
        raise ValidationError("lease expired", kind="lease_expired")
    now = utc_now_iso()
    expires = (
        datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn.execute(
            """
            UPDATE control_leases
            SET renewed_at = ?, expires_at = ?
            WHERE run_id = ?
            """,
            (now, expires, run_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise DbError(f"renew lease failed: {exc}", details={"error": str(exc)}) from exc


def release_lease(conn: sqlite3.Connection, *, run_id: str) -> None:
    try:
        conn.execute("DELETE FROM control_leases WHERE run_id = ?", (run_id,))
        conn.commit()
    except sqlite3.Error as exc:
        raise DbError(f"release lease failed: {exc}", details={"error": str(exc)}) from exc


def get_lease(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM control_leases WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def lease_expired(row: dict[str, Any], *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    try:
        exp = _parse_iso(str(row["expires_at"]))
    except ValueError:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return now >= exp


def assert_write_allowed(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    generation: int,
    token: str,
    controller: str = "agent",
) -> dict[str, Any]:
    """Validate lease for write path (prompt/abort). Raises on failure."""
    row = get_lease(conn, run_id)
    if row is None:
        raise ValidationError("no active lease", kind="lease_not_found")
    if row.get("controller") != controller:
        raise ValidationError(
            "lease controller mismatch",
            kind="lease_controller_mismatch",
            details={"expected": controller, "got": row.get("controller")},
        )
    if int(row["generation"]) != int(generation):
        raise ValidationError(
            "stale generation",
            kind="lease_generation_mismatch",
            details={"expected": row["generation"], "got": generation},
        )
    if lease_expired(row):
        raise ValidationError("lease expired", kind="lease_expired")
    if not verify_lease_token(run_id, generation, token, row["token_hash"]):
        raise ValidationError("lease token invalid", kind="lease_token_invalid")
    return row
