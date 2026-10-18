"""Generation core: users, credits, spend counters, moderation, studio jobs.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("now()")

APPEND_ONLY_FN = """
CREATE FUNCTION reject_append_only_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("firebase_uid", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'pending_deletion', 'deleted')",
            name=op.f("ck_users_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("firebase_uid", name=op.f("uq_users_firebase_uid")),
    )

    op.create_table(
        "credit_accounts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("held", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("held >= 0", name=op.f("ck_credit_accounts_held_non_negative")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_credit_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_credit_accounts")),
    )

    op.create_table(
        "credit_lots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=False),
        sa.Column("amount_granted", sa.Integer(), nullable=False),
        sa.Column("amount_remaining", sa.Integer(), nullable=False),
        sa.Column("expires_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("amount_granted > 0", name=op.f("ck_credit_lots_granted_positive")),
        sa.CheckConstraint(
            "amount_remaining >= 0 AND amount_remaining <= amount_granted",
            name=op.f("ck_credit_lots_remaining_range"),
        ),
        sa.CheckConstraint(
            "source IN ('welcome', 'daily_free', 'reward_ad', 'purchase', 'subscription', "
            "'admin', 'refund')",
            name=op.f("ck_credit_lots_source"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_credit_lots_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_lots")),
        sa.UniqueConstraint(
            "user_id", "source", "source_ref", name=op.f("uq_credit_lots_user_id_source_source_ref")
        ),
    )
    op.create_index(
        "ix_credit_lots_spendable",
        "credit_lots",
        ["user_id", "expires_at"],
        postgresql_where=sa.text("amount_remaining > 0"),
    )

    op.create_table(
        "spend_counters",
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(64), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("cost_usd_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("scope IN ('global', 'user')", name=op.f("ck_spend_counters_scope")),
        sa.CheckConstraint("period IN ('day', 'month')", name=op.f("ck_spend_counters_period")),
        sa.PrimaryKeyConstraint(
            "scope", "scope_id", "period", "period_start", name=op.f("pk_spend_counters")
        ),
    )

    op.create_table(
        "moderation_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.Uuid()),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "subject_type IN ('prompt', 'upload', 'output')",
            name=op.f("ck_moderation_checks_subject_type"),
        ),
        sa.CheckConstraint(
            "decision IN ('allow', 'block')", name=op.f("ck_moderation_checks_decision")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_moderation_checks_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_checks")),
    )
    op.create_index(
        "ix_moderation_checks_user_decision",
        "moderation_checks",
        ["user_id", "decision", "created_at"],
    )

    op.create_table(
        "uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("moderation_status", sa.String(16), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("deleted_at", TS),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_uploads_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uploads")),
    )
    op.create_index("ix_uploads_user_created", "uploads", ["user_id", "created_at"])
    op.create_index(
        "ix_uploads_expires",
        "uploads",
        ["expires_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prompt", sa.Text()),
        sa.Column("style_preset", sa.String(64)),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("aspect", sa.String(16), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("output_format", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("credit_cost", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd_micros", sa.BigInteger(), nullable=False),
        sa.Column("actual_cost_usd_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("billing_uncertain", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TS, nullable=False, server_default=NOW),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", TS),
        sa.Column("error_code", sa.String(64)),
        sa.Column("retry_of_job_id", sa.Uuid()),
        sa.Column("source_image_id", sa.Uuid()),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("deleted_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "kind IN ('generate', 'edit', 'variation')", name=op.f("ck_generation_jobs_kind")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_generation_jobs_status"),
        ),
        sa.CheckConstraint("credit_cost > 0", name=op.f("ck_generation_jobs_credit_cost_positive")),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts",
            name=op.f("ck_generation_jobs_attempts_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_generation_jobs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_generation_jobs_retry_of_job_id_generation_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_jobs")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_generation_jobs_user_id_idempotency_key")
        ),
    )
    op.create_index(
        "ix_generation_jobs_queue",
        "generation_jobs",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_generation_jobs_leases",
        "generation_jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_generation_jobs_user_created", "generation_jobs", ["user_id", "created_at"]
    )

    op.create_table(
        "generated_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("thumb_key", sa.String(300), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("deleted_at", TS),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_generated_images_job_id_generation_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_generated_images_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generated_images")),
    )
    op.create_index(
        "ix_generated_images_user_created", "generated_images", ["user_id", "created_at"]
    )
    op.create_foreign_key(
        op.f("fk_generation_jobs_source_image_id_generated_images"),
        "generation_jobs",
        "generated_images",
        ["source_image_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "generation_job_inputs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_generation_job_inputs_job_id_generation_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["uploads.id"],
            name=op.f("fk_generation_job_inputs_upload_id_uploads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "upload_id", name=op.f("pk_generation_job_inputs")),
    )

    op.create_table(
        "credit_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("resolved_at", TS),
        sa.CheckConstraint("amount > 0", name=op.f("ck_credit_reservations_amount_positive")),
        sa.CheckConstraint(
            "status IN ('held', 'captured', 'released')",
            name=op.f("ck_credit_reservations_status"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name=op.f("fk_credit_reservations_job_id_generation_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_credit_reservations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_reservations")),
        sa.UniqueConstraint("job_id", name=op.f("uq_credit_reservations_job_id")),
    )

    op.create_table(
        "credit_reservation_allocations",
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "amount > 0", name=op.f("ck_credit_reservation_allocations_amount_positive")
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["credit_lots.id"],
            name=op.f("fk_credit_reservation_allocations_lot_id_credit_lots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["credit_reservations.id"],
            name=op.f("fk_credit_reservation_allocations_reservation_id_credit_reservations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "reservation_id", "lot_id", name=op.f("pk_credit_reservation_allocations")
        ),
    )

    op.create_table(
        "credit_ledger",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("held_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_after", sa.Integer(), nullable=False),
        sa.Column("held_after", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Uuid()),
        sa.Column("reservation_id", sa.Uuid()),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("reason", sa.String(200)),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "entry_type IN ('grant', 'reserve', 'capture', 'release', 'expire', 'revoke', "
            "'admin_adjust')",
            name=op.f("ck_credit_ledger_entry_type"),
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_credit_ledger")),
        sa.UniqueConstraint("id", name=op.f("uq_credit_ledger_id")),
    )
    op.create_index("ix_credit_ledger_user_seq", "credit_ledger", ["user_id", "seq"])

    op.execute(APPEND_ONLY_FN)
    op.execute(
        "CREATE TRIGGER credit_ledger_append_only BEFORE UPDATE OR DELETE ON credit_ledger "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS credit_ledger_append_only ON credit_ledger")
    op.execute("DROP FUNCTION IF EXISTS reject_append_only_change()")
    op.drop_table("credit_ledger")
    op.drop_table("credit_reservation_allocations")
    op.drop_table("credit_reservations")
    op.drop_table("generation_job_inputs")
    op.drop_constraint(
        op.f("fk_generation_jobs_source_image_id_generated_images"),
        "generation_jobs",
        type_="foreignkey",
    )
    op.drop_table("generated_images")
    op.drop_table("generation_jobs")
    op.drop_table("uploads")
    op.drop_table("moderation_checks")
    op.drop_table("spend_counters")
    op.drop_table("credit_lots")
    op.drop_table("credit_accounts")
    op.drop_table("users")
