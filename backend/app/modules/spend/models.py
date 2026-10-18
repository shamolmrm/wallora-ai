from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SpendCounter(Base):
    """Committed AI spend per scope and period, in micro-USD.

    Estimated cost is added when credits are reserved and corrected to the
    actual cost when the job settles, so concurrent reservations can never push
    spend past a cap.
    """

    __tablename__ = "spend_counters"
    __table_args__ = (
        CheckConstraint("scope IN ('global', 'user')", name="scope"),
        CheckConstraint("period IN ('day', 'month')", name="period"),
    )

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    job_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
