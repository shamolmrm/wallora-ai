"""Firebase ID-token verification.

``verify_id_token`` checks signature, audience (project), issuer and expiry
against Google's public keys (cached by the SDK). It is synchronous, so it runs
in a worker thread.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import firebase_admin
from firebase_admin import auth as firebase_auth


@dataclass(frozen=True)
class VerifiedIdentity:
    uid: str
    email: str | None
    email_verified: bool
    sign_in_provider: str | None


class AuthError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthServiceUnavailableError(Exception):
    pass


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedIdentity: ...


MAX_DEV_UID_LEN = 64


class DevTokenVerifier:
    """Local development only (see Settings.dev_auth_enabled): ``dev:<uid>`` tokens."""

    async def verify(self, token: str) -> VerifiedIdentity:
        prefix, _, uid = token.partition(":")
        if (
            prefix != "dev"
            or not uid
            or len(uid) > MAX_DEV_UID_LEN
            or not uid.replace("-", "").isalnum()
        ):
            raise AuthError("token_invalid")
        return VerifiedIdentity(f"dev-{uid}", f"{uid}@dev.local", True, "password")


class FirebaseTokenVerifier:
    def __init__(self, project_id: str | None) -> None:
        options = {"projectId": project_id} if project_id else None
        try:
            self._app = firebase_admin.get_app()
        except ValueError:
            # Uses GOOGLE_APPLICATION_CREDENTIALS (a file outside the repo) when set.
            self._app = firebase_admin.initialize_app(options=options)

    async def verify(self, token: str) -> VerifiedIdentity:
        try:
            claims = await asyncio.to_thread(firebase_auth.verify_id_token, token, self._app)
        except firebase_auth.ExpiredIdTokenError as exc:
            raise AuthError("token_expired") from exc
        except firebase_auth.RevokedIdTokenError as exc:
            raise AuthError("token_revoked") from exc
        except firebase_auth.CertificateFetchError as exc:
            raise AuthServiceUnavailableError from exc
        except (firebase_auth.InvalidIdTokenError, ValueError) as exc:
            raise AuthError("token_invalid") from exc
        firebase_claims = claims.get("firebase") or {}
        return VerifiedIdentity(
            uid=str(claims["uid"]),
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified", False)),
            sign_in_provider=firebase_claims.get("sign_in_provider"),
        )
