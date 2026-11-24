"""F7: decisões sem posição e avaliação append-only de previsões

Revision ID: 0002_f7_journal
Revises: 0001_f6_initial
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_f7_journal"
down_revision: str | None = "0001_f6_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("position_id", sa.String(length=36), nullable=True),
        sa.Column("client_decision_id", sa.String(length=36), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_type IN ('no_entry', 'entry', 'exit', 'hold', 'watch', 'other')",
            name="ck_decision_type",
        ),
        sa.ForeignKeyConstraint(["position_id"], ["position.id"]),
        sa.UniqueConstraint("position_id", name="uq_decision_position"),
        sa.UniqueConstraint("client_decision_id", name="uq_decision_client_id"),
    )
    op.create_index("decision_session_idx", "decision", ["session", "id"])
    op.create_index("decision_underlying_idx", "decision", ["underlying", "session", "id"])

    op.add_column("prediction", sa.Column("decision_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_prediction_decision",
        "prediction",
        "decision",
        ["decision_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_prediction_one_parent",
        "prediction",
        "(position_id IS NOT NULL) <> (decision_id IS NOT NULL)",
    )
    op.create_index(
        "prediction_one_decision_idx",
        "prediction",
        ["decision_id"],
        unique=True,
        postgresql_where=sa.text("decision_id IS NOT NULL"),
    )

    op.create_table(
        "prediction_evaluation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("prediction_id", sa.String(length=36), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("observed", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("void_reason", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('hit', 'miss', 'void')",
            name="ck_prediction_evaluation_status",
        ),
        sa.CheckConstraint(
            "(status = 'void' AND observed IS NULL AND void_reason IS NOT NULL) "
            "OR (status IN ('hit', 'miss') AND observed IS NOT NULL AND void_reason IS NULL)",
            name="ck_prediction_evaluation_payload",
        ),
        sa.ForeignKeyConstraint(["prediction_id"], ["prediction.id"]),
        sa.UniqueConstraint("prediction_id", "session", name="uq_prediction_evaluation_session"),
    )
    op.create_index(
        "prediction_evaluation_parent_idx",
        "prediction_evaluation",
        ["prediction_id", "session", "id"],
    )

    for table in ("decision", "prediction_evaluation"):
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
    for table in ("decision", "prediction_evaluation"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
    op.drop_table("prediction_evaluation")
    op.drop_index("prediction_one_decision_idx", table_name="prediction")
    op.drop_constraint("ck_prediction_one_parent", "prediction", type_="check")
    op.drop_constraint("fk_prediction_decision", "prediction", type_="foreignkey")
    op.drop_column("prediction", "decision_id")
    op.drop_table("decision")
