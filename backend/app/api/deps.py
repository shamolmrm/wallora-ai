import re
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.container import Container
from app.core.errors import ProblemError
from app.core.ids import new_id
from app.integrations.auth import AuthError, AuthServiceUnavailableError
from app.modules.users.models import User, UserStatus

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_current_user(
    container: ContainerDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
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

    async with container.sessions() as session, session.begin():
        stmt = (
            pg_insert(User)
            .values(
                id=new_id(),
                firebase_uid=identity.uid,
                email=identity.email,
                email_verified=identity.email_verified,
                status=UserStatus.ACTIVE,
            )
            .on_conflict_do_update(
                index_elements=["firebase_uid"],
                set_={"email": identity.email, "email_verified": identity.email_verified},
            )
            .returning(User)
        )
        user = (await session.execute(stmt)).scalar_one()
    if user.status != UserStatus.ACTIVE:
        raise ProblemError(403, "account_inactive", "This account is not active.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


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
