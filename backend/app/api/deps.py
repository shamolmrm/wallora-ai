import re
from typing import Annotated

from fastapi import Depends, Header, Request
from google.cloud.firestore_v1 import AsyncTransaction

from app.container import Container
from app.core.errors import ProblemError
from app.core.ids import new_id
from app.db.documents import UserDoc, UserStatus
from app.db.firestore import FIREBASE_UIDS, USERS, run_transaction, tx_get
from app.integrations.auth import AuthError, AuthServiceUnavailableError, VerifiedIdentity

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def _provision_user(container: Container, identity: VerifiedIdentity) -> UserDoc:
    """Find or create the user for a Firebase UID (one mapping document per UID)."""
    db = container.db
    mapping_ref = db.collection(FIREBASE_UIDS).document(identity.uid)
    mapping = await mapping_ref.get()
    if mapping.exists:
        user_snap = await db.collection(USERS).document(str(mapping.get("user_id"))).get()
        user = UserDoc.from_doc(user_snap.to_dict() or {})
        if (user.email, user.email_verified) != (identity.email, identity.email_verified):
            await user_snap.reference.update(
                {"email": identity.email, "email_verified": identity.email_verified}
            )
            user.email, user.email_verified = identity.email, identity.email_verified
        return user

    async def create(txn: AsyncTransaction) -> UserDoc:
        existing = await tx_get(txn, mapping_ref)
        if existing.exists:  # created concurrently by another request
            user_snap = await tx_get(txn, db.collection(USERS).document(existing.get("user_id")))
            return UserDoc.from_doc(user_snap.to_dict() or {})
        now = container.clock()
        user = UserDoc(
            id=str(new_id()),
            firebase_uid=identity.uid,
            email=identity.email,
            email_verified=identity.email_verified,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        txn.create(mapping_ref, {"user_id": user.id, "created_at": now})
        txn.create(db.collection(USERS).document(user.id), user.to_doc())
        return user

    return await run_transaction(db, create)


async def get_current_user(
    container: ContainerDep,
    authorization: Annotated[str | None, Header()] = None,
) -> UserDoc:
    if not authorization or not authorization.startswith("Bearer "):
        raise ProblemError(
            401,
            "unauthenticated",
            "Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        identity = await container.token_verifier.verify(token)
    except AuthError as exc:
        raise ProblemError(
            401,
            exc.code,
            "Your session is invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except AuthServiceUnavailableError:
        raise ProblemError(503, "auth_unavailable", "Sign-in is temporarily unavailable.") from None
    if identity.sign_in_provider == "anonymous":
        raise ProblemError(403, "account_required", "Create an account to use this feature.")

    user = await _provision_user(container, identity)
    if user.status != UserStatus.ACTIVE:
        raise ProblemError(403, "account_inactive", "This account is not active.")
    return user


CurrentUser = Annotated[UserDoc, Depends(get_current_user)]


def idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if key is None:
        raise ProblemError(
            400, "idempotency_key_required", "An Idempotency-Key header is required."
        )
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ProblemError(
            400, "idempotency_key_invalid", "Idempotency-Key must be 8-128 URL-safe characters."
        )
    return key


IdempotencyKey = Annotated[str, Depends(idempotency_key)]
