"""Spend caps enforced with transactional counters.

Reservation adds a job's estimated cost to the global day/month and per-user
day counters inside the same transaction that reserves credits; exceeding a
cap raises and the whole transaction is discarded. Reading a counter in a
transaction locks it, so concurrent reservations are serialised per counter
and can never jointly overshoot a cap.

Known scaling limit: the global counters are single documents. That is fine
for early traffic; at sustained high volume they should become sharded
counters with approximate (slightly lagging) cap checks.

Settlement corrects the same counters (keyed by the job's creation time) by
``actual - estimate``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from google.cloud.firestore_v1 import AsyncClient, AsyncDocumentReference, AsyncTransaction

from app.db.firestore import SPEND_COUNTERS, tx_get_all

GLOBAL_ID = "all"


@dataclass(frozen=True)
class SpendLimits:
    global_daily_micros: int
    global_monthly_micros: int
    user_daily_micros: int
    user_daily_jobs: int


class SpendCapError(Exception):
    def __init__(self, scope: str, kind: str) -> None:
        super().__init__(f"{scope} {kind} cap reached")
        self.scope = scope
        self.kind = kind


def usd_to_micros(usd: float) -> int:
    return round(usd * 1_000_000)


def counter_ids(user_id: str, at: datetime) -> tuple[str, str, str]:
    day = at.astimezone(UTC).date()
    month = day.replace(day=1)
    return (
        f"global:{GLOBAL_ID}:day:{day.isoformat()}",
        f"global:{GLOBAL_ID}:month:{month.isoformat()}",
        f"user:{user_id}:day:{day.isoformat()}",
    )


class SpendBook:
    def __init__(self, db: AsyncClient, user_id: str, committed_at: datetime) -> None:
        self._ids = counter_ids(user_id, committed_at)
        self._refs: list[AsyncDocumentReference] = [
            db.collection(SPEND_COUNTERS).document(doc_id) for doc_id in self._ids
        ]
        self.values: dict[str, dict[str, int]] = {}

    async def load(self, txn: AsyncTransaction) -> None:
        for doc_id, snap in zip(self._ids, await tx_get_all(txn, self._refs), strict=True):
            stored = snap.to_dict() if snap.exists else None
            self.values[doc_id] = {
                "cost_usd_micros": int((stored or {}).get("cost_usd_micros", 0)),
                "job_count": int((stored or {}).get("job_count", 0)),
            }

    def _add(self, cost_delta: int, job_delta: int) -> None:
        for value in self.values.values():
            value["cost_usd_micros"] += cost_delta
            value["job_count"] += job_delta

    def commit_estimate(self, estimate_micros: int, limits: SpendLimits) -> None:
        self._add(estimate_micros, 1)
        global_day, global_month, user_day = (self.values[i] for i in self._ids)
        if global_day["cost_usd_micros"] > limits.global_daily_micros:
            raise SpendCapError("global", "daily_cost")
        if global_month["cost_usd_micros"] > limits.global_monthly_micros:
            raise SpendCapError("global", "monthly_cost")
        if user_day["job_count"] > limits.user_daily_jobs:
            raise SpendCapError("user", "daily_jobs")
        if user_day["cost_usd_micros"] > limits.user_daily_micros:
            raise SpendCapError("user", "daily_cost")

    def adjust(self, cost_delta: int, job_delta: int) -> None:
        self._add(cost_delta, job_delta)

    def write(self, txn: AsyncTransaction) -> None:
        for doc_id, ref in zip(self._ids, self._refs, strict=True):
            scope, scope_id, period, start = doc_id.split(":", 3)
            txn.set(
                ref,
                {
                    "scope": scope,
                    "scope_id": scope_id,
                    "period": period,
                    "period_start": start,
                    **self.values[doc_id],
                },
            )
