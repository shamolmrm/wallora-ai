"""Generation worker (Firestore).

Each job attempt:
  claim (transactional status check + lease) -> load inputs -> provider call
  (timeout) -> validate output -> output moderation -> store -> settle.

Settlement always re-reads the job in a transaction and checks the lease
(owner and attempt number). If the reaper took the job back meanwhile, the
late result is discarded and its objects deleted, so a job can never be
settled twice.

Spend accounting per attempt: actual cost when the provider reports usage,
otherwise the estimate (``billing_uncertain``) - including timeouts, because
the provider may have finished and billed after we stopped waiting.
"""

import asyncio
import contextlib
import logging
import random
from dataclasses import dataclass
from datetime import timedelta

from google.cloud.firestore_v1 import AsyncDocumentReference, AsyncTransaction
from google.cloud.firestore_v1.base_query import FieldFilter

from app.container import Container
from app.core.ids import new_id
from app.core.imaging import InvalidImageError, ProcessedOutput, process_output
from app.db.documents import (
    TERMINAL_STATUSES,
    ImageDoc,
    JobDoc,
    JobKind,
    JobStatus,
    ReservationDoc,
    SubjectType,
    UploadDoc,
    UserDoc,
)
from app.db.firestore import (
    GENERATED_IMAGES,
    GENERATION_JOBS,
    UPLOADS,
    USERS,
    query_all,
    run_transaction,
    tx_get,
)
from app.modules.credits.service import CreditBook
from app.modules.moderation.service import prune, record_block
from app.modules.spend.service import SpendBook
from app.modules.studio.catalog import cost_micros
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
CLAIM_CANDIDATES = 10


@dataclass(frozen=True)
class Claim:
    job_id: str
    user_id: str
    attempt: int


class _AttemptFailedError(Exception):
    def __init__(
        self, error: ProviderError, attempt_cost: int, *, block_source: str | None = None
    ) -> None:
        super().__init__(error.code)
        self.error = error
        self.attempt_cost = attempt_cost
        # Set when content was blocked; names who blocked it for the moderation record.
        self.block_source = block_source


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
        self.db = container.db
        self.worker_id = worker_id
        self.rng = rng or random.Random()  # noqa: S311 - jitter, not security

    def _job_ref(self, job_id: str) -> AsyncDocumentReference:
        return self.db.collection(GENERATION_JOBS).document(job_id)

    def _user_ref(self, user_id: str) -> AsyncDocumentReference:
        return self.db.collection(USERS).document(user_id)

    # ------------------------------------------------------------------ claiming
    async def claim(self) -> Claim | None:
        now = self.c.clock()
        query = (
            self.db.collection(GENERATION_JOBS)
            .where(filter=FieldFilter("status", "==", JobStatus.QUEUED.value))
            .where(filter=FieldFilter("next_attempt_at", "<=", now))
            .order_by("next_attempt_at")
            .limit(CLAIM_CANDIDATES)
        )
        for candidate in await query_all(query):
            claim = await self._try_claim(candidate.id)
            if claim is not None:
                return claim
        return None

    async def _try_claim(self, job_id: str) -> Claim | None:
        async def txn_fn(txn: AsyncTransaction) -> Claim | None:
            snap = await tx_get(txn, self._job_ref(job_id))
            job = JobDoc.from_doc(snap.to_dict() or {})
            now = self.c.clock()
            if job.status != JobStatus.QUEUED or job.next_attempt_at > now:
                return None  # another worker won the race
            job.status = JobStatus.PROCESSING
            job.attempts += 1
            job.lease_owner = self.worker_id
            job.lease_expires_at = now + timedelta(seconds=self.s.job_lease_seconds)
            job.started_at = job.started_at or now
            job.updated_at = now
            txn.set(snap.reference, job.to_doc())
            return Claim(job_id=job.id, user_id=job.user_id, attempt=job.attempts)

        return await run_transaction(self.db, txn_fn)

    # ------------------------------------------------------------------ processing
    async def process(self, claim: Claim) -> None:
        snap = await self._job_ref(claim.job_id).get()
        job = JobDoc.from_doc(snap.to_dict() or {})
        try:
            result = await self._call_provider(job)
            attempt_cost, uncertain = self._attempt_cost(job, result)
            output = await self._validate_output(result, attempt_cost)
            await self._moderate_output(job, output, attempt_cost)
            image_id = str(new_id())
            keys = await self._store(job, image_id, output, attempt_cost)
        except _AttemptFailedError as fail:
            await self._handle_failure(
                claim, fail.error, fail.attempt_cost, block_source=fail.block_source
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
            logger.warning("lease_lost_discarding_result", extra={"job_id": claim.job_id})
            await asyncio.gather(*(self.c.storage.delete(key) for key in keys))

    async def _load_inputs(self, job: JobDoc) -> list[InputImage]:
        if job.kind == JobKind.GENERATE:
            return []
        missing = _AttemptFailedError(
            ProviderError("input_unavailable", "inputs missing", retryable=False), 0
        )
        keys: list[tuple[str, str]] = []
        if job.kind == JobKind.EDIT:
            if not job.input_upload_ids:
                raise missing
            for upload_id in job.input_upload_ids:
                snap = await self.db.collection(UPLOADS).document(upload_id).get()
                upload = UploadDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
                if upload is None or upload.deleted:
                    raise missing
                keys.append((upload.storage_key, upload.mime_type))
        else:
            if job.source_image_id is None:
                raise missing
            snap = await self.db.collection(GENERATED_IMAGES).document(job.source_image_id).get()
            image = ImageDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
            if image is None or image.deleted_at is not None:
                raise missing
            keys.append((image.storage_key, image.mime_type))
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

    async def _call_provider(self, job: JobDoc) -> ImageResult:
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
            source = f"{job.provider}-safety" if isinstance(exc, ContentBlockedError) else None
            raise _AttemptFailedError(exc, cost, block_source=source) from exc
        except TimeoutError as exc:
            error = ProviderError("timeout", "timed out", retryable=True, billing_uncertain=True)
            raise _AttemptFailedError(error, job.estimated_cost_usd_micros) from exc

    @staticmethod
    def _attempt_cost(job: JobDoc, result: ImageResult) -> tuple[int, bool]:
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
        self, job: JobDoc, output: ProcessedOutput, attempt_cost: int
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
            raise _AttemptFailedError(
                ContentBlockedError("output blocked by moderation"),
                attempt_cost,
                block_source=verdict.provider,
            )

    async def _store(
        self, job: JobDoc, image_id: str, output: ProcessedOutput, attempt_cost: int
    ) -> tuple[str, str]:
        full_key, thumb_key = output_keys(job.user_id, job.id, image_id)
        for attempt in range(1, self.s.storage_max_attempts + 1):
            try:
                await self.c.storage.put(full_key, output.data, output.mime_type)
                await self.c.storage.put(thumb_key, output.thumb, "image/webp")
            except Exception as exc:
                if attempt == self.s.storage_max_attempts:
                    logger.exception("storage_put_failed", extra={"job_id": job.id})
                    error = ProviderError(
                        "storage_error", "could not store output", retryable=False
                    )
                    raise _AttemptFailedError(error, attempt_cost) from exc
                await asyncio.sleep(backoff_seconds(attempt, 0.5, 5.0, self.rng))
            else:
                return full_key, thumb_key
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ settlement
    async def _read_owned(
        self, txn: AsyncTransaction, claim: Claim
    ) -> tuple[UserDoc, JobDoc] | None:
        user = UserDoc.from_doc((await tx_get(txn, self._user_ref(claim.user_id))).to_dict() or {})
        job = JobDoc.from_doc((await tx_get(txn, self._job_ref(claim.job_id))).to_dict() or {})
        if (
            job.status != JobStatus.PROCESSING
            or job.lease_owner != self.worker_id
            or job.attempts != claim.attempt
        ):
            return None
        return user, job

    def _clear_lease(self, job: JobDoc) -> None:
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = self.c.clock()

    async def _settle_success(
        self,
        claim: Claim,
        *,
        image_id: str,
        keys: tuple[str, str],
        output: ProcessedOutput,
        result: ImageResult,
        attempt_cost: int,
        uncertain: bool,
    ) -> bool:
        async def txn_fn(txn: AsyncTransaction) -> bool:
            owned = await self._read_owned(txn, claim)
            if owned is None:
                return False
            user, job = owned
            now = self.c.clock()
            book = CreditBook(self.db, user, now)
            spend = SpendBook(self.db, user.id, job.created_at)
            await spend.load(txn)

            image = ImageDoc(
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
            reservation = job.reservation_doc()
            book.capture(reservation, job.id)
            job.reservation = reservation.to_doc()
            job.actual_cost_usd_micros += attempt_cost
            job.billing_uncertain = job.billing_uncertain or uncertain
            job.usage = result.usage.as_dict() if result.usage else None
            job.status = JobStatus.SUCCEEDED
            job.error_code = None
            job.finished_at = now
            self._clear_lease(job)
            spend.adjust(job.actual_cost_usd_micros - job.estimated_cost_usd_micros, 0)

            txn.create(self.db.collection(GENERATED_IMAGES).document(image_id), image.to_doc())
            txn.set(self._job_ref(job.id), job.to_doc())
            book.write(txn)
            spend.write(txn)
            txn.set(self._user_ref(user.id), user.to_doc())
            return True

        return await run_transaction(self.db, txn_fn)

    async def _handle_failure(
        self,
        claim: Claim,
        error: ProviderError,
        attempt_cost: int,
        *,
        block_source: str | None = None,
    ) -> None:
        async def txn_fn(txn: AsyncTransaction) -> None:
            owned = await self._read_owned(txn, claim)
            if owned is None:
                logger.warning("lease_lost_on_failure", extra={"job_id": claim.job_id})
                return
            user, job = owned
            now = self.c.clock()
            reservation = job.reservation_doc()
            retry = error.retryable and job.attempts < job.max_attempts
            book = CreditBook(self.db, user, now)
            spend = SpendBook(self.db, user.id, job.created_at)
            if not retry:
                await spend.load(txn)

            job.actual_cost_usd_micros += attempt_cost
            job.billing_uncertain = job.billing_uncertain or error.billing_uncertain
            job.error_code = error.code
            if block_source is not None:
                # Provider refusals and our output moderation both count as abuse strikes.
                verdict = ModerationVerdict(
                    blocked=True, provider=block_source, categories=["output_blocked"]
                )
                record_block(
                    self.db,
                    txn,
                    user=user,
                    subject_type=SubjectType.OUTPUT,
                    subject_id=job.id,
                    verdict=verdict,
                    now=now,
                )
                user.strike_times = prune(
                    user.strike_times,
                    now=now,
                    keep=timedelta(hours=self.s.abuse_strike_window_hours),
                )
            if retry:
                self._requeue(job)
            else:
                self._fail(job, book, spend, reservation)
                book.write(txn)
                spend.write(txn)
            txn.set(self._job_ref(job.id), job.to_doc())
            txn.set(self._user_ref(user.id), user.to_doc())

        await run_transaction(self.db, txn_fn)

    def _requeue(self, job: JobDoc) -> None:
        delay = backoff_seconds(
            job.attempts, self.s.retry_base_delay_seconds, self.s.retry_max_delay_seconds, self.rng
        )
        job.status = JobStatus.QUEUED
        job.next_attempt_at = self.c.clock() + timedelta(seconds=delay)
        self._clear_lease(job)

    def _fail(
        self, job: JobDoc, book: CreditBook, spend: SpendBook, reservation: ReservationDoc
    ) -> None:
        book.release(reservation, job.id, job.error_code or "failed")
        job.reservation = reservation.to_doc()
        job.status = JobStatus.FAILED
        job.finished_at = self.c.clock()
        self._clear_lease(job)
        spend.adjust(job.actual_cost_usd_micros - job.estimated_cost_usd_micros, -1)

    # ------------------------------------------------------------------ maintenance
    async def reap_expired_leases(self) -> int:
        """Requeue (or fail and refund) jobs whose worker died mid-attempt."""
        now = self.c.clock()
        query = (
            self.db.collection(GENERATION_JOBS)
            .where(filter=FieldFilter("status", "==", JobStatus.PROCESSING.value))
            .where(filter=FieldFilter("lease_expires_at", "<", now))
        )
        reaped = 0
        for candidate in await query_all(query):
            user_id = str(candidate.get("user_id"))

            async def txn_fn(
                txn: AsyncTransaction, job_id: str = candidate.id, uid: str = user_id
            ) -> bool:
                user = UserDoc.from_doc((await tx_get(txn, self._user_ref(uid))).to_dict() or {})
                job = JobDoc.from_doc((await tx_get(txn, self._job_ref(job_id))).to_dict() or {})
                current = self.c.clock()
                if (
                    job.status != JobStatus.PROCESSING
                    or job.lease_expires_at is None
                    or job.lease_expires_at >= current
                ):
                    return False
                reservation = job.reservation_doc()
                book = CreditBook(self.db, user, current)
                spend = SpendBook(self.db, uid, job.created_at)
                final = job.attempts >= job.max_attempts
                if final:
                    await spend.load(txn)
                # The dead attempt may have reached the provider: count its estimate.
                job.actual_cost_usd_micros += job.estimated_cost_usd_micros
                job.billing_uncertain = True
                job.error_code = "lease_expired"
                if final:
                    self._fail(job, book, spend, reservation)
                    book.write(txn)
                    spend.write(txn)
                    txn.set(self._user_ref(uid), user.to_doc())
                else:
                    self._requeue(job)
                txn.set(self._job_ref(job_id), job.to_doc())
                return True

            if await run_transaction(self.db, txn_fn):
                reaped += 1
        return reaped

    async def purge_expired_uploads(self) -> int:
        """Delete reference uploads past retention that no active job still needs."""
        now = self.c.clock()
        query = (
            self.db.collection(UPLOADS)
            .where(filter=FieldFilter("deleted", "==", False))
            .where(filter=FieldFilter("expires_at", "<", now))
            .limit(200)
        )
        purged = 0
        for snap in await query_all(query):
            users_of = self.db.collection(GENERATION_JOBS).where(
                filter=FieldFilter("input_upload_ids", "array_contains", snap.id)
            )
            if any(
                job.get("status") not in {s.value for s in TERMINAL_STATUSES}
                for job in await query_all(users_of)
            ):
                continue
            await snap.reference.update({"deleted": True, "deleted_at": now})
            await self.c.storage.delete(str(snap.get("storage_key")))
            purged += 1
        return purged

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
                # Leave the job to the reaper: its lease expires and it is retried or refunded.
                logger.exception("job_processing_crashed", extra={"job_id": claim.job_id})
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
