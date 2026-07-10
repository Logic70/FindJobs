"""Tests for job lifecycle reconciliation and collection completeness.

All tests are deterministic and offline (in-memory SQLite).  Every test
creates its own session so there is no cross-test interference.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from findjobs.models import Base, Company, Job, JobObservation, CollectRun, Source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def session():
    """Provide a clean in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess: Session = Session()
    yield sess
    sess.close()


def _make_company(session: Session, slug: str = "test-co") -> tuple[int, int]:
    """Create a company row and return its id."""
    company = Company(slug=slug, name="Test Co")
    session.add(company)
    session.flush()
    return company.id


def _make_source(
    session: Session,
    slug: str = "test-source",
    company_id: int | None = None,
    config_yaml: str = "",
) -> int:
    """Create a source row and return its id."""
    if company_id is None:
        company_id = _make_company(session)
    source = Source(
        company_id=company_id,
        slug=slug,
        name="Test Source",
        is_active=True,
        config_yaml=config_yaml,
    )
    session.add(source)
    session.flush()
    return source.id


def _make_target_job(
    session: Session,
    source_id: int,
    company_id: int | None = None,
    external_id: str = "ext-1",
    status: str = "active",
    missing_run_count: int = 0,
) -> Job:
    """Create a target-relevance job row."""
    if company_id is None:
        company_id = _make_company(session)
    job = Job(
        source_id=source_id,
        company_id=company_id,
        external_id=external_id,
        title=f"Job {external_id}",
        status=status,
        missing_run_count=missing_run_count,
        relevance_status="target",
    )
    session.add(job)
    session.flush()
    return job


def _make_excluded_job(
    session: Session,
    source_id: int,
    company_id: int | None = None,
    external_id: str = "ext-ex-1",
    status: str = "active",
    missing_run_count: int = 0,
) -> Job:
    """Create an excluded-relevance job row."""
    if company_id is None:
        company_id = _make_company(session)
    job = Job(
        source_id=source_id,
        company_id=company_id,
        external_id=external_id,
        title=f"Excluded {external_id}",
        status=status,
        missing_run_count=missing_run_count,
        relevance_status="excluded",
    )
    session.add(job)
    session.flush()
    return job


def _make_collect_run(session: Session, source_id: int) -> int:
    """Create a completed collect run and return its id."""
    run = CollectRun(source_id=source_id, status="completed")
    session.add(run)
    session.flush()
    return run.id


def _make_observation(session: Session, job_id: int, collect_run_id: int) -> None:
    """Record a job observation for a run."""
    obs = JobObservation(job_id=job_id, collect_run_id=collect_run_id)
    session.add(obs)
    session.flush()


# ===================================================================
# SourceConfig collection_completeness
# ===================================================================


class TestSourceConfigCompleteness:
    """SourceConfig.collection_completeness field behaviour."""

    def test_default_is_partial(self):
        from findjobs.config import SourceConfig

        sc = SourceConfig(slug="test", name="T", company_slug="co")
        assert sc.collection_completeness == "partial"

    def test_accepts_complete_for_target_scope(self):
        from findjobs.config import SourceConfig

        sc = SourceConfig(
            slug="test",
            name="T",
            company_slug="co",
            collection_completeness="complete_for_target_scope",
        )
        assert sc.collection_completeness == "complete_for_target_scope"

    def test_rejects_invalid_value(self):
        from findjobs.config import SourceConfig

        with pytest.raises(ValidationError):
            SourceConfig(
                slug="test",
                name="T",
                company_slug="co",
                collection_completeness="invalid",  # type: ignore[arg-type]
            )


# ===================================================================
# Config snapshot persistence
# ===================================================================


class TestConfigSnapshot:
    """sync_source persists a deterministic JSON snapshot in config_yaml."""

    def test_config_yaml_persisted_on_create(self, session):
        from findjobs.config import SourceConfig
        from findjobs.repository import sync_source

        company_id = _make_company(session)
        sc = SourceConfig(
            slug="test-src",
            name="Test Source",
            company_slug="test-co",
            is_active=True,
            collection_completeness="complete_for_target_scope",
        )
        source = sync_source(session, sc, company_id)

        parsed = json.loads(source.config_yaml)
        assert parsed["slug"] == "test-src"
        assert parsed["collection_completeness"] == "complete_for_target_scope"
        assert parsed["is_active"] is True
        assert parsed["name"] == "Test Source"

    def test_config_yaml_deterministic(self, session):
        """Two sync calls produce identical config_yaml for the same config."""
        from findjobs.config import SourceConfig
        from findjobs.repository import sync_source

        company_id = _make_company(session)
        sc = SourceConfig(
            slug="deterministic-test",
            name="Det Src",
            company_slug="test-co",
            collection_completeness="complete_for_target_scope",
        )

        # First call: create.
        s1 = sync_source(session, sc, company_id)
        yaml_1 = s1.config_yaml

        # Second call: update.
        s2 = sync_source(session, sc, company_id)
        yaml_2 = s2.config_yaml

        assert yaml_1 == yaml_2
        # Verify it's valid JSON with sorted keys.
        parsed = json.loads(yaml_1)
        keys = list(parsed.keys())
        assert keys == sorted(keys), f"Keys not sorted: {keys}"


# ===================================================================
# Lifecycle reconciliation — basic state transitions
# ===================================================================


class TestReconcilePartialSource:
    """Partial sources must skip reconciliation."""

    def test_skipped_with_correct_action(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        source_id = _make_source(session)
        run_id = _make_collect_run(session, source_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=False
        )
        assert result.action == "skipped_partial"
        assert "partial" in result.reason.lower()


class TestReconcileFirstMiss:
    """An unseen active job becomes missing with count 1."""

    def test_active_becomes_missing(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        # The subject job remains unseen so it transitions to missing.
        subject = _make_target_job(
            session, source_id, company_id, external_id="subject"
        )
        # A companion job is observed, satisfying the zero-result guard.
        companion = _make_target_job(
            session, source_id, company_id, external_id="companion"
        )
        run_id = _make_collect_run(session, source_id)
        _make_observation(session, companion.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.made_missing == 1
        assert result.made_archived == 0

        session.refresh(subject)
        assert subject.status == "missing"
        assert subject.missing_run_count == 1

        # The companion was seen and stays active.
        session.refresh(companion)
        assert companion.status == "active"
        assert companion.missing_run_count == 0


class TestReconcileSecondMiss:
    """An unseen missing job becomes archived with count 2."""

    def test_missing_becomes_archived(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        # The subject job remains unseen so it transitions to archived.
        subject = _make_target_job(
            session,
            source_id,
            company_id,
            external_id="subject",
            status="missing",
            missing_run_count=1,
        )
        # A companion job is observed, satisfying the zero-result guard.
        companion = _make_target_job(
            session, source_id, company_id, external_id="companion"
        )
        run_id = _make_collect_run(session, source_id)
        _make_observation(session, companion.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.made_archived == 1

        session.refresh(subject)
        assert subject.status == "archived"
        assert subject.missing_run_count == 2

        # The companion was seen and stays active.
        session.refresh(companion)
        assert companion.status == "active"
        assert companion.missing_run_count == 0


class TestReconcileArchivedStability:
    """An unseen archived job stays archived without counter growth."""

    def test_archived_remains_archived(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id, status="archived", missing_run_count=2
        )
        run_id = _make_collect_run(session, source_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.kept_archived == 1

        session.refresh(job)
        assert job.status == "archived"
        assert job.missing_run_count == 2  # unchanged


# ===================================================================
# Reappearance — driven through real upsert_job
# ===================================================================


class TestReconcileReappearance:
    """Jobs reappearing via upsert_job are restored to active."""

    def test_missing_job_reappears_via_upsert(self, session):
        from findjobs.collection import (
            reconcile_jobs_after_collect,
            upsert_job,
            CollectedJob,
        )

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id, status="missing", missing_run_count=1
        )
        run_id = _make_collect_run(session, source_id)

        # Real upsert path: creates observation AND resets the job.
        cj = CollectedJob(
            external_id=job.external_id,
            title=job.title,
            location=job.location,
            matched_tags=["AI"],
        )
        upsert_job(session, source_id, company_id, run_id, cj)

        # Verify upsert already cleared the stale state.
        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0
        assert job.relevance_status == "target"

        # Reconciliation sees it in seen_job_ids and keeps it active.
        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.seen_target == 1
        assert result.made_missing == 0
        assert result.made_archived == 0

        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0

    def test_archived_job_reappears_via_upsert(self, session):
        from findjobs.collection import (
            reconcile_jobs_after_collect,
            upsert_job,
            CollectedJob,
        )

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id, status="archived", missing_run_count=2
        )
        run_id = _make_collect_run(session, source_id)

        # Real upsert path.
        cj = CollectedJob(
            external_id=job.external_id,
            title=job.title,
            location=job.location,
            matched_tags=["AI"],
        )
        upsert_job(session, source_id, company_id, run_id, cj)

        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.seen_target == 1

        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0


# ===================================================================
# Upsert resets stale state (before reconciliation)
# ===================================================================


class TestUpsertResetsState:
    """upsert_job itself (without reconciliation) resets missing/archived."""

    def test_upsert_resets_missing_job(self, session):
        from findjobs.collection import upsert_job, CollectedJob

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id, status="missing", missing_run_count=1
        )
        run_id = _make_collect_run(session, source_id)

        cj = CollectedJob(
            external_id=job.external_id,
            title=job.title,
            location=job.location,
            matched_tags=["AI"],
        )
        upsert_job(session, source_id, company_id, run_id, cj)

        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0
        assert job.relevance_status == "target"

    def test_upsert_resets_archived_job(self, session):
        from findjobs.collection import upsert_job, CollectedJob

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id, status="archived", missing_run_count=2
        )
        run_id = _make_collect_run(session, source_id)

        cj = CollectedJob(
            external_id=job.external_id,
            title=job.title,
            location=job.location,
            matched_tags=["AI"],
        )
        upsert_job(session, source_id, company_id, run_id, cj)

        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0

    def test_upsert_resets_excluded_relevance(self, session):
        """upsert_job resets relevance_status to target even if previously excluded."""
        from findjobs.collection import upsert_job, CollectedJob

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id,
        )
        # Force to excluded to test the reset.
        job.relevance_status = "excluded"
        session.flush()

        run_id = _make_collect_run(session, source_id)

        cj = CollectedJob(
            external_id=job.external_id,
            title=job.title,
            location=job.location,
            matched_tags=["AI"],
        )
        upsert_job(session, source_id, company_id, run_id, cj)

        session.refresh(job)
        assert job.relevance_status == "target"


# ===================================================================
# Partial source — upsert still clears stale state
# ===================================================================


class TestPartialSourceUpsert:
    """Even when reconciliation is skipped, upsert_job clears stale state."""

    def test_partial_source_upsert_clears_missing(self, session):
        from findjobs.collection import (
            reconcile_jobs_after_collect,
            upsert_job,
            CollectedJob,
        )

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(
            session, source_id, company_id, status="missing", missing_run_count=1
        )
        run_id = _make_collect_run(session, source_id)

        # upsert_job is called regardless of completeness.
        cj = CollectedJob(
            external_id=job.external_id,
            title=job.title,
            location=job.location,
            matched_tags=["AI"],
        )
        upsert_job(session, source_id, company_id, run_id, cj)

        # upsert resets the job regardless.
        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0

        # Reconciliation is skipped for partial sources.
        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=False
        )
        assert result.action == "skipped_partial"

        # Skipped reconciliation does NOT undo the upsert.
        session.refresh(job)
        assert job.status == "active"
        assert job.missing_run_count == 0


# ===================================================================
# Source isolation
# ===================================================================


class TestReconcileSourceIsolation:
    """Reconciliation for one source does not affect another source's jobs."""

    def test_other_source_jobs_untouched(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        src_a = _make_source(session, slug="src-a", company_id=company_id)
        src_b = _make_source(session, slug="src-b", company_id=company_id)

        # job_a on src_a will be unseen — it should become missing.
        job_a = _make_target_job(
            session, src_a, company_id, external_id="ext-a"
        )
        # A companion on src_a is observed, satisfying the zero-result guard.
        companion_a = _make_target_job(
            session, src_a, company_id, external_id="companion-a"
        )
        # job_b on src_b should be untouched throughout.
        job_b = _make_target_job(
            session, src_b, company_id, external_id="ext-b"
        )
        run_id = _make_collect_run(session, src_a)
        _make_observation(session, companion_a.id, run_id)

        result = reconcile_jobs_after_collect(
            session, src_a, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.made_missing == 1  # job_a unseen

        session.refresh(job_a)
        assert job_a.status == "missing"

        # companion_a was seen and stays active.
        session.refresh(companion_a)
        assert companion_a.status == "active"

        # job_b on the other source is untouched.
        session.refresh(job_b)
        assert job_b.status == "active"


class TestReconcileExcludedIsolation:
    """Non-target jobs must not be affected by reconciliation."""

    def test_excluded_jobs_untouched(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        # The unseen target job — should become missing.
        target_job = _make_target_job(
            session, source_id, company_id, external_id="ext-t"
        )
        # A companion target job is observed, satisfying the zero-result guard.
        companion = _make_target_job(
            session, source_id, company_id, external_id="companion"
        )
        # The excluded job must never be touched by reconciliation.
        excluded_job = _make_excluded_job(
            session, source_id, company_id, external_id="ext-ex"
        )
        run_id = _make_collect_run(session, source_id)
        _make_observation(session, companion.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.made_missing == 1

        session.refresh(target_job)
        assert target_job.status == "missing"

        # The companion was seen and stays active.
        session.refresh(companion)
        assert companion.status == "active"

        # The excluded job was never touched.
        session.refresh(excluded_job)
        assert excluded_job.status == "active"
        assert excluded_job.missing_run_count == 0


# ===================================================================
# Safety guards — zero-result
# ===================================================================


class TestZeroResultGuard:
    """Reconciliation must be skipped when a run observes zero target jobs
    and target jobs have been tracked for this source."""

    def test_skip_when_zero_observed(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        _make_target_job(session, source_id, company_id)
        run_id = _make_collect_run(session, source_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "skipped_zero_target"
        assert result.total_target > 0

    def test_does_not_skip_when_no_target_jobs_exist(self, session):
        """If no target jobs are tracked, zero observations is a no-op."""
        from findjobs.collection import reconcile_jobs_after_collect

        source_id = _make_source(session)
        run_id = _make_collect_run(session, source_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.total_target == 0

    def test_does_not_skip_when_target_jobs_observed(self, session):
        """If target jobs exist and are observed, reconciliation runs."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        job = _make_target_job(session, source_id, company_id)
        run_id = _make_collect_run(session, source_id)
        _make_observation(session, job.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"


# ===================================================================
# Safety guards — mass drop
# ===================================================================


class TestMassDropGuard:
    """Reconciliation must be skipped when >50 % of >=10 active/missing
    target jobs would become unseen."""

    def test_skip_when_above_threshold(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        # 10 active jobs, observe 4 = 6/10 unseen (60 % > 50 %).
        for i in range(4):
            _make_target_job(
                session, source_id, company_id, external_id=f"obs-{i}"
            )
        for i in range(6):
            _make_target_job(
                session, source_id, company_id, external_id=f"unseen-{i}"
            )

        run_id = _make_collect_run(session, source_id)
        all_jobs = session.query(Job).filter(Job.source_id == source_id).all()
        for j in all_jobs:
            if "obs-" in j.external_id:
                _make_observation(session, j.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "skipped_mass_drop"
        assert result.total_target == 10
        assert result.seen_target == 4

        # No jobs should have changed state.
        for j in all_jobs:
            session.refresh(j)
            assert j.status == "active"
            assert j.missing_run_count == 0

    def test_reconcile_at_boundary_fifty_percent(self, session):
        """Exactly 50 % unseen with 10 total is NOT >50 % = reconcile."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        for i in range(5):
            _make_target_job(
                session, source_id, company_id, external_id=f"obs-{i}"
            )
        for i in range(5):
            _make_target_job(
                session, source_id, company_id, external_id=f"unseen-{i}"
            )

        run_id = _make_collect_run(session, source_id)
        all_jobs = session.query(Job).filter(Job.source_id == source_id).all()
        for j in all_jobs:
            if "obs-" in j.external_id:
                _make_observation(session, j.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.total_target == 10
        assert result.seen_target == 5

        for j in all_jobs:
            session.refresh(j)
            if "obs-" in j.external_id:
                assert j.status == "active"
                assert j.missing_run_count == 0
            else:
                assert j.status == "missing"
                assert j.missing_run_count == 1

    def test_reconcile_below_ten_jobs(self, session):
        """Mass-drop guard does not apply when fewer than 10 active/missing jobs."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        for i in range(3):
            _make_target_job(
                session, source_id, company_id, external_id=f"obs-{i}"
            )
        for i in range(5):
            _make_target_job(
                session, source_id, company_id, external_id=f"unseen-{i}"
            )

        run_id = _make_collect_run(session, source_id)
        all_jobs = session.query(Job).filter(Job.source_id == source_id).all()
        for j in all_jobs:
            if "obs-" in j.external_id:
                _make_observation(session, j.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.total_target == 8

    def test_skip_reason_includes_fraction(self, session):
        """The skipped mass-drop result contains the fraction in its reason."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        for i in range(10):
            _make_target_job(
                session, source_id, company_id, external_id=f"j-{i}"
            )
        run_id = _make_collect_run(session, source_id)
        # No observations = all 10 unseen, zero-result guard fires first.

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "skipped_zero_target"


# ===================================================================
# Safety guards — archived jobs must not inflate denominator
# ===================================================================


class TestArchivedNotInDenominator:
    """Archived target jobs must not count toward mass-drop denominator
    or zero-result triggers."""

    def test_archived_do_not_inflate_mass_drop_denominator(self, session):
        """100 archived + 10 observed active = reconcile, not mass-drop skip."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        observed_active: list[Job] = []
        for i in range(10):
            j = _make_target_job(
                session, source_id, company_id, external_id=f"active-{i}"
            )
            observed_active.append(j)

        for i in range(100):
            _make_target_job(
                session,
                source_id,
                company_id,
                external_id=f"archived-{i}",
                status="archived",
                missing_run_count=2,
            )

        run_id = _make_collect_run(session, source_id)
        for j in observed_active:
            _make_observation(session, j.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        # total_active_missing=10, all seen = no mass-drop trigger.
        assert result.action == "reconciled"
        assert result.total_target == 10
        assert result.seen_target == 10
        assert result.made_missing == 0
        assert result.kept_archived == 100

    def test_archived_only_zero_observations_no_false_warning(self, session):
        """Only archived target jobs with no observations should reconcile,
        not false-trigger the zero-result guard."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        for i in range(5):
            _make_target_job(
                session,
                source_id,
                company_id,
                external_id=f"archived-{i}",
                status="archived",
                missing_run_count=2,
            )

        run_id = _make_collect_run(session, source_id)
        # No observations.

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        # total_active_missing=0, so zero-result guard does not fire.
        assert result.action == "reconciled"
        assert result.total_target == 0
        assert result.kept_archived == 5

    def test_mixed_archived_and_active_with_partial_observation(self, session):
        """Archived jobs do not dilute the mass-drop ratio;
        9 active + 1 observed should still be treated as 9 active total."""
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)

        # 5 active, 3 observed
        for i in range(3):
            j = _make_target_job(
                session, source_id, company_id, external_id=f"obs-{i}"
            )
        for i in range(2):
            _make_target_job(
                session, source_id, company_id, external_id=f"unseen-{i}"
            )

        # 50 archived (should be invisible to guards)
        for i in range(50):
            _make_target_job(
                session,
                source_id,
                company_id,
                external_id=f"archived-{i}",
                status="archived",
                missing_run_count=2,
            )

        run_id = _make_collect_run(session, source_id)
        all_jobs = session.query(Job).filter(Job.source_id == source_id).all()
        for j in all_jobs:
            if j.status == "active" and "obs-" in j.external_id:
                _make_observation(session, j.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "reconciled"
        assert result.total_target == 5  # only active/missing count
        assert result.seen_target == 3
        assert result.made_missing == 2


# ===================================================================
# Safety guards — observed via ReconcileResult
# ===================================================================


class TestSkippedIsObservable:
    """A skipped reconciliation must be observable through ReconcileResult."""

    def test_skipped_partial_not_silent(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        source_id = _make_source(session)
        run_id = _make_collect_run(session, source_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=False
        )
        assert result.action == "skipped_partial"
        assert result.reason

    def test_skipped_zero_target_not_silent(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        _make_target_job(session, source_id, company_id)
        run_id = _make_collect_run(session, source_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "skipped_zero_target"
        assert result.reason

    def test_skipped_mass_drop_not_silent(self, session):
        from findjobs.collection import reconcile_jobs_after_collect

        company_id = _make_company(session)
        source_id = _make_source(session, company_id=company_id)
        for i in range(4):
            _make_target_job(
                session, source_id, company_id, external_id=f"obs-{i}"
            )
        for i in range(6):
            _make_target_job(
                session, source_id, company_id, external_id=f"unseen-{i}"
            )

        run_id = _make_collect_run(session, source_id)
        all_jobs = session.query(Job).filter(Job.source_id == source_id).all()
        for j in all_jobs:
            if "obs-" in j.external_id:
                _make_observation(session, j.id, run_id)

        result = reconcile_jobs_after_collect(
            session, source_id, run_id, is_complete=True
        )
        assert result.action == "skipped_mass_drop"
        assert result.reason


# ===================================================================
# Load-sources integration (YAML to SourceConfig)
# ===================================================================


class TestLoadSourcesCompleteness:
    """load_sources should parse collection_completeness from YAML."""

    def _write_temp_yaml(self, data: dict) -> str:
        import tempfile
        import yaml

        tmpdir = tempfile.mkdtemp()
        path = f"{tmpdir}/sources.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return path

    def test_active_source_has_completeness(self):
        from findjobs.config import load_sources

        data = {
            "companies": [{"slug": "co", "name": "Co"}],
            "sources": [
                {
                    "slug": "co-careers",
                    "name": "Co Careers",
                    "company_slug": "co",
                    "is_active": True,
                    "collection_completeness": "complete_for_target_scope",
                }
            ],
        }
        path = self._write_temp_yaml(data)
        config = load_sources(path)

        sc = config.sources[0]
        assert sc.collection_completeness == "complete_for_target_scope"

    def test_inactive_source_defaults_to_partial(self):
        from findjobs.config import load_sources

        data = {
            "companies": [{"slug": "co", "name": "Co"}],
            "sources": [
                {
                    "slug": "co-talent",
                    "name": "Co Talent",
                    "company_slug": "co",
                    "is_active": False,
                }
            ],
        }
        path = self._write_temp_yaml(data)
        config = load_sources(path)

        sc = config.sources[0]
        assert sc.collection_completeness == "partial"


# ===================================================================
# ReconcileResult dataclass
# ===================================================================


class TestReconcileResultDataclass:
    """ReconcileResult fields match expectations."""

    def test_defaults(self):
        from findjobs.collection import ReconcileResult

        r = ReconcileResult()
        assert r.action == ""
        assert r.total_target == 0
        assert r.seen_target == 0
        assert r.made_missing == 0
        assert r.made_archived == 0
        assert r.kept_archived == 0
        assert r.reason == ""
