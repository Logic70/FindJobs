"""Create initial (legacy) schema.

Revision ID: 0001
Revises: None
Create Date: 2026-07-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables that match the original Base.metadata.create_all output."""
    # -- companies ----------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True, server_default=""),
        sa.Column("homepage_url", sa.String(500), nullable=True, server_default=""),
        sa.Column("careers_url", sa.String(500), nullable=True, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_companies_slug", "companies", ["slug"], unique=True)

    # -- sources ------------------------------------------------------------
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "source_type",
            sa.String(50),
            nullable=False,
            server_default="official_careers",
        ),
        sa.Column("base_url", sa.String(500), nullable=True, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default="1"),
        sa.Column("config_yaml", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sources_slug", "sources", ["slug"])

    # -- jobs ---------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column(
            "company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False
        ),
        sa.Column("external_id", sa.String(200), nullable=True, server_default=""),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("url", sa.String(500), nullable=True, server_default=""),
        sa.Column("description", sa.Text(), nullable=True, server_default=""),
        sa.Column("salary_text", sa.Text(), nullable=True, server_default=""),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column(
            "salary_currency", sa.String(10), nullable=True, server_default="CNY"
        ),
        sa.Column(
            "salary_period", sa.String(20), nullable=True, server_default="yearly"
        ),
        sa.Column(
            "salary_disclosed", sa.Boolean(), nullable=True, server_default="0"
        ),
        sa.Column("location", sa.String(200), nullable=True, server_default=""),
        sa.Column("job_type", sa.String(50), nullable=True, server_default=""),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True, server_default="active"),
        sa.Column("matched_tags", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])

    # -- collect_runs -------------------------------------------------------
    op.create_table(
        "collect_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="running"
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("jobs_found", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("jobs_new", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("errors", sa.Text(), nullable=True, server_default=""),
    )

    # -- job_observations ---------------------------------------------------
    op.create_table(
        "job_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column(
            "collect_run_id",
            sa.Integer(),
            sa.ForeignKey("collect_runs.id"),
            nullable=True,
        ),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True, server_default=""),
        sa.Column("new_value", sa.Text(), nullable=True, server_default=""),
    )

    # -- user_marks (plain -- no unique constraint yet) ----------------------
    op.create_table(
        "user_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column(
            "mark_type", sa.String(20), nullable=False, server_default="bookmark"
        ),
        sa.Column("note", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Remove all legacy tables."""
    op.drop_table("user_marks")
    op.drop_table("job_observations")
    op.drop_table("collect_runs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_sources_slug", table_name="sources")
    op.drop_table("sources")
    op.drop_index("ix_companies_slug", table_name="companies")
    op.drop_table("companies")
