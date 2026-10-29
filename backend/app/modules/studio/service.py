"""Studio use cases on Firestore: generation jobs, reference uploads, downloads.

Creation order (see docs/06-generation-and-credits.md):
1. Validate and plan the request (tier, aspect, kind, prompt) - no I/O.
2. Fast idempotency lookup: a replayed key returns the original job.
3. Kill switch, abuse strikes, input ownership.
4. Prompt moderation outside any transaction; failures are closed (503).
5. One transaction that reads the user document (lock), idempotency record,
   inputs, credit lots and spend counters, then decides in memory (rate
   limits, starter grants, spend caps, credit reservation) and writes the job,
   idempotency record, lots, ledger, counters and user. Any failure discards
   every write.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from google.cloud.firestore_v1 import AsyncDocumentReference, AsyncTransaction
from google.cloud.firestore_v1.base_query import FieldFilter

from app.container import Container
from app.core.errors import ProblemError
from app.core.ids import new_id
from app.core.imaging import InvalidImageError, sanitize_upload
from app.db.documents import (
    ImageDoc,
    JobDoc,
    JobKind,
    JobStatus,
    ModerationStatus,
    SubjectType,
    UploadDoc,
    UserDoc,
)
from app.db.firestore import (
    GENERATED_IMAGES,
    GENERATION_JOBS,
    IDEMPOTENCY_KEYS,
    UPLOADS,
    USERS,
    Snapshot,
    query_all,
    run_transaction,
    tx_get,
    tx_get_all,
    tx_query,
)
from app.modules.credits.service import (
    CreditBook,
    InsufficientCreditsError,
)
from app.modules.moderation.service import moderate_prompt, prune, recent_count, record_block
from app.modules.spend.service import SpendBook, SpendCapError, SpendLimits, usd_to_micros
from app.modules.studio.catalog import ASPECTS, TIERS, Aspect, Tier, estimate_cost_micros
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
FIRESTORE_IN_LIMIT = 30
RATE_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class JobPlan:
    kind: JobKind
    prompt: str
    style_preset: str | None
    tier: Tier
    aspect: Aspect
    model: ModelCapabilities
    reference_ids: list[str]
    source_image_id: str | None
    estimate_micros: int
    request_hash: str


def safety_identifier(user_id: str) -> str:
    """Stable pseudonymous ID sent to the provider for abuse attribution."""
    return hashlib.sha256(f"wallora:{user_id}".encode()).hexdigest()[:32]


def upload_key(user_id: str, upload_id: str) -> str:
    return f"u/{user_id}/ref/{upload_id}.png"


def output_keys(user_id: str, job_id: str, image_id: str) -> tuple[str, str]:
    base = f"u/{user_id}/gen/{job_id}/{image_id}"
    return f"{base}.webp", f"{base}_thumb.webp"


def idempotency_doc_id(user_id: str, key: str) -> str:
    return hashlib.sha256(f"{user_id}\x1f{key}".encode()).hexdigest()[:40]


def moderation_unavailable() -> ProblemError:
    return ProblemError(
        503,
        "moderation_unavailable",
        "Safety checks are temporarily unavailable. Please try again.",
        headers={"Retry-After": "30"},
    )


class StudioService:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.s = container.settings
        self.db = container.db

    # ------------------------------------------------------------------ refs
    def _user_ref(self, user_id: str) -> AsyncDocumentReference:
        return self.db.collection(USERS).document(user_id)

    def _job_ref(self, job_id: str) -> AsyncDocumentReference:
        return self.db.collection(GENERATION_JOBS).document(job_id)

    def _upload_ref(self, upload_id: str) -> AsyncDocumentReference:
        return self.db.collection(UPLOADS).document(upload_id)

    def _image_ref(self, image_id: str) -> AsyncDocumentReference:
        return self.db.collection(GENERATED_IMAGES).document(image_id)

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

        refs = [str(r) for r in req.reference_upload_ids]
        source = str(req.source_image_id) if req.source_image_id else None
        if len(set(refs)) != len(refs):
            raise ProblemError(422, "duplicate_reference", "Reference images must be distinct.")
        if req.kind is JobKind.GENERATE and (refs or source):
            raise ProblemError(422, "invalid_inputs", "Text-to-image takes no input images.")
        if req.kind is JobKind.EDIT and (
            source or not 1 <= len(refs) <= self.s.max_reference_images
        ):
            raise ProblemError(
                422,
                "invalid_inputs",
                f"Edits need 1-{self.s.max_reference_images} reference images.",
            )
        if req.kind is JobKind.VARIATION and (refs or source is None):
            raise ProblemError(422, "invalid_inputs", "Variations need exactly one source image.")

        raw_prompt = req.prompt
        if raw_prompt is None or not raw_prompt.strip():
            if req.kind is not JobKind.VARIATION:
                raise ProblemError(422, "prompt_required", "A prompt is required.")
            raw_prompt = DEFAULT_VARIATION_PROMPT
        prompt = normalize_prompt(raw_prompt, max_chars=self.s.prompt_max_chars)

        model = get_model(tier.model if req.kind is JobKind.GENERATE else tier.edit_model)
        input_images = len(refs) + (1 if source else 0)
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
            "references": refs,
            "source_image_id": source,
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
            source_image_id=source,
            estimate_micros=estimate,
            request_hash=request_hash,
        )

    def _limits(self) -> SpendLimits:
        return SpendLimits(
            global_daily_micros=usd_to_micros(self.s.global_daily_cost_cap_usd),
            global_monthly_micros=usd_to_micros(self.s.global_monthly_cost_cap_usd),
            user_daily_micros=usd_to_micros(self.s.user_daily_cost_cap_usd),
            user_daily_jobs=self.s.user_daily_generation_cap,
        )

    # ------------------------------------------------------------------ creation helpers
    async def load_job(self, job_id: str) -> JobDoc | None:
        snap = await self._job_ref(job_id).get()
        return JobDoc.from_doc(snap.to_dict() or {}) if snap.exists else None

    @staticmethod
    def _replay(job: JobDoc, request_hash: str) -> tuple[JobDoc, bool]:
        if job.request_hash != request_hash:
            raise ProblemError(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was already used for a different request.",
            )
        return job, True

    def _input_refs(self, plan: JobPlan) -> list[AsyncDocumentReference]:
        refs = [self._upload_ref(uid) for uid in plan.reference_ids]
        if plan.source_image_id:
            refs.append(self._image_ref(plan.source_image_id))
        return refs

    def _validate_inputs(self, user_id: str, plan: JobPlan, snaps: list[Snapshot]) -> None:
        now = self.c.clock()
        uploads, images = snaps[: len(plan.reference_ids)], snaps[len(plan.reference_ids) :]
        for snap in uploads:
            upload = UploadDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
            if (
                upload is None
                or upload.user_id != user_id
                or upload.deleted
                or upload.expires_at <= now
                or upload.moderation_status != ModerationStatus.PASSED
            ):
                raise ProblemError(
                    422, "invalid_reference", "A reference image is missing or expired."
                )
        for snap in images:
            image = ImageDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
            if image is None or image.user_id != user_id or image.deleted_at is not None:
                raise ProblemError(422, "invalid_source_image", "The source image is unavailable.")

    def _enforce_rate_limits(self, user: UserDoc) -> None:
        now = self.c.clock()
        for window, limit in (
            (timedelta(minutes=1), self.s.user_generations_per_minute),
            (RATE_WINDOW, self.s.user_generations_per_hour),
        ):
            if recent_count(user.recent_job_times, now=now, window=window) >= limit:
                raise ProblemError(
                    429,
                    "rate_limited",
                    "Too many generation requests. Please wait and try again.",
                    headers={"Retry-After": str(int(window.total_seconds()))},
                )

    @staticmethod
    def _spend_problem(exc: SpendCapError) -> ProblemError:
        if exc.scope == "global":
            return ProblemError(
                503,
                "generation_paused",
                "Generation is temporarily unavailable. Please try again later.",
            )
        return ProblemError(
            429,
            "daily_limit_reached",
            "You've reached today's generation limit.",
            headers={"Retry-After": "3600"},
        )

    async def _record_block(self, user_id: str, subject: SubjectType, verdict: Any) -> None:
        async def txn_fn(txn: AsyncTransaction) -> None:
            user = UserDoc.from_doc((await tx_get(txn, self._user_ref(user_id))).to_dict() or {})
            now = self.c.clock()
            record_block(
                self.db,
                txn,
                user=user,
                subject_type=subject,
                subject_id=None,
                verdict=verdict,
                now=now,
            )
            user.strike_times = prune(
                user.strike_times, now=now, keep=timedelta(hours=self.s.abuse_strike_window_hours)
            )
            txn.set(self._user_ref(user_id), user.to_doc())

        await run_transaction(self.db, txn_fn)

    # ------------------------------------------------------------------ creation
    async def create_generation(
        self,
        user: UserDoc,
        idempotency_key: str,
        req: CreateGenerationRequest,
        *,
        retry_of: str | None = None,
    ) -> tuple[JobDoc, bool]:
        """Returns ``(job, replayed)``."""
        plan = self.plan(req)
        idem_ref = self.db.collection(IDEMPOTENCY_KEYS).document(
            idempotency_doc_id(user.id, idempotency_key)
        )

        existing = await idem_ref.get()
        if existing.exists:
            job = await self.load_job(str(existing.get("job_id")))
            if job is not None:
                return self._replay(job, plan.request_hash)
        if not self.s.generation_enabled:
            raise ProblemError(503, "generation_paused", "Generation is temporarily unavailable.")
        strikes = recent_count(
            user.strike_times,
            now=self.c.clock(),
            window=timedelta(hours=self.s.abuse_strike_window_hours),
        )
        if strikes >= self.s.abuse_strike_threshold:
            raise ProblemError(
                403,
                "generation_restricted",
                "Generation is temporarily restricted on this account.",
            )
        input_snaps = [await ref.get() for ref in self._input_refs(plan)]
        self._validate_inputs(user.id, plan, input_snaps)

        try:
            verdict = await moderate_prompt(self.c.moderation, plan.prompt)
        except ModerationUnavailableError:
            raise moderation_unavailable() from None
        if verdict.blocked:
            await self._record_block(user.id, SubjectType.PROMPT, verdict)
            raise ProblemError(
                422,
                "prompt_rejected",
                "This prompt can't be used. Please review the content guidelines.",
            )

        job_id = str(new_id())

        async def txn_fn(txn: AsyncTransaction) -> JobDoc | str:
            now = self.c.clock()
            user_ref = self._user_ref(user.id)
            current = UserDoc.from_doc((await tx_get(txn, user_ref)).to_dict() or {})
            idem = await tx_get(txn, idem_ref)
            if idem.exists:  # same key committed concurrently: replay it
                return str(idem.get("job_id"))
            self._validate_inputs(user.id, plan, await tx_get_all(txn, self._input_refs(plan)))
            book = CreditBook(self.db, current, now)
            spend = SpendBook(self.db, user.id, now)
            await spend.load(txn)

            self._enforce_rate_limits(current)
            book.ensure_starter_grants(
                welcome_credits=self.s.welcome_credits,
                daily_free_credits=self.s.daily_free_credits,
            )
            try:
                spend.commit_estimate(plan.estimate_micros, self._limits())
            except SpendCapError as exc:
                raise self._spend_problem(exc) from None
            try:
                reservation = book.reserve(job_id, plan.tier.credits)
            except InsufficientCreditsError as exc:
                raise ProblemError(
                    402,
                    "insufficient_credits",
                    "You don't have enough credits for this generation.",
                    extra={"required": exc.required, "available": exc.available},
                ) from None

            current.recent_job_times = [
                *prune(current.recent_job_times, now=now, keep=RATE_WINDOW),
                now,
            ]
            current.updated_at = now
            job = JobDoc(
                id=job_id,
                user_id=user.id,
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
                created_at=now,
                updated_at=now,
                reservation=reservation.to_doc(),
                input_upload_ids=plan.reference_ids,
                source_image_id=plan.source_image_id,
                retry_of_job_id=retry_of,
            )
            txn.create(self._job_ref(job_id), job.to_doc())
            txn.create(idem_ref, {"job_id": job_id, "user_id": user.id, "created_at": now})
            book.write(txn)
            spend.write(txn)
            txn.set(user_ref, current.to_doc())
            return job

        result = await run_transaction(self.db, txn_fn)
        if isinstance(result, str):
            replayed = await self.load_job(result)
            assert replayed is not None  # noqa: S101 - the key's job is committed with it
            return self._replay(replayed, plan.request_hash)
        return result, False

    # ------------------------------------------------------------------ queries
    async def get_job(self, user_id: str, job_id: str) -> JobDoc:
        job = await self.load_job(job_id)
        if job is None or job.user_id != user_id or not job.visible:
            raise ProblemError(404, "not_found", "Generation not found.")
        return job

    async def list_jobs(self, user_id: str, *, limit: int, before: str | None) -> list[JobDoc]:
        query = (
            self.db.collection(GENERATION_JOBS)
            .where(filter=FieldFilter("user_id", "==", user_id))
            .where(filter=FieldFilter("visible", "==", True))
            .order_by("created_at", direction="DESCENDING")
            .order_by("id", direction="DESCENDING")
        )
        if before is not None:
            cursor = await self._job_ref(before).get()
            if not cursor.exists or cursor.get("user_id") != user_id:
                raise ProblemError(422, "invalid_cursor", "Invalid pagination cursor.")
            query = query.start_after(cursor)
        snaps = await query_all(query.limit(limit))
        return [JobDoc.from_doc(s.to_dict() or {}) for s in snaps]

    async def images_for(self, job_ids: list[str]) -> dict[str, list[ImageDoc]]:
        result: dict[str, list[ImageDoc]] = {job_id: [] for job_id in job_ids}
        for start in range(0, len(job_ids), FIRESTORE_IN_LIMIT):
            chunk = job_ids[start : start + FIRESTORE_IN_LIMIT]
            query = self.db.collection(GENERATED_IMAGES).where(
                filter=FieldFilter("job_id", "in", chunk)
            )
            for snap in await query_all(query):
                image = ImageDoc.from_doc(snap.to_dict() or {})
                if image.deleted_at is None:
                    result[image.job_id].append(image)
        for images in result.values():
            images.sort(key=lambda image: image.created_at)
        return result

    # ------------------------------------------------------------------ mutations
    async def cancel(self, user_id: str, job_id: str) -> JobDoc:
        async def txn_fn(txn: AsyncTransaction) -> JobDoc:
            user_ref = self._user_ref(user_id)
            user = UserDoc.from_doc((await tx_get(txn, user_ref)).to_dict() or {})
            snap = await tx_get(txn, self._job_ref(job_id))
            job = JobDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
            if job is None or job.user_id != user_id or not job.visible:
                raise ProblemError(404, "not_found", "Generation not found.")
            if job.status != JobStatus.QUEUED:
                raise ProblemError(
                    409, "not_cancellable", "Only queued generations can be cancelled."
                )
            now = self.c.clock()
            reservation = job.reservation_doc()
            book = CreditBook(self.db, user, now)
            spend = SpendBook(self.db, user_id, job.created_at)
            await spend.load(txn)

            book.release(reservation, job.id, "cancelled")
            spend.adjust(job.actual_cost_usd_micros - job.estimated_cost_usd_micros, -1)
            job.status = JobStatus.CANCELLED
            job.finished_at = now
            job.updated_at = now
            job.reservation = reservation.to_doc()
            txn.set(self._job_ref(job_id), job.to_doc())
            book.write(txn)
            spend.write(txn)
            txn.set(user_ref, user.to_doc())
            return job

        return await run_transaction(self.db, txn_fn)

    async def retry(self, user: UserDoc, job_id: str, idempotency_key: str) -> tuple[JobDoc, bool]:
        original = await self.get_job(user.id, job_id)
        if original.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise ProblemError(
                409, "not_retryable", "Only failed or cancelled generations can be retried."
            )
        req = CreateGenerationRequest(
            kind=JobKind(original.kind),
            prompt=original.prompt,
            tier=original.tier,
            aspect=original.aspect,
            style_preset=original.style_preset,
            reference_upload_ids=original.input_upload_ids,
            source_image_id=original.source_image_id,
        )
        return await self.create_generation(user, idempotency_key, req, retry_of=job_id)

    async def delete(self, user_id: str, job_id: str) -> None:
        async def txn_fn(txn: AsyncTransaction) -> list[str]:
            snap = await tx_get(txn, self._job_ref(job_id))
            job = JobDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
            if job is None or job.user_id != user_id or not job.visible:
                raise ProblemError(404, "not_found", "Generation not found.")
            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                raise ProblemError(409, "job_active", "Cancel or wait for the generation first.")
            images_query = self.db.collection(GENERATED_IMAGES).where(
                filter=FieldFilter("job_id", "==", job_id)
            )
            image_snaps = await tx_query(txn, images_query)
            now = self.c.clock()
            keys: list[str] = []
            for image_snap in image_snaps:
                image = ImageDoc.from_doc(image_snap.to_dict() or {})
                if image.deleted_at is None:
                    keys.extend([image.storage_key, image.thumb_key])
                    txn.update(image_snap.reference, {"deleted_at": now})
            # Erase private content; the job stays for credit and spend records.
            txn.update(
                self._job_ref(job_id),
                {"deleted_at": now, "visible": False, "prompt": None, "updated_at": now},
            )
            return keys

        keys = await run_transaction(self.db, txn_fn)
        await asyncio.gather(*(self.c.storage.delete(key) for key in keys))

    # ------------------------------------------------------------------ uploads
    async def create_upload(self, user: UserDoc, data: bytes) -> UploadDoc:
        now = self.c.clock()
        if recent_count(user.recent_upload_times, now=now, window=RATE_WINDOW) >= (
            self.s.uploads_per_hour
        ):
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
            raise moderation_unavailable() from None
        if verdict.blocked:
            await self._record_block(user.id, SubjectType.UPLOAD, verdict)
            raise ProblemError(422, "upload_rejected", "This image can't be used.")

        upload = UploadDoc(
            id=str(new_id()),
            user_id=user.id,
            storage_key="",
            mime_type=image.mime_type,
            size_bytes=len(image.data),
            width=image.width,
            height=image.height,
            sha256=image.sha256,
            moderation_status=ModerationStatus.PASSED,
            expires_at=now + timedelta(days=self.s.reference_retention_days),
            created_at=now,
        )
        upload.storage_key = upload_key(user.id, upload.id)
        await self.c.storage.put(upload.storage_key, image.data, image.mime_type)

        async def txn_fn(txn: AsyncTransaction) -> None:
            user_ref = self._user_ref(user.id)
            current = UserDoc.from_doc((await tx_get(txn, user_ref)).to_dict() or {})
            if recent_count(current.recent_upload_times, now=now, window=RATE_WINDOW) >= (
                self.s.uploads_per_hour
            ):
                raise ProblemError(
                    429, "rate_limited", "Too many uploads.", headers={"Retry-After": "3600"}
                )
            current.recent_upload_times = [
                *prune(current.recent_upload_times, now=now, keep=RATE_WINDOW),
                now,
            ]
            txn.create(self._upload_ref(upload.id), upload.to_doc())
            txn.set(user_ref, current.to_doc())

        try:
            await run_transaction(self.db, txn_fn)
        except Exception:
            await self.c.storage.delete(upload.storage_key)
            raise
        return upload

    async def delete_upload(self, user_id: str, upload_id: str) -> None:
        async def txn_fn(txn: AsyncTransaction) -> str:
            snap = await tx_get(txn, self._upload_ref(upload_id))
            upload = UploadDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
            if upload is None or upload.user_id != user_id or upload.deleted:
                raise ProblemError(404, "not_found", "Upload not found.")
            txn.update(snap.reference, {"deleted": True, "deleted_at": self.c.clock()})
            return upload.storage_key

        key = await run_transaction(self.db, txn_fn)
        await self.c.storage.delete(key)

    async def image_url(self, user_id: str, image_id: str) -> str:
        snap = await self._image_ref(image_id).get()
        image = ImageDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
        if image is None or image.user_id != user_id or image.deleted_at is not None:
            raise ProblemError(404, "not_found", "Image not found.")
        return await self.c.storage.signed_url(image.storage_key, self.s.signed_url_ttl_seconds)
