"""F6: journal inicial append-only em PostgreSQL

Revision ID: 0001_f6_initial
Revises:
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_f6_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "position",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("opened_session", sa.Date(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capital_committed_brl", sa.Float(), nullable=True),
        sa.Column("capital_limit_brl", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("entry_iv_atm_30d", sa.Float(), nullable=True),
        sa.Column("breakeven_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capital_committed_brl IS NULL OR capital_committed_brl >= 0",
            name="ck_position_capital_committed_nonnegative",
        ),
        sa.CheckConstraint(
            "capital_limit_brl IS NULL OR capital_limit_brl >= 0",
            name="ck_position_capital_limit_nonnegative",
        ),
    )
    op.create_index("position_opened_idx", "position", ["opened_session", "id"])

    op.create_table(
        "position_leg",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("position_id", sa.String(length=36), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("maturity", sa.Date(), nullable=True),
        sa.Column("qty", sa.BigInteger(), nullable=False),
        sa.Column("entry_price_brl", sa.Float(), nullable=False),
        sa.Column("entry_strike_brl", sa.Float(), nullable=True),
        sa.Column("entry_iv_pct", sa.Float(), nullable=True),
        sa.Column("entry_delta", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("qty > 0", name="ck_position_leg_qty_positive"),
        sa.CheckConstraint("entry_price_brl >= 0", name="ck_position_leg_price_nonnegative"),
        sa.ForeignKeyConstraint(["position_id"], ["position.id"]),
    )
    op.create_index("position_leg_parent_idx", "position_leg", ["position_id", "id"])

    op.create_table(
        "position_event",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("position_id", sa.String(length=36), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["position_id"], ["position.id"]),
    )
    op.create_index("position_event_parent_idx", "position_event", ["position_id", "id"])
    op.create_index(
        "position_event_one_open_idx",
        "position_event",
        ["position_id"],
        unique=True,
        postgresql_where=sa.text("event = 'open'"),
    )
    op.create_index(
        "position_event_one_close_idx",
        "position_event",
        ["position_id"],
        unique=True,
        postgresql_where=sa.text("event = 'close'"),
    )

    op.create_table(
        "prediction",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("position_id", sa.String(length=36), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("operator", sa.String(length=8), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("horizon_sessions", sa.BigInteger(), nullable=False),
        sa.Column("evaluate_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("observed", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("horizon_sessions > 0", name="ck_prediction_horizon_positive"),
        sa.ForeignKeyConstraint(["position_id"], ["position.id"]),
        sa.UniqueConstraint("position_id", name="uq_prediction_position"),
    )
    op.create_index("prediction_parent_idx", "prediction", ["position_id", "id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION b3o_reject_journal_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in ("position", "position_leg", "position_event", "prediction"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION b3o_reject_journal_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION b3o_reject_journal_mutation()
            """
        )


def downgrade() -> None:
    for table in ("prediction", "position_event", "position_leg", "position"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
    op.drop_table("prediction")
    op.drop_table("position_event")
    op.drop_table("position_leg")
    op.drop_table("position")
    op.execute("DROP FUNCTION IF EXISTS b3o_reject_journal_mutation()")
