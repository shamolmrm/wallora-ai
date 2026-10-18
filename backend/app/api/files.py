"""Serves objects for the local/memory storage backends via HMAC-signed URLs.

Disabled (404) when storage is S3, where clients receive presigned URLs instead.
"""

from fastapi import APIRouter, Query, Response

from app.api.deps import ContainerDep
from app.core.errors import ProblemError
from app.integrations.storage import StorageError, content_type_for

router = APIRouter(tags=["files"], include_in_schema=False)


@router.get("/v1/files/{key:path}")
async def get_file(
    key: str,
    container: ContainerDep,
    exp: int = Query(...),
    sig: str = Query(..., max_length=128),
) -> Response:
    signer = container.url_signer
    if signer is None or not signer.verify(key, exp, sig):
        raise ProblemError(404, "not_found", "Not found.")
    try:
        data = await container.storage.get(key)
    except StorageError:
        raise ProblemError(404, "not_found", "Not found.") from None
    return Response(
        content=data,
        media_type=content_type_for(key),
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )
