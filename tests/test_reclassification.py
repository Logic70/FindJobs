"""Phase 1D tests: non-destructive reclassification preview/apply flow.

All tests are deterministic and offline.  Every test creates its own fresh
SQLite database so there is no cross-test state leakage.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findjobs.classify import CLASSIFICATION_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _close_session(session) -> None:
    """Close *session* and dispose its bound engine.

    SQLite holds a file lock while an engine is connected.  On Windows the
    lock prevents ``TemporaryDirectory`` cleanup, so every call to
    :func:`_managed_db` releases the engine before the temp-directory context
    exits.  This function is also safe to call on an already-closed session
    (:meth:`Session.close` is idempotent in SQLAlchemy 2.x).
    """
    session.close()
    if session.bind:
        session.bind.dispose()


@contextmanager
def _managed_db():
    """Yield ``(session, db_path)`` backed by a temporary directory.

    The session is closed and its engine disposed when the context exits,
    ensuring the SQLite file can be removed without error on all platforms.
    Callers that need to release the session earlier (e.g. before running a
    CLI command that opens its own connection) may call
    :func:`_close_session` inside the context — the cleanup in ``finally``
    remains safe to run on an already-closed session.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        from findjobs.db import init_db

        db_path = Path(tmpdir) / "test.db"
        session = init_db(db_path)
        try:
            yield session, db_path
        finally:
            _close_session(session)


def _insert_job(
    session,
    *,
    title: str = "AI Engineer",
    description: str = "",
    job_type: str = "",
    location: str = "",
    relevance_status: str = "target",
    matched_tags: str | None = None,
    external_id: str = "ext-1",
    url: str = "https://example.com/job/1",
    status: str = "active",
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    source_id: int = 1,
    company_id: int = 1,
    classification_version: str = "",
    classification_reasons: str = "[]",
) -> int:
    """Insert a minimal job row and return its id."""
    from findjobs.models import Job

    now = _utcnow()
    tags = matched_tags if matched_tags is not None else json.dumps(["AI"], ensure_ascii=False)
    job = Job(
        source_id=source_id,
        company_id=company_id,
        external_id=external_id,
        title=title,
        url=url,
        description=description,
        relevance_status=relevance_status,
        matched_tags=tags,
        classification_version=classification_version,
        classification_reasons=classification_reasons,
        location=location,
        job_type=job_type,
        status=status,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
    )
    session.add(job)
    session.flush()
    return job.id  # type: ignore[return-value]


def _insert_user_mark(session, job_id: int, mark_type: str = "bookmark", note: str = "") -> int:
    """Add a user mark for *job_id* and return its id."""
    from findjobs.models import UserMark

    m = UserMark(job_id=job_id, mark_type=mark_type, note=note)
    session.add(m)
    session.flush()
    return m.id  # type: ignore[return-value]


def _insert_observation(session, job_id: int) -> int:
    """Add a job observation for *job_id* and return its id."""
    from findjobs.models import JobObservation

    o = JobObservation(job_id=job_id)
    session.add(o)
    session.flush()
    return o.id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Preview (dry-run) — no mutations
# ---------------------------------------------------------------------------


class TestPreviewNoop:
    """Preview mode must compute correct counts without mutating ORM objects."""

    def test_preview_does_not_change_database(self):
        """Preview must not alter any column value."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            # Seed a company + source so FK constraints are satisfied.
            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="Selling products to customers",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            # Run preview
            result = reclassify_jobs(session, apply=False)

            # Preview should detect that this job is no longer relevant
            assert result.scanned == 1
            assert result.excluded == 1
            assert result.restored == 0
            assert result.applied is False
            assert result.deleted == 0

            # Row must NOT have been mutated
            from findjobs.models import Job

            session.expire_all()
            job = session.get(Job, jid)
            assert job.relevance_status == "target"  # unchanged
            assert json.loads(job.matched_tags) == ["AI"]  # unchanged

    def test_preview_returns_same_counts_as_apply(self):
        """Preview and apply should agree on counts for the same data."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="AI Engineer",
                description="Building LLM models",
                matched_tags=json.dumps(["AI", "AI Security"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            preview = reclassify_jobs(session, apply=False)
            # Roll back any side-effects of the preview (there shouldn't be any)
            session.rollback()

            applied = reclassify_jobs(session, apply=True)
            session.rollback()

            assert preview.scanned == applied.scanned
            assert preview.updated == applied.updated
            assert preview.excluded == applied.excluded
            assert preview.restored == applied.restored
            assert preview.normalized == applied.normalized
            assert preview.deleted == applied.deleted == 0

    def test_preview_no_changes_when_nothing_to_do(self):
        """When all jobs already match classifier output, preview shows zeros."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="AI Engineer",
                description="Working on LLMs",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                location="北京",
                job_type="技术",
                classification_version=CLASSIFICATION_VERSION,
                classification_reasons=json.dumps(
                    ["ai_surface_signals"], ensure_ascii=False
                ),
            )
            session.commit()

            result = reclassify_jobs(session, apply=False)
            assert result.scanned == 1
            assert result.updated == 0
            assert result.excluded == 0
            assert result.restored == 0
            assert result.moved_to_review == 0
            assert result.normalized == 0
            assert result.deleted == 0


# ---------------------------------------------------------------------------
# Apply — exclusion preserves related rows
# ---------------------------------------------------------------------------


class TestApplyPreservation:
    """Apply must never delete jobs, observations, or user marks."""

    def test_excluded_job_still_exists(self):
        """A job that becomes excluded must still be queryable after apply."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            result = reclassify_jobs(session, apply=True)
            session.commit()

            assert result.excluded == 1
            assert result.deleted == 0

            from findjobs.models import Job

            job = session.get(Job, jid)
            assert job is not None, "Job was deleted!"  # must still exist
            assert job.relevance_status == "excluded"

    def test_observations_preserved_after_exclusion(self):
        """JobObservation rows must survive reclassification."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source, JobObservation

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            obs_id = _insert_observation(session, jid)
            session.commit()

            reclassify_jobs(session, apply=True)
            session.commit()

            obs = session.get(JobObservation, obs_id)
            assert obs is not None, "Observation was deleted!"
            assert obs.job_id == jid

    def test_user_marks_preserved_after_exclusion(self):
        """UserMark rows (bookmarks, applies, ignores) must survive."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source, UserMark

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            _insert_user_mark(session, jid, mark_type="bookmark", note="looks interesting")
            _insert_user_mark(session, jid, mark_type="applied", note="applied on 2026-07-01")
            session.commit()

            reclassify_jobs(session, apply=True)
            session.commit()

            marks = session.query(UserMark).filter(UserMark.job_id == jid).all()
            assert len(marks) == 2

    def test_url_and_timestamps_preserved(self):
        """Official URL, first_seen_at, last_seen_at must survive reclassify."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            first_seen = datetime(2026, 1, 15, 8, 0, 0)
            last_seen = datetime(2026, 6, 20, 18, 30, 0)

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                url="https://example.com/jobs/unique-123",
                first_seen_at=first_seen,
                last_seen_at=last_seen,
            )
            session.commit()

            reclassify_jobs(session, apply=True)
            session.commit()

            from findjobs.models import Job

            job = session.get(Job, jid)
            assert job.url == "https://example.com/jobs/unique-123"
            assert job.first_seen_at == first_seen
            assert job.last_seen_at == last_seen


# ---------------------------------------------------------------------------
# Restoration — excluded → target
# ---------------------------------------------------------------------------


class TestRestoration:
    """Jobs previously marked excluded must be restored when now relevant."""

    def test_excluded_job_restored_when_now_relevant(self):
        """A job with relevance_status=excluded that matches current rules
        should get relevance_status=target after apply."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            # A job currently marked excluded but with clearly AI-relevant content
            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="AI Engineer",
                description="Building LLM inference systems",
                matched_tags=json.dumps([], ensure_ascii=False),
                relevance_status="excluded",
            )
            session.commit()

            result = reclassify_jobs(session, apply=True)
            session.commit()

            assert result.restored == 1
            assert result.excluded == 0

            from findjobs.models import Job

            job = session.get(Job, jid)
            assert job.relevance_status == "target"
            assert "AI" in json.loads(job.matched_tags)

    def test_no_restoration_for_still_irrelevant(self):
        """An excluded job that remains irrelevant should stay excluded."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="Selling products",
                matched_tags=json.dumps([], ensure_ascii=False),
                relevance_status="excluded",
            )
            session.commit()

            result = reclassify_jobs(session, apply=True)
            session.commit()

            assert result.restored == 0
            assert result.excluded == 0  # already excluded, no transition

            from findjobs.models import Job

            job = session.get(Job, jid)
            assert job.relevance_status == "excluded"


# ---------------------------------------------------------------------------
# Normalization — preview vs apply
# ---------------------------------------------------------------------------


class TestNormalization:
    """Location and job_type normalization must only mutate during apply."""

    def test_normalization_preview_counts_only(self):
        """Preview should count normalizations without mutating."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="AI Engineer",
                description="LLMs",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                location="北京市",
                job_type="技术类",
            )
            session.commit()

            result = reclassify_jobs(session, apply=False)

            assert result.normalized == 2  # location + job_type both need fixing
            assert result.updated >= 2

            # Must NOT have mutated
            session.expire_all()
            from findjobs.models import Job

            job = session.get(Job, jid)
            assert job.location == "北京市"  # unchanged
            assert job.job_type == "技术类"  # unchanged

    def test_normalization_applied(self):
        """Apply should persist normalized values."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):

            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            jid = _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="AI Engineer",
                description="LLMs",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                location="北京市",
                job_type="技术类",
            )
            session.commit()

            result = reclassify_jobs(session, apply=True)
            session.commit()

            assert result.normalized == 2

            from findjobs.models import Job

            job = session.get(Job, jid)
            assert job.location == "北京"
            assert job.job_type == "技术"


# ---------------------------------------------------------------------------
# CLI — dry-run / apply
# ---------------------------------------------------------------------------


class TestCliPrune:
    """CLI ``prune`` command must default to dry-run."""

    def test_prune_default_is_dry_run(self):
        """Running ``prune`` without ``--apply`` must show preview."""
        with _managed_db() as (session, db_path):
            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            # Release the managed engine so the CLI can open its own connection.
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["prune", "--db-path", str(db_path)])
            assert result.exit_code == 0
            assert "Preview (dry-run)" in result.output
            assert "excluded=1" in result.output

            # Verify DB was NOT changed — open a fresh disposable session.
            from findjobs.db import init_db
            from findjobs.models import Job

            session2 = init_db(db_path)
            try:
                job = session2.query(Job).first()
                assert job.relevance_status == "target"  # unchanged
            finally:
                _close_session(session2)

    def test_prune_apply_persists_changes(self):
        """Running ``prune --apply`` must persist reclassification."""
        with _managed_db() as (session, db_path):
            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            # Release the managed engine so the CLI can open its own connection.
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["prune", "--db-path", str(db_path), "--apply"])
            assert result.exit_code == 0
            assert "Applied" in result.output
            assert "excluded=1" in result.output

            # Verify DB was changed — open a fresh disposable session.
            from findjobs.db import init_db
            from findjobs.models import Job

            session2 = init_db(db_path)
            try:
                jobs = session2.query(Job).all()
                assert len(jobs) == 1
                assert jobs[0].relevance_status == "excluded"
            finally:
                _close_session(session2)

    def test_prune_dry_run_shows_deleted_zero(self):
        """Output must always show deleted=0."""
        with _managed_db() as (session, db_path):
            # Release the managed engine so the CLI can open its own connection.
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["prune", "--db-path", str(db_path)])
            assert result.exit_code == 0
            assert "deleted=0" in result.output


# ---------------------------------------------------------------------------
# Deleted count is always zero
# ---------------------------------------------------------------------------


class TestDeletedAlwaysZero:
    """The ``deleted`` field must be zero in every result."""

    def test_deleted_zero_preview(self):
        """Preview must report deleted=0."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):
            result = reclassify_jobs(session, apply=False)
            assert result.deleted == 0

    def test_deleted_zero_apply(self):
        """Apply must report deleted=0."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):
            result = reclassify_jobs(session, apply=True)
            assert result.deleted == 0

    def test_deleted_zero_legacy_wrapper(self):
        """The backward-compatible wrapper must report deleted=0."""
        from findjobs.maintenance import reclassify_and_prune_irrelevant_jobs

        with _managed_db() as (session, _):
            result = reclassify_and_prune_irrelevant_jobs(session)
            assert result.deleted == 0


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------


class TestLegacyCompatibility:
    """``reclassify_and_prune_irrelevant_jobs`` must still work and never delete."""

    def test_legacy_function_available_and_never_deletes(self):
        """The backward-compatible wrapper must return a RelevancePruneResult
        with deleted=0 and the correct scanned/updated counts."""
        from findjobs.maintenance import (
            RelevancePruneResult,
            reclassify_and_prune_irrelevant_jobs,
        )

        with _managed_db() as (session, _):
            from findjobs.models import Company, Source

            c = Company(slug="acme", name="Acme Inc.")
            session.add(c)
            session.flush()
            s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
            session.add(s)
            session.flush()

            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="Sales Representative",
                description="",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            _insert_job(
                session,
                source_id=s.id,
                company_id=c.id,
                title="AI Engineer",
                description="LLMs",
                matched_tags=json.dumps(["AI", "AI Security"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            result = reclassify_and_prune_irrelevant_jobs(session)
            session.commit()

            assert isinstance(result, RelevancePruneResult)
            assert result.scanned == 2
            assert result.deleted == 0

            # The sales job should now be excluded (still exists)
            from findjobs.models import Job

            sales = (
                session.query(Job)
                .filter(Job.external_id == "ext-1")
                .first()
            )
            assert sales is not None
            assert sales.relevance_status == "excluded"
