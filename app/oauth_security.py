from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


NAVER_DISCONNECT_MAX_CLOCK_SKEW_SECONDS = 5 * 60


def _base64url_without_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    encoded = str(value or "").strip().encode("ascii")
    return base64.urlsafe_b64decode(encoded + (b"=" * (-len(encoded) % 4)))


class OAuthTokenCipher:
    """Encrypt OAuth credentials with a rotatable MultiFernet key ring."""

    def __init__(self, comma_separated_keys: str):
        raw_keys = [item.strip() for item in str(comma_separated_keys or "").split(",")]
        keys = [item for item in raw_keys if item]
        if not keys:
            raise ValueError("OAUTH_TOKEN_ENCRYPTION_KEYS is missing")
        try:
            fernets = [Fernet(item.encode("ascii")) for item in keys]
            self._primary_cipher = fernets[0]
            self._cipher = MultiFernet(fernets)
        except (TypeError, ValueError) as exc:
            raise ValueError("OAUTH_TOKEN_ENCRYPTION_KEYS contains an invalid Fernet key") from exc

    def encrypt(self, token: str) -> str:
        value = str(token or "")
        if not value:
            raise ValueError("OAuth token is empty")
        return self._cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_token: str) -> str:
        try:
            return self._cipher.decrypt(str(encrypted_token or "").encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise ValueError("OAuth token could not be decrypted") from exc

    def rotate(self, encrypted_token: str) -> str:
        try:
            return self._cipher.rotate(str(encrypted_token or "").encode("ascii")).decode("ascii")
        except (InvalidToken, ValueError) as exc:
            raise ValueError("OAuth token could not be rotated") from exc

    def uses_primary_key(self, encrypted_token: str) -> bool:
        try:
            self._primary_cipher.decrypt(str(encrypted_token or "").encode("ascii"))
        except (InvalidToken, ValueError):
            return False
        return True


def oauth_subject_hmac(secret: str, provider: str, provider_subject: str) -> str:
    if not secret:
        raise ValueError("OAUTH_SUBJECT_HMAC_SECRET is missing")
    if not provider_subject:
        raise ValueError("provider subject is missing")
    payload = f"{provider}\0{provider_subject}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class NaverDisconnectPayload:
    provider_subject: str
    provider_timestamp: int
    event_digest: str


def verify_naver_disconnect_payload(
    payload: Mapping[str, str],
    *,
    client_id: str,
    client_secret: str,
    subject_hmac_secret: str,
    now: int | None = None,
) -> NaverDisconnectPayload:
    supplied_client_id = str(payload.get("clientId", ""))
    encrypted_subject = str(payload.get("encryptUniqueId", ""))
    timestamp_text = str(payload.get("timestamp", ""))
    supplied_signature = str(payload.get("signature", ""))
    if not all((client_id, client_secret, supplied_client_id, encrypted_subject, timestamp_text, supplied_signature)):
        raise ValueError("required disconnect parameter is missing")
    if not hmac.compare_digest(supplied_client_id, client_id):
        raise PermissionError("disconnect client is not authorized")
    try:
        provider_timestamp = int(timestamp_text)
    except ValueError as exc:
        raise ValueError("disconnect timestamp is invalid") from exc
    checked_at = int(time.time() if now is None else now)
    if abs(checked_at - provider_timestamp) > NAVER_DISCONNECT_MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("disconnect timestamp is expired")

    signature_base = (
        f"clientId={supplied_client_id}&encryptUniqueId={encrypted_subject}"
        f"&timestamp={timestamp_text}"
    )
    protocol_key = hashlib.md5(
        client_secret.encode("utf-8"), usedforsecurity=False
    ).digest()[:16]
    expected_signature = _base64url_without_padding(
        hmac.new(protocol_key, signature_base.encode("utf-8"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise PermissionError("disconnect signature is invalid")

    try:
        encrypted = _base64url_decode(encrypted_subject)
        if len(encrypted) <= 16 or len(encrypted) % 16:
            raise ValueError("invalid encrypted subject length")
        iv, ciphertext = encrypted[:16], encrypted[16:]
        decryptor = Cipher(algorithms.AES(protocol_key), modes.CBC(iv)).decryptor()
        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        provider_subject = (
            unpadder.update(padded_plaintext) + unpadder.finalize()
        ).decode("utf-8")
    except Exception as exc:
        raise ValueError("disconnect subject is invalid") from exc
    if not provider_subject or len(provider_subject) > 255:
        raise ValueError("disconnect subject is invalid")

    subject_digest = oauth_subject_hmac(
        subject_hmac_secret,
        "naver",
        provider_subject,
    )
    event_digest = hmac.new(
        subject_hmac_secret.encode("utf-8"),
        f"naver\0{subject_digest}\0{provider_timestamp}\0{supplied_signature}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return NaverDisconnectPayload(
        provider_subject=provider_subject,
        provider_timestamp=provider_timestamp,
        event_digest=event_digest,
    )
