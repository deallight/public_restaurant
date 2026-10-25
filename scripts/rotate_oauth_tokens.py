from __future__ import annotations

import argparse
import json

from app.config import load_settings
from app.database import Database
from app.oauth_security import OAuthTokenCipher
from app.services import AppError, RestaurantService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify or rotate stored OAuth token ciphertexts to the primary Fernet key."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite non-primary ciphertexts after verifying every ciphertext is readable",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not settings.oauth_token_encryption_keys:
        raise SystemExit("OAUTH_TOKEN_ENCRYPTION_KEYS is required")
    database = Database(settings.database_url)
    database.prepare()
    service = RestaurantService(
        database,
        oauth_token_cipher=OAuthTokenCipher(settings.oauth_token_encryption_keys),
    )
    try:
        result = (
            service.rotate_oauth_token_ciphertexts()
            if args.apply
            else service.oauth_token_encryption_status()
        )
    except AppError as exc:
        raise SystemExit(exc.message) from exc
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "verify",
                **result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result["unreadable"] or (not args.apply and result["non_primary"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
