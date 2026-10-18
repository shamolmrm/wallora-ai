"""Studio use cases: generation jobs, reference uploads and downloads.

Creation order (see docs/06-generation-and-credits.md):
1. Validate and plan the request (tier, aspect, kind, prompt) - no I/O.
2. Fast idempotency lookup: a replayed key returns the original job.
3. Kill switch, abuse strikes, input ownership.
4. Prompt moderation outside any transaction; failures are closed (503).
5. One transaction holding the user's credit-account lock: re-check the key,
   starter grants, rate limits, insert job, commit spend estimate against
   caps, reserve credits. Any failure rolls everything back.
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Container
from app.core.errors import ProblemError
from app.core.ids import new_id
from app.core.imaging import InvalidImageError, sanitize_upload
from app.modules.credits import service as credits
from app.modules.moderation.models import SubjectType
from app.modules.moderation.service import moderate_prompt, recent_strikes, record_check
from app.modules.spend import service as spend
from app.modules.studio.catalog import ASPECTS, TIERS, Aspect, Tier, estimate_cost_micros
from app.modules.studio.models import (
    GeneratedImage,
    GenerationJob,
    GenerationJobInput,
    JobKind,
    JobStatus,
    ModerationStatus,
    Upload,
)
from app.modules.studio.prompts import (
    DEFAULT_VARIATION_PROMPT,
    STYLE_PRESETS,
    compose_prompt,
    normalize_prompt,
)
from app.modules.studio.schemas import CreateGenerationRequest
from app.providers.image.models import ModelCapabilities, get_model, size_problem
from app.providers.moderation.base import ModerationUnavailableError

OUTPUT_FORMAT = "webp"


@dataclass(frozen=True)
class JobPlan:
    kind: JobKind
    prompt: str
    style_preset: str | None
    tier: Tier
    aspect: Aspect
    model: ModelCapabilities
    reference_ids: list[uuid.UUID]
    source_image_id: uuid.UUID | None
    estimate_micros: int
    request_hash: str


def safety_identifier(user_id: uuid.UUID) -> str:
    """Stable pseudonymous ID sent to the provider for abuse attribution."""
    return hashlib.sha256(f"wallora:{user_id}".encode()).hexdigest()[:32]


def upload_key(user_id: uuid.UUID, upload_id: uuid.UUID) -> str:
    return f"u/{user_id}/ref/{upload_id}.png"


def output_keys(user_id: uuid.UUID, job_id: uuid.UUID, image_id: uuid.UUID) -> tuple[str, str]:
    base = f"u/{user_id}/gen/{job_id}/{image_id}"
    return f"{base}.webp", f"{base}_thumb.webp"


class StudioService:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.s = container.settings

    # ------------------------------------------------------------------ planning
    def plan(self, req: CreateGenerationRequest) -> JobPlan:
        tier = TIERS.get(req.tier)
        if tier is None:
            raise ProblemError(422, "invalid_tier", "Unknown generation tier.")
        if tier.premium_only:
            # Entitlements arrive with the purchases phase; until then premium tiers are closed.
            raise ProblemError(403, "tier_requires_premium", "This tier requires Premium.")
        aspect = ASPECTS.get(req.aspect)
        if aspect is None:
            raise ProblemError(422, "invalid_aspect", "Unknown aspect ratio.")
        if req.style_preset is not None and req.style_preset not in STYLE_PRESETS:
            raise ProblemError(422, "invalid_style_preset", "Unknown style preset.")

        refs = list(req.reference_upload_ids)
        if len(set(refs)) != len(refs):
            raise ProblemError(422, "duplicate_reference", "Reference images must be distinct.")
        if req.kind is JobKind.GENERATE and (refs or req.source_image_id):
            raise ProblemError(422, "invalid_inputs", "Text-to-image takes no input images.")
        if req.kind is JobKind.EDIT and (
            req.source_image_id or not 1 <= len(refs) <= self.s.max_reference_images
        ):
            raise ProblemError(
                422,
                "invalid_inputs",
                f"Edits need 1-{self.s.max_reference_images} reference images.",
            )
        if req.kind is JobKind.VARIATION and (refs or req.source_image_id is None):
            raise ProblemError(422, "invalid_inputs", "Variations need exactly one source image.")

        raw_prompt = req.prompt
        if raw_prompt is None or not raw_prompt.strip():
            if req.kind is not JobKind.VARIATION:
                raise ProblemError(422, "prompt_required", "A prompt is required.")
            raw_prompt = DEFAULT_VARIATION_PROMPT
        prompt = normalize_prompt(raw_prompt, max_chars=self.s.prompt_max_chars)

        model = get_model(tier.model if req.kind is JobKind.GENERATE else tier.edit_model)
        input_images = len(refs) + (1 if req.source_image_id else 0)
        if input_images and (not model.supports_edit or input_images > model.max_input_images):
            raise ProblemError(422, "kind_not_supported", "This tier does not support edits.")
        if aspect.experimental and not self.s.allow_experimental_sizes:
            raise ProblemError(422, "aspect_not_supported", "This size is not available.")
        problem = size_problem(
            model, aspect.width, aspect.height, allow_experimental=self.s.allow_experimental_sizes
        )
        if problem:
            raise ProblemError(422, "aspect_not_supported", "This size is not available.", problem)
        if tier.quality not in model.qualities:
            raise ProblemError(500, "tier_misconfigured", "Generation tier is misconfigured.")

        estimate = estimate_cost_micros(
            model=model.name,
            quality=tier.quality,
            width=aspect.width,
            height=aspect.height,
            prompt_chars=len(compose_prompt(prompt, req.style_preset)),
            input_images=input_images,
        )
        canonical = {
            "kind": req.kind.value,
            "prompt": prompt,
            "style_preset": req.style_preset,
            "tier": tier.name,
            "aspect": aspect.name,
            "references": [str(r) for r in refs],
            "source_image_id": str(req.source_image_id) if req.source_image_id else None,
        }
        request_hash = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return JobPlan(
            kind=req.kind,
            prompt=prompt,
            style_preset=req.style_preset,
            tier=tier,
            aspect=aspect,
            model=model,
            reference_ids=refs,
            source_image_id=req.source_image_id,
            estimate_micros=estimate,
            request_hash=request_hash,
        )

    def _limits(self) -> spend.SpendLimits:
        return spend.SpendLimits(
            global_daily_micros=spend.usd_to_micros(self.s.global_daily_cost_cap_usd),
            global_monthly_micros=spend.usd_to_micros(self.s.global_monthly_cost_cap_usd),
            user_daily_micros=spend.usd_to_micros(self.s.user_daily_cost_cap_usd),
            user_daily_jobs=self.s.user_daily_generation_cap,
        )

    # ------------------------------------------------------------------ creation
    @staticmethod
    async def _find_by_key(
        session: AsyncSession, user_id: uuid.UUID, key: str
    ) -> GenerationJob | None:
        stmt = select(GenerationJob).where(
            GenerationJob.user_id == user_id, GenerationJob.idempotency_key == key
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _replay(job: GenerationJob, request_hash: str) -> tuple[GenerationJob, bool]:
        if job.request_hash != request_hash:
            raise ProblemError(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was already used for a different request.",
            )
        return job, True

    async def _check_inputs(self, session: AsyncSession, user_id: uuid.UUID, plan: JobPlan) -> None:
        now = self.c.clock()
        if plan.reference_ids:
            stmt = select(func.count()).where(
                Upload.id.in_(plan.reference_ids),
                Upload.user_id == user_id,
                Upload.deleted_at.is_(None),
                Upload.expires_at > now,
                Upload.moderation_status == ModerationStatus.PASSED,
            )
            if (await session.execute(stmt)).scalar_one() != len(plan.reference_ids):
                raise ProblemError(
                    422, "invalid_reference", "A reference image is missing or expired."
                )
        if plan.source_image_id:
            stmt = select(func.count()).where(
                GeneratedImage.id == plan.source_image_id,
                GeneratedImage.user_id == user_id,
                GeneratedImage.deleted_at.is_(None),
            )
            if (await session.execute(stmt)).scalar_one() != 1:
                raise ProblemError(422, "invalid_source_image", "The source image is unavailable.")

    async def _enforce_rate_limits(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        now = self.c.clock()
        for window, limit in (
            (timedelta(minutes=1), self.s.user_generations_per_minute),
            (timedelta(hours=1), self.s.user_generations_per_hour),
        ):
            stmt = select(func.count()).where(
                GenerationJob.user_id == user_id, GenerationJob.created_at > now - window
            )
            if (await session.execute(stmt)).scalar_one() >= limit:
                raise ProblemError(
                    429,
                    "rate_limited",
                    "Too many generation requests. Please wait and try again.",
                    headers={"Retry-After": str(int(window.total_seconds()))},
                )

    async def create_generation(
        self,
        user_id: uuid.UUID,
        idempotency_key: str,
        req: CreateGenerationRequest,
        *,
        retry_of: uuid.UUID | None = None,
    ) -> tuple[GenerationJob, bool]:
        """Returns ``(job, replayed)``."""
        plan = self.plan(req)

        async with self.c.sessions() as session:
            existing = await self._find_by_key(session, user_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, plan.request_hash)
            if not self.s.generation_enabled:
                raise ProblemError(
                    503, "generation_paused", "Generation is temporarily unavailable."
                )
            strikes = await recent_strikes(
                session,
                user_id=user_id,
                now=self.c.clock(),
                window=timedelta(hours=self.s.abuse_strike_window_hours),
            )
            if strikes >= self.s.abuse_strike_threshold:
                raise ProblemError(
                    403,
                    "generation_restricted",
                    "Generation is temporarily restricted on this account.",
                )
            await self._check_inputs(session, user_id, plan)

        try:
            verdict = await moderate_prompt(self.c.moderation, plan.prompt)
        except ModerationUnavailableError:
            raise ProblemError(
                503,
                "moderation_unavailable",
                "Safety checks are temporarily unavailable. Please try again.",
                headers={"Retry-After": "30"},
            ) from None
        if verdict.blocked:
            async with self.c.sessions() as session, session.begin():
                record_check(
                    session,
                    user_id=user_id,
                    subject_type=SubjectType.PROMPT,
                    subject_id=None,
                    verdict=verdict,
                    now=self.c.clock(),
                )
            raise ProblemError(
                422,
                "prompt_rejected",
                "This prompt can't be used. Please review the content guidelines.",
            )

        async with self.c.sessions() as session, session.begin():
            now = self.c.clock()
            await credits.lock_account(session, user_id)
            # Same-user requests are serialised by the lock, so this re-check is race-free.
            existing = await self._find_by_key(session, user_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, plan.request_hash)
            await credits.ensure_starter_grants(
                session,
                user_id=user_id,
                now=now,
                welcome_credits=self.s.welcome_credits,
                daily_free_credits=self.s.daily_free_credits,
            )
            await self._enforce_rate_limits(session, user_id)
            await self._check_inputs(session, user_id, plan)

            job = GenerationJob(
                id=new_id(),
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=plan.request_hash,
                kind=plan.kind,
                status=JobStatus.QUEUED,
                prompt=plan.prompt,
                style_preset=plan.style_preset,
                tier=plan.tier.name,
                aspect=plan.aspect.name,
                width=plan.aspect.width,
                height=plan.aspect.height,
                quality=plan.tier.quality,
                output_format=OUTPUT_FORMAT,
                provider=self.c.image_provider.name,
                model=plan.model.name,
                credit_cost=plan.tier.credits,
                estimated_cost_usd_micros=plan.estimate_micros,
                max_attempts=self.s.job_max_attempts,
                next_attempt_at=now,
                retry_of_job_id=retry_of,
                source_image_id=plan.source_image_id,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            await session.flush()
            for position, upload_id in enumerate(plan.reference_ids):
                session.add(
                    GenerationJobInput(job_id=job.id, upload_id=upload_id, position=position)
                )

            try:
                await spend.commit_estimate(
                    session,
                    user_id=user_id,
                    estimate_micros=plan.estimate_micros,
                    limits=self._limits(),
                    at=now,
                )
            except spend.SpendCapError as exc:
                if exc.scope == "global":
                    raise ProblemError(
                        503,
                        "generation_paused",
                        "Generation is temporarily unavailable. Please try again later.",
                    ) from None
                raise ProblemError(
                    429,
                    "daily_limit_reached",
                    "You've reached today's generation limit.",
                    headers={"Retry-After": "3600"},
                ) from None
            try:
                await credits.reserve(
                    session, user_id=user_id, job_id=job.id, amount=plan.tier.credits, now=now
                )
            except credits.InsufficientCreditsError as exc:
                raise ProblemError(
                    402,
                    "insufficient_credits",
                    "You don't have enough credits for this generation.",
                    extra={"required": exc.required, "available": exc.available},
                ) from None
        return job, False

    # ------------------------------------------------------------------ queries
    async def get_job(self, user_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob:
        async with self.c.sessions() as session:
            job = await session.get(GenerationJob, job_id)
        if job is None or job.user_id != user_id or job.deleted_at is not None:
            raise ProblemError(404, "not_found", "Generation not found.")
        return job

    async def list_jobs(
        self, user_id: uuid.UUID, *, limit: int, before: uuid.UUID | None
    ) -> list[GenerationJob]:
        stmt = select(GenerationJob).where(
            GenerationJob.user_id == user_id, GenerationJob.deleted_at.is_(None)
        )
        if before is not None:
            stmt = stmt.where(GenerationJob.id < before)
        stmt = stmt.order_by(GenerationJob.id.desc()).limit(limit)
        async with self.c.sessions() as session:
            return list((await session.execute(stmt)).scalars())

    async def images_for(self, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[GeneratedImage]]:
        if not job_ids:
            return {}
        stmt = (
            select(GeneratedImage)
            .where(GeneratedImage.job_id.in_(job_ids), GeneratedImage.deleted_at.is_(None))
            .order_by(GeneratedImage.created_at)
        )
        result: dict[uuid.UUID, list[GeneratedImage]] = {job_id: [] for job_id in job_ids}
        async with self.c.sessions() as session:
            for image in (await session.execute(stmt)).scalars():
                result[image.job_id].append(image)
        return result

    # ------------------------------------------------------------------ mutations
    async def cancel(self, user_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob:
        async with self.c.sessions() as session, session.begin():
            await credits.lock_account(session, user_id)
            stmt = (
                select(GenerationJob)
                .where(GenerationJob.id == job_id, GenerationJob.user_id == user_id)
                .with_for_update()
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None or job.deleted_at is not None:
                raise ProblemError(404, "not_found", "Generation not found.")
            if job.status != JobStatus.QUEUED:
                raise ProblemError(
                    409,
                    "not_cancellable",
                    "Only queued generations can be cancelled.",
                )
            now = self.c.clock()
            job.status = JobStatus.CANCELLED
            job.finished_at = now
            await credits.release(
                session, user_id=user_id, job_id=job.id, now=now, reason="cancelled"
            )
            await spend.adjust(
                session,
                user_id=user_id,
                cost_delta=job.actual_cost_usd_micros - job.estimated_cost_usd_micros,
                job_delta=-1,
                committed_at=job.created_at,
            )
        return job

    async def retry(
        self, user_id: uuid.UUID, job_id: uuid.UUID, idempotency_key: str
    ) -> tuple[GenerationJob, bool]:
        original = await self.get_job(user_id, job_id)
        if original.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise ProblemError(
                409, "not_retryable", "Only failed or cancelled generations can be retried."
            )
        async with self.c.sessions() as session:
            stmt = (
                select(GenerationJobInput.upload_id)
                .where(GenerationJobInput.job_id == job_id)
                .order_by(GenerationJobInput.position)
            )
            refs = list((await session.execute(stmt)).scalars())
        req = CreateGenerationRequest(
            kind=JobKind(original.kind),
            prompt=original.prompt,
            tier=original.tier,
            aspect=original.aspect,
            style_preset=original.style_preset,
            reference_upload_ids=refs,
            source_image_id=original.source_image_id,
        )
        return await self.create_generation(user_id, idempotency_key, req, retry_of=job_id)

    async def delete(self, user_id: uuid.UUID, job_id: uuid.UUID) -> None:
        keys: list[str] = []
        async with self.c.sessions() as session, session.begin():
            stmt = (
                select(GenerationJob)
                .where(GenerationJob.id == job_id, GenerationJob.user_id == user_id)
                .with_for_update()
            )
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None or job.deleted_at is not None:
                raise ProblemError(404, "not_found", "Generation not found.")
            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                raise ProblemError(409, "job_active", "Cancel or wait for the generation first.")
            now = self.c.clock()
            job.deleted_at = now
            job.prompt = None  # erase private content; the row stays for credit/spend records
            images = await session.execute(
                select(GeneratedImage).where(
                    GeneratedImage.job_id == job_id, GeneratedImage.deleted_at.is_(None)
                )
            )
            for image in images.scalars():
                image.deleted_at = now
                keys.extend([image.storage_key, image.thumb_key])
        await asyncio.gather(*(self.c.storage.delete(key) for key in keys))

    # ------------------------------------------------------------------ uploads
    async def create_upload(self, user_id: uuid.UUID, data: bytes) -> Upload:
        now = self.c.clock()
        async with self.c.sessions() as session:
            stmt = select(func.count()).where(
                Upload.user_id == user_id, Upload.created_at > now - timedelta(hours=1)
            )
            if (await session.execute(stmt)).scalar_one() >= self.s.uploads_per_hour:
                raise ProblemError(
                    429, "rate_limited", "Too many uploads.", headers={"Retry-After": "3600"}
                )
        try:
            image = await asyncio.to_thread(
                sanitize_upload,
                data,
                max_bytes=self.s.upload_max_bytes,
                max_pixels=self.s.upload_max_pixels,
                max_side=self.s.upload_max_side_px,
            )
        except InvalidImageError as exc:
            raise ProblemError(422, exc.code, str(exc)) from None
        try:
            verdict = await self.c.moderation.check_image(image.data, image.mime_type)
        except ModerationUnavailableError:
            raise ProblemError(
                503,
                "moderation_unavailable",
                "Safety checks are temporarily unavailable. Please try again.",
                headers={"Retry-After": "30"},
            ) from None
        upload_id = new_id()
        if verdict.blocked:
            async with self.c.sessions() as session, session.begin():
                record_check(
                    session,
                    user_id=user_id,
                    subject_type=SubjectType.UPLOAD,
                    subject_id=None,
                    verdict=verdict,
                    now=now,
                )
            raise ProblemError(422, "upload_rejected", "This image can't be used.")
        key = upload_key(user_id, upload_id)
        await self.c.storage.put(key, image.data, image.mime_type)
        upload = Upload(
            id=upload_id,
            user_id=user_id,
            storage_key=key,
            mime_type=image.mime_type,
            size_bytes=len(image.data),
            width=image.width,
            height=image.height,
            sha256=image.sha256,
            moderation_status=ModerationStatus.PASSED,
            expires_at=now + timedelta(days=self.s.reference_retention_days),
            created_at=now,
        )
        async with self.c.sessions() as session, session.begin():
            session.add(upload)
        return upload

    async def delete_upload(self, user_id: uuid.UUID, upload_id: uuid.UUID) -> None:
        async with self.c.sessions() as session, session.begin():
            upload = await session.get(Upload, upload_id, with_for_update=True)
            if upload is None or upload.user_id != user_id or upload.deleted_at is not None:
                raise ProblemError(404, "not_found", "Upload not found.")
            upload.deleted_at = self.c.clock()
            key = upload.storage_key
        await self.c.storage.delete(key)

    async def image_url(self, user_id: uuid.UUID, image_id: uuid.UUID) -> str:
        async with self.c.sessions() as session:
            image = await session.get(GeneratedImage, image_id)
        if image is None or image.user_id != user_id or image.deleted_at is not None:
            raise ProblemError(404, "not_found", "Image not found.")
        return await self.c.storage.signed_url(image.storage_key, self.s.signed_url_ttl_seconds)
