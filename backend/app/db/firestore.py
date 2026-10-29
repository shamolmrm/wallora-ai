"""Cloud Firestore access helpers.

Server client libraries run transactions with pessimistic locks: every document
read inside a transaction is locked until commit, and Firestore requires all
reads to happen before any write. Domain code therefore follows a
"read everything, decide in memory, then write" shape inside ``run_transaction``.

Collections (document IDs are UUIDv7 strings unless noted):
  users/{user_id}                       profile, credit lots + held, rate/strike windows
  users/{user_id}/credit_ledger/{seq}   append-only, zero-padded sequence IDs
  firebase_uids/{firebase_uid}          -> user_id
  idempotency_keys/{hash(user, key)}    -> job_id + request hash
  generation_jobs, uploads, generated_images, moderation_checks
  spend_counters/{scope:id:period:start}
"""

import asyncio
import os
import random
from collections.abc import Awaitable, Callable
from typing import Any

from google.api_core.exceptions import Aborted
from google.cloud import firestore
from google.cloud.firestore_v1 import AsyncClient, AsyncDocumentReference, AsyncTransaction
from google.cloud.firestore_v1.async_query import AsyncQuery
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from app.core.config import Settings
from app.core.errors import ProblemError

type Snapshot = DocumentSnapshot[AsyncDocumentReference]

USERS = "users"
FIREBASE_UIDS = "firebase_uids"
CREDIT_LEDGER = "credit_ledger"
IDEMPOTENCY_KEYS = "idempotency_keys"
GENERATION_JOBS = "generation_jobs"
UPLOADS = "uploads"
GENERATED_IMAGES = "generated_images"
MODERATION_CHECKS = "moderation_checks"
SPEND_COUNTERS = "spend_counters"
CATEGORIES = "categories"
WALLPAPERS = "wallpapers"

# The client library retries aborted transactions immediately, which livelocks when
# several requests contend on one user document. Keep its own retries short and wrap
# them in jittered exponential backoff.
LIBRARY_ATTEMPTS = 3
CONTENTION_ROUNDS = 10
BACKOFF_BASE_SECONDS = 0.02
BACKOFF_MAX_SECONDS = 1.0
_CONTENTION_MESSAGE = "Failed to commit transaction"


def _is_contention(exc: Exception) -> bool:
    return isinstance(exc, Aborted) or (
        isinstance(exc, ValueError) and str(exc).startswith(_CONTENTION_MESSAGE)
    )


def create_client(settings: Settings) -> AsyncClient:
    # The client library only reads the emulator host from the process environment, so a
    # value that came from backend/.env must be exported explicitly. Settings validation
    # refuses an emulator host outside local/test.
    if settings.firestore_emulator_host:
        os.environ["FIRESTORE_EMULATOR_HOST"] = settings.firestore_emulator_host
    return firestore.AsyncClient(
        project=settings.firestore_project_id or settings.firebase_project_id,
        database=settings.firestore_database,
    )


async def run_transaction[T](db: AsyncClient, fn: Callable[[AsyncTransaction], Awaitable[T]]) -> T:
    """Run ``fn`` in a transaction, retrying on contention. ``fn`` may run several times."""

    @firestore.async_transactional
    async def wrapper(transaction: AsyncTransaction) -> T:
        return await fn(transaction)

    for round_ in range(1, CONTENTION_ROUNDS + 1):
        try:
            return await wrapper(db.transaction(max_attempts=LIBRARY_ATTEMPTS))
        except Exception as exc:
            if not _is_contention(exc):
                raise
            delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2**round_)
            await asyncio.sleep(random.uniform(0, delay))  # noqa: S311 - jitter, not security
    raise ProblemError(
        503, "busy", "The service is busy. Please try again.", headers={"Retry-After": "2"}
    )


# google-cloud-firestore 2.34.1: AsyncTransaction.get(doc_ref) and .get_all() `await` an async
# generator and raise TypeError. The public per-reference/per-query `transaction=` parameters
# work, so these helpers use them instead.


async def tx_get(txn: AsyncTransaction, ref: AsyncDocumentReference) -> Snapshot:
    snapshot: Snapshot = await ref.get(transaction=txn)
    return snapshot


async def tx_get_all(txn: AsyncTransaction, refs: list[AsyncDocumentReference]) -> list[Snapshot]:
    """Snapshots in the same order as ``refs`` (missing documents have ``exists=False``)."""
    return [await tx_get(txn, ref) for ref in refs]


async def tx_query(txn: AsyncTransaction, query: AsyncQuery) -> list[Snapshot]:
    return [snapshot async for snapshot in query.stream(transaction=txn)]


async def query_all(query: AsyncQuery) -> list[Snapshot]:
    return [snapshot async for snapshot in query.stream()]


def data(snapshot: Snapshot) -> dict[str, Any]:
    value = snapshot.to_dict()
    if value is None:
        raise RuntimeError(f"document {snapshot.reference.path} does not exist")
    return value
