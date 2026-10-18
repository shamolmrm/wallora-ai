"""Generation worker.

Each job attempt:
  claim (SKIP LOCKED + lease) -> load inputs -> provider call (timeout) ->
  validate output -> output moderation -> store -> settle.

Settlement always re-locks the job and checks the lease (owner and attempt
number). If the reaper took the job back meanwhile, the late result is
discarded and its objects deleted, so a job can never be settled twice.

Spend accounting per attempt: actual cost when the provider reports usage,
otherwise the estimate (``billing_uncertain``) - including timeouts, because
the provider may have finished and billed after we stopped waiting.
"""

import asyncio
import contextlib
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Container
from app.core.ids import new_id
from app.core.imaging import InvalidImageError, ProcessedOutput, process_output
from app.modules.credits import service as credits
from app.modules.moderation.models import SubjectType
from app.modules.moderation.service import record_check
from app.modules.spend import service as spend
from app.modules.studio.catalog import cost_micros
from app.modules.studio.models import (
    TERMINAL_STATUSES,
    GeneratedImage,
    GenerationJob,
    GenerationJobInput,
    JobKind,
    JobStatus,
    Upload,
)
from app.modules.studio.prompts import compose_prompt
from app.modules.studio.service import output_keys, safety_identifier
from app.providers.image.base import (
    ContentBlockedError,
    ImageRequest,
    ImageResult,
    InputImage,
    ProviderError,
)
from app.providers.image.models import MAX_PIXELS
from app.providers.moderation.base import ModerationUnavailableError, ModerationVerdict

logger = logging.getLogger(__name__)

TIMEOUT_MARGIN_SECONDS = 10.0
MODERATION_ATTEMPTS = 3


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    user_id: uuid.UUID
    attempt: int


class _AttemptFailedError(Exception):
    def __init__(
        self, error: ProviderError, attempt_cost: int, *, block_recorded: bool = False
    ) -> None:
        super().__init__(error.code)
        self.error = error
        self.attempt_cost = attempt_cost
        self.block_recorded = block_recorded


def backoff_seconds(attempt: int, base: float, maximum: float, rng: random.Random) -> float:
    """Exponential backoff with jitter in [50%, 100%] of the capped delay."""
    capped: float = min(maximum, base * (2 ** max(0, attempt - 1)))
    return capped * (0.5 + rng.random() / 2)


class GenerationWorker:
    def __init__(
        self, container: Container, *, worker_id: str, rng: random.Random | None = None
    ) -> None:
        self.c = container
        self.s = container.settings
        self.worker_id = worker_id
        self.rng = rng or random.Random()  # noqa: S311 - jitter, not security

    # ------------------------------------------------------------------ claiming
    async def claim(self) -> Claim | None:
        now = self.c.clock()
        async with self.c.sessions() as session, session.begin():
            candidate = (
                select(GenerationJob.id)
                .where(
                    GenerationJob.status == JobStatus.QUEUED, GenerationJob.next_attempt_at <= now
                )
                .order_by(GenerationJob.next_attempt_at, GenerationJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            stmt = (
                update(GenerationJob)
                .where(GenerationJob.id == candidate)
                .values(
                    status=JobStatus.PROCESSING,
                    attempts=GenerationJob.attempts + 1,
                    lease_owner=self.worker_id,
                    lease_expires_at=now + timedelta(seconds=self.s.job_lease_seconds),
                    started_at=func.coalesce(GenerationJob.started_at, now),
                    updated_at=now,
                )
                .returning(GenerationJob.id, GenerationJob.user_id, GenerationJob.attempts)
            )
            row = (await session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return Claim(job_id=row[0], user_id=row[1], attempt=row[2])

    # ------------------------------------------------------------------ processing
    async def process(self, claim: Claim) -> None:
        async with self.c.sessions() as session:
            job = await session.get(GenerationJob, claim.job_id)
        assert job is not None  # noqa: S101 - claimed rows are never deleted
        try:
            result = await self._call_provider(job)
            attempt_cost, uncertain = self._attempt_cost(job, result)
            output = await self._validate_output(result, attempt_cost)
            await self._moderate_output(job, output, attempt_cost)
            image_id = new_id()
            keys = await self._store(job, image_id, output, attempt_cost)
        except _AttemptFailedError as fail:
            await self._handle_failure(
                claim, fail.error, fail.attempt_cost, block_recorded=fail.block_recorded
            )
            return
        settled = await self._settle_success(
            claim,
            image_id=image_id,
            keys=keys,
            output=output,
            result=result,
            attempt_cost=attempt_cost,
            uncertain=uncertain,
        )
        if not settled:
            logger.warning("lease_lost_discarding_result", extra={"job_id": str(claim.job_id)})
            await asyncio.gather(*(self.c.storage.delete(key) for key in keys))

    async def _load_inputs(self, job: GenerationJob) -> list[InputImage]:
        if job.kind == JobKind.GENERATE:
            return []
        missing = _AttemptFailedError(
            ProviderError("input_unavailable", "inputs missing", retryable=False), 0
        )
        async with self.c.sessions() as session:
            if job.kind == JobKind.EDIT:
                stmt = (
                    select(Upload.storage_key, Upload.mime_type, Upload.deleted_at)
                    .join(GenerationJobInput, GenerationJobInput.upload_id == Upload.id)
                    .where(GenerationJobInput.job_id == job.id)
                    .order_by(GenerationJobInput.position)
                )
                rows = [(row[0], row[1], row[2]) for row in await session.execute(stmt)]
                if not rows or any(deleted is not None for _, _, deleted in rows):
                    raise missing
                keys = [(key, mime) for key, mime, _ in rows]
            else:
                image = (
                    await session.get(GeneratedImage, job.source_image_id)
                    if job.source_image_id
                    else None
                )
                if image is None or image.deleted_at is not None:
                    raise missing
                keys = [(image.storage_key, image.mime_type)]
        images = []
        for index, (key, mime) in enumerate(keys):
            try:
                data = await self.c.storage.get(key)
            except Exception as exc:
                raise _AttemptFailedError(
                    ProviderError("input_unavailable", "input read failed", retryable=True), 0
                ) from exc
            ext = key.rsplit(".", 1)[-1]
            images.append(InputImage(data=data, mime_type=mime, filename=f"input-{index}.{ext}"))
        return images

    async def _call_provider(self, job: GenerationJob) -> ImageResult:
        images = await self._load_inputs(job)
        assert job.prompt is not None  # noqa: S101 - prompts are only erased on terminal jobs
        request = ImageRequest(
            model=job.model,
            prompt=compose_prompt(job.prompt, job.style_preset),
            width=job.width,
            height=job.height,
            quality=job.quality,
            output_format=job.output_format,
            safety_identifier=safety_identifier(job.user_id),
            images=images,
        )
        provider = self.c.image_provider
        timeout = self.s.openai_timeout_seconds + TIMEOUT_MARGIN_SECONDS
        try:
            async with asyncio.timeout(timeout):
                if images:
                    return await provider.edit(request)
                return await provider.generate(request)
        except ProviderError as exc:
            cost = job.estimated_cost_usd_micros if exc.billing_uncertain else 0
            raise _AttemptFailedError(exc, cost) from exc
        except TimeoutError as exc:
            error = ProviderError("timeout", "timed out", retryable=True, billing_uncertain=True)
            raise _AttemptFailedError(error, job.estimated_cost_usd_micros) from exc

    @staticmethod
    def _attempt_cost(job: GenerationJob, result: ImageResult) -> tuple[int, bool]:
        if result.usage is None:
            return job.estimated_cost_usd_micros, True
        return cost_micros(job.model, result.usage), False

    async def _validate_output(self, result: ImageResult, attempt_cost: int) -> ProcessedOutput:
        try:
            return await asyncio.to_thread(process_output, result.data, max_pixels=MAX_PIXELS)
        except InvalidImageError as exc:
            error = ProviderError("invalid_output", "provider output invalid", retryable=False)
            raise _AttemptFailedError(error, attempt_cost) from exc

    async def _moderate_output(
        self, job: GenerationJob, output: ProcessedOutput, attempt_cost: int
    ) -> None:
        verdict: ModerationVerdict | None = None
        for attempt in range(1, MODERATION_ATTEMPTS + 1):
            try:
                verdict = await self.c.moderation.check_image(output.data, output.mime_type)
                break
            except ModerationUnavailableError:
                if attempt == MODERATION_ATTEMPTS:
                    error = ProviderError(
                        "moderation_unavailable", "output moderation failed", retryable=False
                    )
                    raise _AttemptFailedError(error, attempt_cost) from None
                await asyncio.sleep(backoff_seconds(attempt, 0.5, 5.0, self.rng))
        assert verdict is not None  # noqa: S101
        if verdict.blocked:
            async with self.c.sessions() as session, session.begin():
                record_check(
                    session,
                    user_id=job.user_id,
                    subject_type=SubjectType.OUTPUT,
                    subject_id=job.id,
                    verdict=verdict,
                    now=self.c.clock(),
                )
            raise _AttemptFailedError(
                ContentBlockedError("output blocked by moderation"),
                attempt_cost,
                block_recorded=True,
            )

    async def _store(
        self, job: GenerationJob, image_id: uuid.UUID, output: ProcessedOutput, attempt_cost: int
    ) -> tuple[str, str]:
        full_key, thumb_key = output_keys(job.user_id, job.id, image_id)
        for attempt in range(1, self.s.storage_max_attempts + 1):
            try:
                await self.c.storage.put(full_key, output.data, output.mime_type)
                await self.c.storage.put(thumb_key, output.thumb, "image/webp")
            except Exception as exc:
                if attempt == self.s.storage_max_attempts:
                    logger.exception("storage_put_failed", extra={"job_id": str(job.id)})
                    error = ProviderError(
                        "storage_error", "could not store output", retryable=False
                    )
                    raise _AttemptFailedError(error, attempt_cost) from exc
                await asyncio.sleep(backoff_seconds(attempt, 0.5, 5.0, self.rng))
            else:
                return full_key, thumb_key
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ settlement
    async def _lock_owned_job(self, session: AsyncSession, claim: Claim) -> GenerationJob | None:
        await credits.lock_account(session, claim.user_id)
        stmt = (
            select(GenerationJob)
            .where(GenerationJob.id == claim.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job = (await session.execute(stmt)).scalar_one()
        if (
            job.status != JobStatus.PROCESSING
            or job.lease_owner != self.worker_id
            or job.attempts != claim.attempt
        ):
            return None
        return job

    async def _settle_success(
        self,
        claim: Claim,
        *,
        image_id: uuid.UUID,
        keys: tuple[str, str],
        output: ProcessedOutput,
        result: ImageResult,
        attempt_cost: int,
        uncertain: bool,
    ) -> bool:
        async with self.c.sessions() as session, session.begin():
            job = await self._lock_owned_job(session, claim)
            if job is None:
                return False
            now = self.c.clock()
            session.add(
                GeneratedImage(
                    id=image_id,
                    job_id=job.id,
                    user_id=job.user_id,
                    storage_key=keys[0],
                    thumb_key=keys[1],
                    width=output.width,
                    height=output.height,
                    mime_type=output.mime_type,
                    size_bytes=len(output.data),
                    created_at=now,
                )
            )
            job.actual_cost_usd_micros += attempt_cost
            job.billing_uncertain = job.billing_uncertain or uncertain
            job.usage = result.usage.as_dict() if result.usage else None
            job.status = JobStatus.SUCCEEDED
            job.error_code = None
            job.finished_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            await credits.capture(session, user_id=job.user_id, job_id=job.id, now=now)
            await spend.adjust(
                session,
                user_id=job.user_id,
                cost_delta=job.actual_cost_usd_micros - job.estimated_cost_usd_micros,
                job_delta=0,
                committed_at=job.created_at,
            )
        return True

    async def _handle_failure(
        self,
        claim: Claim,
        error: ProviderError,
        attempt_cost: int,
        *,
        block_recorded: bool = False,
    ) -> None:
        async with self.c.sessions() as session, session.begin():
            job = await self._lock_owned_job(session, claim)
            if job is None:
                logger.warning("lease_lost_on_failure", extra={"job_id": str(claim.job_id)})
                return
            job.actual_cost_usd_micros += attempt_cost
            job.billing_uncertain = job.billing_uncertain or error.billing_uncertain
            job.error_code = error.code
            if isinstance(error, ContentBlockedError) and not block_recorded:
                # The provider's own safety system refused: counts as an abuse strike too.
                record_check(
                    session,
                    user_id=job.user_id,
                    subject_type=SubjectType.OUTPUT,
                    subject_id=job.id,
                    verdict=ModerationVerdict(True, f"{job.provider}-safety", ["provider_blocked"]),
                    now=self.c.clock(),
                )
            if error.retryable and job.attempts < job.max_attempts:
                self._requeue(job)
            else:
                await self._fail(session, job)

    def _requeue(self, job: GenerationJob) -> None:
        delay = backoff_seconds(
            job.attempts, self.s.retry_base_delay_seconds, self.s.retry_max_delay_seconds, self.rng
        )
        job.status = JobStatus.QUEUED
        job.next_attempt_at = self.c.clock() + timedelta(seconds=delay)
        job.lease_owner = None
        job.lease_expires_at = None

    async def _fail(self, session: AsyncSession, job: GenerationJob) -> None:
        now = self.c.clock()
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        await credits.release(
            session, user_id=job.user_id, job_id=job.id, now=now, reason=job.error_code or "failed"
        )
        await spend.adjust(
            session,
            user_id=job.user_id,
            cost_delta=job.actual_cost_usd_micros - job.estimated_cost_usd_micros,
            job_delta=-1,
            committed_at=job.created_at,
        )

    # ------------------------------------------------------------------ maintenance
    async def reap_expired_leases(self) -> int:
        """Requeue (or fail and refund) jobs whose worker died mid-attempt."""
        now = self.c.clock()
        async with self.c.sessions() as session:
            stmt = select(GenerationJob.id, GenerationJob.user_id).where(
                GenerationJob.status == JobStatus.PROCESSING, GenerationJob.lease_expires_at < now
            )
            candidates = [(row[0], row[1]) for row in await session.execute(stmt)]
        reaped = 0
        for job_id, user_id in candidates:
            async with self.c.sessions() as session, session.begin():
                await credits.lock_account(session, user_id)
                job = (
                    await session.execute(
                        select(GenerationJob)
                        .where(GenerationJob.id == job_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one()
                if job.status != JobStatus.PROCESSING or job.lease_expires_at is None:
                    continue
                if job.lease_expires_at >= self.c.clock():
                    continue
                # The dead attempt may have reached the provider: count its estimate.
                job.actual_cost_usd_micros += job.estimated_cost_usd_micros
                job.billing_uncertain = True
                job.error_code = "lease_expired"
                if job.attempts < job.max_attempts:
                    self._requeue(job)
                else:
                    await self._fail(session, job)
                reaped += 1
        return reaped

    async def purge_expired_uploads(self) -> int:
        """Delete reference uploads past retention that no active job still needs."""
        now = self.c.clock()
        active_use = exists().where(
            GenerationJobInput.upload_id == Upload.id,
            GenerationJobInput.job_id == GenerationJob.id,
            GenerationJob.status.not_in([s.value for s in TERMINAL_STATUSES]),
        )
        stmt = (
            select(Upload)
            .where(Upload.deleted_at.is_(None), Upload.expires_at < now, ~active_use)
            .limit(200)
            .with_for_update(skip_locked=True, of=Upload)
        )
        keys: list[str] = []
        async with self.c.sessions() as session, session.begin():
            for upload in (await session.execute(stmt)).scalars():
                upload.deleted_at = now
                keys.append(upload.storage_key)
        await asyncio.gather(*(self.c.storage.delete(key) for key in keys))
        return len(keys)

    # ------------------------------------------------------------------ loop
    async def run_forever(self, stop: asyncio.Event, *, maintenance_every: float = 30.0) -> None:
        semaphore = asyncio.Semaphore(self.s.worker_concurrency)
        tasks: set[asyncio.Task[None]] = set()
        loop = asyncio.get_running_loop()
        next_maintenance = 0.0

        async def run(claim: Claim) -> None:
            try:
                await self.process(claim)
            except Exception:
                # Leave the job to the reaper: its lease will expire and it is retried or refunded.
                logger.exception("job_processing_crashed", extra={"job_id": str(claim.job_id)})
            finally:
                semaphore.release()

        while not stop.is_set():
            if loop.time() >= next_maintenance:
                try:
                    await self.reap_expired_leases()
                    await self.purge_expired_uploads()
                except Exception:
                    logger.exception("maintenance_failed")
                next_maintenance = loop.time() + maintenance_every
            await semaphore.acquire()
            claim = await self._safe_claim()
            if claim is None:
                semaphore.release()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.s.worker_poll_interval_seconds)
                continue
            task = asyncio.create_task(run(claim))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_claim(self) -> Claim | None:
        try:
            return await self.claim()
        except Exception:
            logger.exception("claim_failed")
            return None
