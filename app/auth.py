from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass


SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class SessionClaims:
    user_id: int
    issued_at: int
    expires_at: int
    nonce: str


@dataclass(frozen=True)
class SessionCodec:
    """Issue and verify compact, server-signed browser sessions."""

    secret: str
    max_age_seconds: int = SESSION_MAX_AGE_SECONDS

    def issue(self, user_id: int, now: int | None = None) -> str:
        if not self.secret:
            raise ValueError("session signing secret is missing")
        issued_at = int(time.time() if now is None else now)
        payload = {
            "exp": issued_at + self.max_age_seconds,
            "iat": issued_at,
            "nonce": secrets.token_urlsafe(12),
            "uid": int(user_id),
            "v": 1,
        }
        encoded = _base64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = _base64url_encode(
            hmac.new(
                self._signing_key(),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return f"{encoded}.{signature}"

    def verify(self, token: str, now: int | None = None) -> int | None:
        claims = self.verify_claims(token, now=now)
        return claims.user_id if claims is not None else None

    def verify_claims(
        self,
        token: str,
        now: int | None = None,
    ) -> SessionClaims | None:
        if not token or not self.secret:
            return None
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = _base64url_encode(
                hmac.new(self._signing_key(), encoded.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return None
            payload = json.loads(_base64url_decode(encoded).decode("utf-8"))
            checked_at = int(time.time() if now is None else now)
            issued_at = int(payload.get("iat", 0))
            expires_at = int(payload.get("exp", 0))
            nonce = str(payload.get("nonce", ""))
            if (
                payload.get("v") != 1
                or issued_at <= 0
                or expires_at <= checked_at
                or expires_at <= issued_at
                or not nonce
            ):
                return None
            user_id = int(payload.get("uid", 0))
            if user_id <= 0:
                return None
            return SessionClaims(
                user_id=user_id,
                issued_at=issued_at,
                expires_at=expires_at,
                nonce=nonce,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def issue_action_token(self, session_token: str, action: str) -> str:
        """Bind a CSRF token to one signed browser session and action."""
        if self.verify(session_token) is None or not action:
            raise ValueError("valid session and action are required")
        return _base64url_encode(
            hmac.new(
                self._signing_key(),
                f"{action}\0{session_token}".encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )

    def verify_action_token(
        self,
        session_token: str,
        action: str,
        supplied_token: str,
    ) -> bool:
        if not supplied_token:
            return False
        try:
            expected_token = self.issue_action_token(session_token, action)
        except ValueError:
            return False
        return hmac.compare_digest(supplied_token, expected_token)

    def _signing_key(self) -> bytes:
        return hmac.new(
            self.secret.encode("utf-8"),
            b"public-restaurant/session-signing/v1",
            hashlib.sha256,
        ).digest()
