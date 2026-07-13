"""Tests for non-destructive job-detail backfill.

Covers :func:`~findjobs.detail_backfill.backfill_job_details` and the
``details-backfill`` CLI command.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Provide an in-memory SQLite session with the full schema."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    from findjobs.models import Base

    Base.metadata.create_all(engine)
    Session_factory = sessionmaker(bind=engine)
    session = Session_factory()

    # Seed minimal reference data (company + source).
    from findjobs.models import Company, Source as SourceModel

    session.add(Company(id=1, slug="test-co", name="Test Co"))
    session.add(
        SourceModel(id=1, company_id=1, slug="test-source", name="Test Source")
    )
    session.commit()
    yield session
    session.close()


def _insert_job(session, **overrides) -> int:
    """Insert a bare-bones Job row and return its id."""
    from findjobs.models import Job, _utcnow

    now = _utcnow()
    vals = dict(
        source_id=1,
        company_id=1,
        title="Engineer",
        description="",
        first_seen_at=now,
        last_seen_at=now,
        responsibilities="",
        requirements="",
        detail_completeness="missing",
    )
    vals.update(overrides)
    job = Job(**vals)
    session.add(job)
    session.flush()
    return job.id


# ===================================================================
# DetailBackfillResult
# ===================================================================


class TestDetailBackfillResult:
    """Verify the dataclass shape and defaults."""

    def test_defaults(self):
        from findjobs.detail_backfill import DetailBackfillResult

        result = DetailBackfillResult()
        assert result.scanned == 0
        assert result.updated == 0
        assert result.applied is False
        assert result.deleted == 0
        assert result.full == 0
        assert result.responsibilities_only == 0
        assert result.requirements_only == 0
        assert result.combined_only == 0
        assert result.missing == 0

    def test_immutable(self):
        from findjobs.detail_backfill import DetailBackfillResult

        result = DetailBackfillResult(scanned=5)
        with pytest.raises(AttributeError):
            result.scanned = 10  # type: ignore[misc]


# ===================================================================
# Dry-run: no mutation
# ===================================================================


class TestDryRunNoMutation:
    """backfill with apply=False must not modify the database."""

    def test_dry_run_does_not_modify_jobs(self, db_session):
        """Existing job rows are unchanged after a dry-run backfill."""
        _insert_job(
            db_session,
            id=1,
            title="JobA",
            description="岗位职责\n写代码\n任职要求\n懂Python",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=False)

        assert result.scanned == 1
        assert result.updated == 1  # would fill both fields
        assert result.applied is False

        # Verify database is untouched
        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == ""
        assert row[1] == ""
        assert row[2] == "missing"

    def test_dry_run_multiple_jobs(self, db_session):
        """Multiple jobs scanned, none mutated."""
        _insert_job(db_session, id=10, title="J1", description="岗位职责\nwork")
        _insert_job(db_session, id=20, title="J2", description="任职要求\nskill")
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=False)

        assert result.scanned == 2
        assert result.updated == 2
        assert result.applied is False

        rows = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements FROM jobs ORDER BY id"
            )
        ).fetchall()
        assert rows[0][0] == ""
        assert rows[0][1] == ""
        assert rows[1][0] == ""
        assert rows[1][1] == ""


# ===================================================================
# Apply: persistence
# ===================================================================


class TestApplyPersistence:
    """backfill with apply=True persists changes to the database."""

    def test_apply_fills_empty_fields(self, db_session):
        """Previously empty resp/req are filled from description headings."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="岗位职责\n编码\n任职要求\n本科",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.scanned == 1
        assert result.updated == 1
        assert result.applied is True

        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "编码"
        assert row[1] == "本科"
        assert row[2] == "full"

    def test_apply_multiple_jobs(self, db_session):
        """All jobs are processed in apply mode."""
        _insert_job(db_session, id=10, title="J1", description="岗位职责\nwork")
        _insert_job(db_session, id=20, title="J2", description="任职要求\nskill")
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.scanned == 2
        assert result.updated == 2

        rows = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs ORDER BY id"
            )
        ).fetchall()
        assert rows[0][0] == "work"
        assert rows[0][1] == ""
        assert rows[0][2] == "responsibilities_only"
        assert rows[1][0] == ""
        assert rows[1][1] == "skill"
        assert rows[1][2] == "requirements_only"


# ===================================================================
# Explicit-field preservation
# ===================================================================


class TestExplicitFieldPreservation:
    """Existing non-empty responsibilities/requirements are never overwritten."""

    def test_explicit_responsibilities_preserved(self, db_session):
        """Non-empty responsibilities survive backfill even if description
        contains a resp heading."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="岗位职责\noverwrite_me\n任职要求\nskill",
            responsibilities="manual_resp",
            detail_completeness="responsibilities_only",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.updated == 1  # requirements got filled
        assert result.full == 1

        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "manual_resp"  # not overwritten
        assert row[1] == "skill"
        assert row[2] == "full"

    def test_explicit_requirements_preserved(self, db_session):
        """Non-empty requirements survive backfill."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="岗位职责\nwork\n任职要求\noverwrite_me",
            requirements="manual_req",
            detail_completeness="requirements_only",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.updated == 1
        assert result.full == 1

        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "work"
        assert row[1] == "manual_req"  # not overwritten
        assert row[2] == "full"

    def test_both_explicit_preserved(self, db_session):
        """When both fields are already set, nothing changes."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="Some description here\n岗位职责\nx\n任职要求\ny",
            responsibilities="existing_resp",
            requirements="existing_req",
            detail_completeness="full",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.updated == 0
        assert result.full == 1

        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "existing_resp"
        assert row[1] == "existing_req"
        assert row[2] == "full"


# ===================================================================
# One-field completion
# ===================================================================


class TestOneFieldCompletion:
    """When one field is empty and the other is explicit, the empty one
    may be filled from description headings."""

    def test_fill_requirements_only(self, db_session):
        """Empty requirements filled from description; explicit resp kept."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="任职要求\n懂Python",
            responsibilities="manual_resp",
            detail_completeness="responsibilities_only",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.updated == 1
        assert result.full == 1

        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "manual_resp"
        assert row[1] == "懂Python"
        assert row[2] == "full"

    def test_fill_responsibilities_only(self, db_session):
        """Empty responsibilities filled from description; explicit req kept."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="岗位职责\n写代码",
            requirements="manual_req",
            detail_completeness="requirements_only",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=True)

        assert result.updated == 1
        assert result.full == 1

        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "写代码"
        assert row[1] == "manual_req"
        assert row[2] == "full"


# ===================================================================
# Description unchanged
# ===================================================================


class TestDescriptionUnchanged:
    """The description field must never be modified by backfill."""

    def test_description_preserved_after_apply(self, db_session):
        """Description is identical before and after backfill."""
        desc = "岗位职责\n写代码\n任职要求\n本科"
        _insert_job(db_session, id=1, title="Dev", description=desc)
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        backfill_job_details(db_session, apply=True)

        row = db_session.execute(
            sa_text("SELECT description FROM jobs WHERE id=1")
        ).fetchone()
        assert row[0] == desc

    def test_description_preserved_dry_run(self, db_session):
        """Description is never even inspected for mutation in dry-run."""
        desc = "岗位职责\n写代码\n任职要求\n本科"
        _insert_job(db_session, id=1, title="Dev", description=desc)
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        backfill_job_details(db_session, apply=False)

        row = db_session.execute(
            sa_text("SELECT description FROM jobs WHERE id=1")
        ).fetchone()
        assert row[0] == desc


# ===================================================================
# Idempotency
# ===================================================================


class TestIdempotency:
    """Running backfill twice must give identical results."""

    def test_apply_twice_same_result(self, db_session):
        """Second apply reports zero updates and preserves data."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="岗位职责\nwork\n任职要求\nskills",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result1 = backfill_job_details(db_session, apply=True)
        result2 = backfill_job_details(db_session, apply=True)

        assert result1.updated >= 1
        assert result2.updated == 0  # nothing changed on second pass
        assert result2.full == 1

        # Verify data is still correct after second pass
        row = db_session.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "work"
        assert row[1] == "skills"
        assert row[2] == "full"

    def test_dry_run_twice_same_counts(self, db_session):
        """Two dry-run passes return identical projected counts."""
        _insert_job(
            db_session,
            id=1,
            title="Dev",
            description="岗位职责\nwork\n任职要求\nskills",
        )
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result1 = backfill_job_details(db_session, apply=False)
        result2 = backfill_job_details(db_session, apply=False)

        assert result1.updated == result2.updated
        assert result1.full == result2.full
        assert result1.scanned == result2.scanned


# ===================================================================
# Distribution keys
# ===================================================================


class TestAllDistributionKeys:
    """All five completeness states are reported correctly."""

    def test_all_five_states(self, db_session):
        """One job for each completeness state after normalization."""
        # full: description with both headings
        _insert_job(
            db_session,
            id=1,
            title="Full",
            description="岗位职责\nwork\n任职要求\nskills",
        )
        # responsibilities_only: only resp heading
        _insert_job(
            db_session,
            id=2,
            title="RespOnly",
            description="岗位职责\njust work",
        )
        # requirements_only: only req heading
        _insert_job(
            db_session,
            id=3,
            title="ReqOnly",
            description="任职要求\njust skills",
        )
        # combined_only: description without headings
        _insert_job(
            db_session,
            id=4,
            title="Combined",
            description="Plain text job ad without sections.",
        )
        # missing: empty everything
        _insert_job(db_session, id=5, title="Missing", description="")
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=False)

        assert result.scanned == 5
        assert result.full == 1
        assert result.responsibilities_only == 1
        assert result.requirements_only == 1
        assert result.combined_only == 1
        assert result.missing == 1
        assert result.updated >= 4  # at least 4 jobs would change

    def test_zero_counts_reported(self, db_session):
        """Empty database reports zero for all distribution keys."""
        from findjobs.detail_backfill import backfill_job_details

        result = backfill_job_details(db_session, apply=False)

        assert result.scanned == 0
        assert result.updated == 0
        assert result.full == 0
        assert result.responsibilities_only == 0
        assert result.requirements_only == 0
        assert result.combined_only == 0
        assert result.missing == 0


# ===================================================================
# Preservation of job / observation / mark counts
# ===================================================================


class TestPreservesCounts:
    """Row counts for jobs, observations, and user marks are never altered."""

    def test_counts_preserved_apply(self, db_session):
        """Apply mode does not change row counts."""
        from findjobs.models import Job, JobObservation, UserMark

        jid = _insert_job(db_session, id=99, title="Dev", description="岗位职责\nwork")
        db_session.add(
            JobObservation(job_id=jid, collect_run_id=None, seen_at=_utcnow())
        )
        db_session.add(UserMark(job_id=jid, mark_type="bookmark"))
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        jobs_before = db_session.query(Job).count()
        obs_before = db_session.query(JobObservation).count()
        marks_before = db_session.query(UserMark).count()

        backfill_job_details(db_session, apply=True)

        assert db_session.query(Job).count() == jobs_before
        assert db_session.query(JobObservation).count() == obs_before
        assert db_session.query(UserMark).count() == marks_before

    def test_counts_preserved_dry_run(self, db_session):
        """Dry-run mode does not change row counts."""
        from findjobs.models import Job, JobObservation, UserMark

        jid = _insert_job(db_session, id=99, title="Dev", description="岗位职责\nwork")
        db_session.add(
            JobObservation(job_id=jid, collect_run_id=None, seen_at=_utcnow())
        )
        db_session.add(UserMark(job_id=jid, mark_type="bookmark"))
        db_session.commit()

        from findjobs.detail_backfill import backfill_job_details

        jobs_before = db_session.query(Job).count()
        obs_before = db_session.query(JobObservation).count()
        marks_before = db_session.query(UserMark).count()

        backfill_job_details(db_session, apply=False)

        assert db_session.query(Job).count() == jobs_before
        assert db_session.query(JobObservation).count() == obs_before
        assert db_session.query(UserMark).count() == marks_before


def _utcnow():
    """Helper for test timestamps."""
    from findjobs.models import _utcnow as _m_utcnow

    return _m_utcnow()


# ===================================================================
# CLI integration
# ===================================================================


class TestCLIDefaultDryRun:
    """The CLI defaults to dry-run mode."""

    def test_default_dry_run_output(self, tmp_path):
        """Default invocation shows preview mode and does not mutate DB."""
        from findjobs.db import init_db
        from typer.testing import CliRunner

        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        _insert_job(
            session,
            id=1,
            title="Dev",
            description="岗位职责\nwork\n任职要求\nskills",
        )
        session.commit()
        session.close()

        runner = CliRunner()
        result = runner.invoke(app, ["details-backfill", "--db-path", str(db_path)])

        assert result.exit_code == 0
        assert "Preview (dry-run)" in result.stdout

        # Verify database not mutated
        session2 = init_db(db_path)
        row = session2.execute(
            sa_text(
                "SELECT responsibilities, requirements FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == ""
        assert row[1] == ""
        session2.close()


class TestCLIApply:
    """The --apply flag persists changes."""

    def test_apply_output_and_persistence(self, tmp_path):
        """CLI with --apply shows applied mode and persists to DB."""
        from findjobs.db import init_db
        from typer.testing import CliRunner

        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        _insert_job(
            session,
            id=1,
            title="Dev",
            description="岗位职责\nwork\n任职要求\nskills",
        )
        session.commit()
        session.close()

        runner = CliRunner()
        result = runner.invoke(
            app, ["details-backfill", "--apply", "--db-path", str(db_path)]
        )

        assert result.exit_code == 0
        assert "Applied" in result.stdout
        assert "scanned=" in result.stdout
        assert "updated=" in result.stdout
        assert "deleted=" in result.stdout
        assert "full=" in result.stdout
        assert "responsibilities_only=" in result.stdout
        assert "requirements_only=" in result.stdout
        assert "combined_only=" in result.stdout
        assert "missing=" in result.stdout

        # Verify database mutated
        session2 = init_db(db_path)
        row = session2.execute(
            sa_text(
                "SELECT responsibilities, requirements FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == "work"
        assert row[1] == "skills"
        session2.close()

    def test_cli_apply_rollback_on_failure(self, tmp_path, monkeypatch):
        """Commit failure after flush rolls back and CLI exits non-zero."""
        from findjobs.db import init_db
        from typer.testing import CliRunner

        from findjobs.cli import app
        from sqlalchemy.orm import Session as SASession

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        _insert_job(
            session,
            id=1,
            title="Dev",
            description="岗位职责\nwork\n任职要求\nskills",
        )
        session.commit()
        session.close()

        # Simulate a commit failure — flush already sent SQL to the
        # connection, but the transaction must roll back.
        def _failing_commit(_self):
            raise RuntimeError("Simulated commit failure")

        monkeypatch.setattr(SASession, "commit", _failing_commit)

        runner = CliRunner()
        result = runner.invoke(
            app, ["details-backfill", "--apply", "--db-path", str(db_path)]
        )

        assert result.exit_code != 0
        # Exception propagated — not swallowed.
        assert result.exception is not None
        assert "Simulated commit failure" in str(result.exception)

        # Re-open and verify the original detail fields are unchanged.
        session2 = init_db(db_path)
        row = session2.execute(
            sa_text(
                "SELECT responsibilities, requirements, detail_completeness "
                "FROM jobs WHERE id=1"
            )
        ).fetchone()
        assert row[0] == ""
        assert row[1] == ""
        assert row[2] == "missing"
        session2.close()

    def test_output_mode_label(self, tmp_path):
        """Output includes the mode and all required counts in order."""
        from findjobs.db import init_db
        from typer.testing import CliRunner

        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        session.close()

        runner = CliRunner()
        result = runner.invoke(
            app, ["details-backfill", "--apply", "--db-path", str(db_path)]
        )

        assert result.exit_code == 0
        assert "Applied details-backfill:" in result.stdout
        # Verify deterministic order of fields in output
        output = result.stdout.strip()
        assert output.index("scanned=") < output.index("updated=")
        assert output.index("updated=") < output.index("deleted=")
        assert output.index("deleted=") < output.index("full=")
        assert output.index("full=") < output.index("responsibilities_only=")
        assert output.index("responsibilities_only=") < output.index(
            "requirements_only="
        )
        assert output.index("requirements_only=") < output.index("combined_only=")
        assert output.index("combined_only=") < output.index("missing=")
