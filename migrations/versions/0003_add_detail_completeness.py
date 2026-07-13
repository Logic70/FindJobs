"""Add detail_completeness column to jobs table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-10 00:00:00.000002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add detail_completeness column to jobs."""
    op.add_column(
        "jobs",
        sa.Column(
            "detail_completeness",
            sa.String(30),
            nullable=False,
            server_default="missing",
        ),
    )


def downgrade() -> None:
    """Remove detail_completeness column from jobs."""
    op.drop_column("jobs", "detail_completeness")
