import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.deps import ContainerDep, CurrentUser, IdempotencyKey
from app.core.errors import ProblemError
from app.db.documents import ImageDoc, JobDoc, JobKind, JobStatus
from app.modules.studio.catalog import ASPECTS, TIERS
from app.modules.studio.prompts import STYLE_PRESETS
from app.modules.studio.schemas import (
    AspectOut,
    CapabilitiesOut,
    CreateGenerationRequest,
    DownloadOut,
    GenerationOut,
    GenerationPage,
    ImageOut,
    TierOut,
    UploadOut,
)
from app.modules.studio.service import StudioService
from app.providers.image.models import get_model, size_problem

router = APIRouter(prefix="/v1", tags=["studio"])

UPLOAD_CHUNK = 64 * 1024


async def _to_out(service: StudioService, job: JobDoc, images: list[ImageDoc]) -> GenerationOut:
    ttl = service.s.signed_url_ttl_seconds
    image_out: list[ImageOut] = []
    if job.status == JobStatus.SUCCEEDED:
        for image in images:
            url, thumb = await asyncio.gather(
                service.c.storage.signed_url(image.storage_key, ttl),
                service.c.storage.signed_url(image.thumb_key, ttl),
            )
            image_out.append(
                ImageOut(
                    id=uuid.UUID(image.id),
                    width=image.width,
                    height=image.height,
                    mime_type=image.mime_type,
                    url=url,
                    thumb_url=thumb,
                    url_expires_in=ttl,
                )
            )
    return GenerationOut(
        id=uuid.UUID(job.id),
        kind=job.kind,
        status=job.status,
        prompt=job.prompt,
        style_preset=job.style_preset,
        tier=job.tier,
        aspect=job.aspect,
        width=job.width,
        height=job.height,
        credit_cost=job.credit_cost,
        attempts=job.attempts,
        error_code=job.error_code,
        retry_of_job_id=uuid.UUID(job.retry_of_job_id) if job.retry_of_job_id else None,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        images=image_out,
    )


async def _single(service: StudioService, job: JobDoc) -> GenerationOut:
    images = await service.images_for([job.id])
    return await _to_out(service, job, images[job.id])


@router.get("/studio/capabilities", response_model=CapabilitiesOut)
async def capabilities(container: ContainerDep) -> CapabilitiesOut:
    s = container.settings
    tiers = []
    for tier in TIERS.values():
        model = get_model(tier.model)
        edit_model = get_model(tier.edit_model)
        aspects = [
            a.name
            for a in ASPECTS.values()
            if (not a.experimental or s.allow_experimental_sizes)
            and size_problem(
                model, a.width, a.height, allow_experimental=s.allow_experimental_sizes
            )
            is None
        ]
        tiers.append(
            TierOut(
                name=tier.name,
                credits=tier.credits,
                quality=tier.quality,
                premium_only=tier.premium_only,
                aspects=aspects,
                supports_edit=edit_model.supports_edit,
                max_reference_images=min(edit_model.max_input_images, s.max_reference_images),
            )
        )
    return CapabilitiesOut(
        provider=container.image_provider.name,
        generation_enabled=s.generation_enabled,
        kinds=[k.value for k in JobKind],
        tiers=tiers,
        aspects=[
            AspectOut(name=a.name, width=a.width, height=a.height, experimental=a.experimental)
            for a in ASPECTS.values()
            if not a.experimental or s.allow_experimental_sizes
        ],
        style_presets=sorted(STYLE_PRESETS),
        prompt_max_chars=s.prompt_max_chars,
        upload_max_bytes=s.upload_max_bytes,
    )


@router.post(
    "/generations",
    response_model=GenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Replay of an earlier request with the same key"}},
)
async def create_generation(
    body: CreateGenerationRequest,
    user: CurrentUser,
    key: IdempotencyKey,
    container: ContainerDep,
    response: Response,
) -> GenerationOut:
    service = StudioService(container)
    job, replayed = await service.create_generation(user, key, body)
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await _single(service, job)


@router.get("/generations", response_model=GenerationPage)
async def list_generations(
    user: CurrentUser,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: uuid.UUID | None = None,
) -> GenerationPage:
    service = StudioService(container)
    jobs = await service.list_jobs(user.id, limit=limit, before=str(cursor) if cursor else None)
    images = await service.images_for([job.id for job in jobs])
    items = [await _to_out(service, job, images[job.id]) for job in jobs]
    next_cursor = str(jobs[-1].id) if len(jobs) == limit else None
    return GenerationPage(items=items, next_cursor=next_cursor)


@router.get("/generations/{job_id}", response_model=GenerationOut)
async def get_generation(
    job_id: uuid.UUID, user: CurrentUser, container: ContainerDep
) -> GenerationOut:
    service = StudioService(container)
    return await _single(service, await service.get_job(user.id, str(job_id)))


@router.post("/generations/{job_id}/cancel", response_model=GenerationOut)
async def cancel_generation(
    job_id: uuid.UUID, user: CurrentUser, container: ContainerDep
) -> GenerationOut:
    service = StudioService(container)
    return await _single(service, await service.cancel(user.id, str(job_id)))


@router.post(
    "/generations/{job_id}/retry",
    response_model=GenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_generation(
    job_id: uuid.UUID,
    user: CurrentUser,
    key: IdempotencyKey,
    container: ContainerDep,
    response: Response,
) -> GenerationOut:
    service = StudioService(container)
    job, replayed = await service.retry(user, str(job_id), key)
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await _single(service, job)


@router.delete("/generations/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_generation(job_id: uuid.UUID, user: CurrentUser, container: ContainerDep) -> None:
    await StudioService(container).delete(user.id, str(job_id))


@router.post("/generated-images/{image_id}/download", response_model=DownloadOut)
async def download_image(
    image_id: uuid.UUID, user: CurrentUser, container: ContainerDep
) -> DownloadOut:
    url = await StudioService(container).image_url(user.id, str(image_id))
    return DownloadOut(url=url, expires_in=container.settings.signed_url_ttl_seconds)


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def create_upload(
    user: CurrentUser,
    container: ContainerDep,
    file: Annotated[UploadFile, File()],
) -> UploadOut:
    limit = container.settings.upload_max_bytes
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(UPLOAD_CHUNK):
        size += len(chunk)
        if size > limit:
            raise ProblemError(413, "file_too_large", "The file is too large.")
        chunks.append(chunk)
    upload = await StudioService(container).create_upload(user, b"".join(chunks))
    return UploadOut(
        id=uuid.UUID(upload.id),
        width=upload.width,
        height=upload.height,
        mime_type=upload.mime_type,
        expires_at=upload.expires_at,
    )


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(upload_id: uuid.UUID, user: CurrentUser, container: ContainerDep) -> None:
    await StudioService(container).delete_upload(user.id, str(upload_id))
