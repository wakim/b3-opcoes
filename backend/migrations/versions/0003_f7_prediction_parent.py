"""F7: permite previsão cujo pai é uma decisão sem posição

Revision ID: 0003_f7_prediction_parent
Revises: 0002_f7_journal
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_f7_prediction_parent"
down_revision: str | None = "0002_f7_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "prediction",
        "position_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "prediction",
        "position_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
