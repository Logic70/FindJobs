"""Add Phase-1 columns to jobs and unique constraint on user_marks.

Before adding the constraint, duplicate user_marks rows are merged
deterministically:
  - keep the row with the lowest id;
  - preserve a non-empty note from the most recently updated duplicate;
  - keep the earliest created_at time;
  - keep the latest updated_at time.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10 00:00:00.000001
"""
from typing import Sequence, Union

from datetime import datetime

from sqlalchemy import text

from alembic import op
import sqlalchemy as sa


# Sentinel used when comparing nullable timestamps; any real datetime
# will be larger so that a recorded time always beats an unknown one.
_EPOCH = datetime(1970, 1, 1, 0, 0, 0)


def _dt_or_sentinel(val: object) -> datetime:
    """Return *val* when it is a :class:`datetime`, or the sentinel otherwise."""
    return val if isinstance(val, datetime) else _EPOCH

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _merge_duplicate_user_marks(conn) -> None:
    """Merge duplicate (job_id, mark_type) rows in user_marks.

    Operates in-place on the open connection.  No commit is performed so
    that the whole migration stays atomic.
    """
    # Identify groups that have more than one row.
    dup_groups = conn.execute(
        text(
            """
            SELECT job_id, mark_type
            FROM user_marks
            GROUP BY job_id, mark_type
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    if not dup_groups:
        return

    for job_id, mark_type in dup_groups:
        rows = conn.execute(
            text(
                """
                SELECT id, note, created_at, updated_at
                FROM user_marks
                WHERE job_id = :job_id AND mark_type = :mark_type
                ORDER BY id ASC
                """
            ),
            {"job_id": job_id, "mark_type": mark_type},
        ).fetchall()

        # Keep the row with the lowest id.
        keep_id = rows[0][0]

        # Earliest created_at -- filter out None values.
        created_values = [r[2] for r in rows if r[2] is not None]
        earliest_created = min(created_values) if created_values else None

        # Latest updated_at -- filter out None values.
        updated_values = [r[3] for r in rows if r[3] is not None]
        latest_updated = max(updated_values) if updated_values else None

        # Non-empty note from the most recently updated duplicate.
        # Tiebreaker: when two rows have the same updated_at (or both
        # are None), the row with the higher id wins.
        candidates = [r for r in rows if r[1] and r[1].strip()]
        best_note = ""
        if candidates:
            best_note = max(
                candidates,
                key=lambda r: (_dt_or_sentinel(r[3]), r[0]),
            )[1]

        # Delete all rows except the keeper.
        delete_ids = [r[0] for r in rows if r[0] != keep_id]
        for did in delete_ids:
            conn.execute(
                text("DELETE FROM user_marks WHERE id = :id"),
                {"id": did},
            )

        # Update the keeper with merged values.
        conn.execute(
            text(
                """
                UPDATE user_marks
                SET note = :note,
                    created_at = :created_at,
                    updated_at = :updated_at
                WHERE id = :id
                """
            ),
            {
                "id": keep_id,
                "note": best_note,
                "created_at": earliest_created,
                "updated_at": latest_updated,
            },
        )


def upgrade() -> None:
    """Add new columns to jobs and a unique constraint on user_marks."""
    conn = op.get_bind()

    # -- jobs additions -----------------------------------------------------
    op.add_column(
        "jobs",
        sa.Column(
            "relevance_status",
            sa.String(20),
            nullable=False,
            server_default="target",
        ),
    )
    op.create_index("ix_jobs_relevance_status", "jobs", ["relevance_status"])

    op.add_column(
        "jobs",
        sa.Column(
            "missing_run_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_version",
            sa.String(50),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_reasons",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "responsibilities",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "requirements",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )

    # -- user_marks deduplication + constraint ------------------------------
    _merge_duplicate_user_marks(conn)

    with op.batch_alter_table("user_marks", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "uq_user_marks_job_mark", ["job_id", "mark_type"]
        )


def downgrade() -> None:
    """Remove Phase-1 additions."""
    with op.batch_alter_table("user_marks", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_user_marks_job_mark", type_="unique")

    op.drop_column("jobs", "requirements")
    op.drop_column("jobs", "responsibilities")
    op.drop_column("jobs", "classification_reasons")
    op.drop_column("jobs", "classification_version")
    op.drop_column("jobs", "missing_run_count")
    op.drop_index("ix_jobs_relevance_status", table_name="jobs")
    op.drop_column("jobs", "relevance_status")
