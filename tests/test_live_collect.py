"""Integration tests for live collection run orchestration.

All tests are deterministic and offline.  Multi-connection visibility tests use
temporary file-backed SQLite databases so that an independent connection can
observe committed state while a collection is in progress.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from findjobs.adapters.registry import register
from findjobs.collection import CollectedJob, ReconcileResult
from findjobs.config import CompanyConfig, SourceConfig, SourcesConfig
from findjobs.models import Base, CollectRun, Company, Job, JobObservation, Source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_db(db_path: str | Path) -> Any:
    from findjobs.db import init_db

    return init_db(db_path)


def _seed_source_with_job(
    db_path: str | Path,
    external_id: str = "ext-1",
    status: str = "active",
    missing_run_count: int = 0,
    relevance_status: str = "target",
) -> dict[str, Any]:
    """Open a session on *db_path*, ensure company+source exist, add job.

    The company and source are created only once per database; subsequent
    calls reuse the existing rows.
    """
    session = _init_db(db_path)
    try:
        company = (
            session.query(Company)
            .filter(Company.slug == "live-test-co")
            .first()
        )
        if company is None:
            company = Company(slug="live-test-co", name="Live Test Co")
            session.add(company)
            session.flush()

        source = (
            session.query(Source)
            .filter(Source.slug == "live-test-source")
            .first()
        )
        if source is None:
            source = Source(
                company_id=company.id,
                slug="live-test-source",
                name="Live Test Source",
                is_active=True,
            )
            session.add(source)
            session.flush()

        job = Job(
            source_id=source.id,
            company_id=company.id,
            external_id=external_id,
            title=f"Job {external_id}",
            status=status,
            missing_run_count=missing_run_count,
            relevance_status=relevance_status,
        )
        session.add(job)
        session.flush()

        session.commit()
        return {
            "company_id": company.id,
            "source_id": source.id,
            "job_id": job.id,
        }
    finally:
        session.close()


def _mock_config(
    adapter_name: str,
    completeness: str = "partial",
) -> SourcesConfig:
    """Build a SourcesConfig that reuses the pre-seeded company/source slugs."""
    return SourcesConfig(
        companies=[CompanyConfig(slug="live-test-co", name="Live Test Co")],
        sources=[
            SourceConfig(
                slug="live-test-source",
                name="Live Test Source",
                company_slug="live-test-co",
                is_active=True,
                adapter=adapter_name,
                collection_completeness=completeness,
            )
        ],
    )


def _run_collect(
    db_path: str | Path,
    config: SourcesConfig,
    echo_list: list[str] | None = None,
) -> None:
    """Invoke _run_live_collect with a patched config and optional echo capture."""
    from findjobs.cli import _run_live_collect

    messages: list[str] = []

    def _echo(msg: str) -> None:
        messages.append(msg)

    with patch("findjobs.cli.load_sources", return_value=config):
        _run_live_collect(str(db_path), _echo)

    if echo_list is not None:
        echo_list.extend(messages)


# ===================================================================
# Test adapters
# ===================================================================


class _EmptyAdapter:
    """Returns no jobs."""

    def collect(self, context: Any) -> list[CollectedJob]:
        return []


class _SingleJobAdapter:
    """Returns one job with the given external_id on each call."""

    def __init__(self, external_id: str = "ext-1"):
        self._external_id = external_id

    def collect(self, context: Any) -> list[CollectedJob]:
        return [
            CollectedJob(
                external_id=self._external_id,
                title=f"Job {self._external_id}",
                location="Shanghai",
                matched_tags=["AI"],
            )
        ]


class _FailingAdapter:
    """Raises RuntimeError on collect()."""

    def collect(self, context: Any) -> list[CollectedJob]:
        msg = "simulated adapter failure"
        raise RuntimeError(msg)


class _BlockingAdapter:
    """Blocks in collect() until *proceed* is set, then returns *results*."""

    def __init__(
        self,
        started: threading.Event,
        proceed: threading.Event,
        results: list[CollectedJob] | None = None,
    ):
        self._started = started
        self._proceed = proceed
        self._results = results or []

    def collect(self, context: Any) -> list[CollectedJob]:
        self._started.set()
        assert self._proceed.wait(timeout=15), "Blocking adapter timed out"
        return self._results


# ===================================================================
# Tests
# ===================================================================


class TestLiveCollectRunningRowVisibility:
    """The running CollectRun row must be visible through an independent DB
    connection while adapter.collect executes."""

    def test_running_row_visible_during_collect(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        started = threading.Event()
        proceed = threading.Event()

        adapter_name = "test_blocking_adapter"
        register(adapter_name, _BlockingAdapter(started, proceed))

        config = _mock_config(adapter_name)
        errors: list[Exception] = []

        def _run() -> None:
            try:
                _run_collect(db_path, config)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=_run)
        t.start()

        assert started.wait(timeout=10), "Adapter never started"

        # Connect to the same file via a different connection and verify the
        # running row exists.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        check_engine = create_engine(f"sqlite:///{db_path}")
        check_session = sessionmaker(bind=check_engine)()
        try:
            runs = check_session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "running"
            assert runs[0].started_at is not None
            assert runs[0].finished_at is None
        finally:
            check_session.close()
            check_engine.dispose()

        proceed.set()
        t.join(timeout=10)

        assert not errors, f"Thread raised: {errors}"

        # After the run completes the same row should be marked completed.
        verify_engine = create_engine(f"sqlite:///{db_path}")
        verify_session = sessionmaker(bind=verify_engine)()
        try:
            runs = verify_session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "completed"
        finally:
            verify_session.close()
            verify_engine.dispose()


class TestLiveCollectSuccess:
    """Successful live collection creates/commits exactly one completed run."""

    def test_success_creates_one_completed_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        adapter_name = "test_empty_adapter"
        register(adapter_name, _EmptyAdapter())

        config = _mock_config(adapter_name)
        _run_collect(db_path, config)

        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "completed"
            assert runs[0].finished_at is not None
        finally:
            session.close()


class TestLiveCollectAdapterFailure:
    """When one source's adapter fails, its run is marked failed and later
    sources are still processed."""

    def test_adapter_failure_fails_same_run_and_continues(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        adapter_fail = "test_adapter_fail"
        adapter_ok = "test_adapter_ok"

        register(adapter_fail, _FailingAdapter())
        register(adapter_ok, _EmptyAdapter())

        config = SourcesConfig(
            companies=[CompanyConfig(slug="live-test-co", name="Live Test Co")],
            sources=[
                SourceConfig(
                    slug="live-failing",
                    name="Failing Source",
                    company_slug="live-test-co",
                    is_active=True,
                    adapter=adapter_fail,
                    collection_completeness="partial",
                ),
                SourceConfig(
                    slug="live-ok",
                    name="OK Source",
                    company_slug="live-test-co",
                    is_active=True,
                    adapter=adapter_ok,
                    collection_completeness="partial",
                ),
            ],
        )
        _run_collect(db_path, config)

        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).order_by(CollectRun.id).all()
            assert len(runs) == 2
            assert runs[0].status == "failed"
            assert "simulated adapter failure" in (runs[0].errors or "")
            assert runs[0].finished_at is not None
            assert runs[1].status == "completed"
            assert runs[1].finished_at is not None
        finally:
            session.close()

    def test_adapter_failure_does_not_create_second_run(self, tmp_path: Path) -> None:
        """Adapter failure after run creation uses the same run row (failed)."""
        db_path = tmp_path / "test.db"
        adapter_name = "test_second_run_adapter"
        register(adapter_name, _FailingAdapter())

        config = _mock_config(adapter_name)
        _run_collect(db_path, config)

        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "failed"
        finally:
            session.close()


class TestLiveCollectReconciliationFailure:
    """A reconciliation exception after the run was committed rolls back
    uncommitted job changes and fails the same run."""

    def test_reconciliation_failure_rolls_back_and_fails_same_run(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"

        # Pre-seed a target job so reconciliation has something to act on.
        _seed_source_with_job(db_path)

        adapter_name = "test_rec_fail_adapter"
        register(adapter_name, _SingleJobAdapter())

        config = _mock_config(adapter_name, completeness="complete_for_target_scope")

        from findjobs.collection import reconcile_jobs_after_collect as real_reconcile

        call_count = 0

        def _failing_reconcile(
            session: Any,
            source_id: int,
            collect_run_id: int,
            is_complete: bool,
        ) -> ReconcileResult:
            nonlocal call_count
            if is_complete and call_count == 0:
                call_count += 1
                msg = "reconciliation crashed"
                raise RuntimeError(msg)
            return real_reconcile(session, source_id, collect_run_id, is_complete)

        with patch(
            "findjobs.collection.reconcile_jobs_after_collect",
            side_effect=_failing_reconcile,
        ):
            _run_collect(db_path, config)

        session = _init_db(str(db_path))
        try:
            # Exactly one run should exist (no second run created).
            runs = session.query(CollectRun).all()
            assert len(runs) == 1, f"Expected 1 run, got {len(runs)}"
            assert runs[0].status == "failed"
            assert "reconciliation crashed" in (runs[0].errors or "")

            # Job changes must be rolled back: no observations committed.
            obs_count = session.query(JobObservation).count()
            assert obs_count == 0, f"Expected 0 observations, got {obs_count}"

            # The pre-seeded job should still be active (unchanged).
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "active"
            assert job.missing_run_count == 0
        finally:
            session.close()


class TestLiveCollectPartialCompleteness:
    """Partial completeness must not change unseen jobs."""

    def test_partial_does_not_change_unseen_jobs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"

        # Pre-seed an active target job.
        _seed_source_with_job(db_path)

        adapter_name = "test_partial_adapter"
        register(adapter_name, _EmptyAdapter())

        config = _mock_config(adapter_name, completeness="partial")
        _run_collect(db_path, config)

        session = _init_db(str(db_path))
        try:
            # Job should still be active because reconciliation was skipped.
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "active"
            assert job.missing_run_count == 0
        finally:
            session.close()


class TestLiveCollectLifecycleTransitions:
    """Full lifecycle transitions driven through live collect runs."""

    def test_two_complete_snapshots_move_job_to_archived(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"

        # Pre-seed two jobs: the subject and a companion that keeps the
        # zero-result guard from firing when the subject goes unseen.
        _seed_source_with_job(db_path, external_id="ext-1", status="active")
        _seed_source_with_job(db_path, external_id="companion", status="active")

        adapter_name = "test_transition_adapter"

        class _TransitionAdapter:
            """Returns both jobs on first call, then only the companion."""

            def __init__(self) -> None:
                self._call = 0

            def collect(self, context: Any) -> list[CollectedJob]:
                self._call += 1
                return [
                    CollectedJob(
                        external_id="ext-1",
                        title="Job ext-1",
                        location="Shanghai",
                        matched_tags=["AI"],
                    ),
                    CollectedJob(
                        external_id="companion",
                        title="Job companion",
                        location="Shanghai",
                        matched_tags=["AI"],
                    ),
                ] if self._call == 1 else [
                    CollectedJob(
                        external_id="companion",
                        title="Job companion",
                        location="Shanghai",
                        matched_tags=["AI"],
                    ),
                ]

        register(adapter_name, _TransitionAdapter())
        config = _mock_config(adapter_name, completeness="complete_for_target_scope")

        # Run 1: both observed -> both active
        _run_collect(db_path, config)
        session = _init_db(str(db_path))
        try:
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "active"
            assert job.missing_run_count == 0
        finally:
            session.close()

        # Run 2: only companion observed -> ext-1 becomes missing
        _run_collect(db_path, config)
        session = _init_db(str(db_path))
        try:
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "missing"
            assert job.missing_run_count == 1
        finally:
            session.close()

        # Run 3: only companion observed -> ext-1 becomes archived
        _run_collect(db_path, config)
        session = _init_db(str(db_path))
        try:
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "archived"
            assert job.missing_run_count == 2
        finally:
            session.close()

    def test_reappearance_restores_active(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"

        # Pre-seed subject and companion so both exist before any run.
        _seed_source_with_job(db_path, external_id="ext-1", status="active")
        _seed_source_with_job(
            db_path, external_id="companion-re", status="active"
        )

        adapter_name = "test_reappear_adapter"

        class _ReappearAdapter:
            def __init__(self) -> None:
                self._call = 0

            def collect(self, context: Any) -> list[CollectedJob]:
                self._call += 1
                if self._call == 1:
                    # Run 1: both observed -> both active
                    return [
                        CollectedJob(
                            external_id="ext-1",
                            title="Job ext-1",
                            location="Shanghai",
                            matched_tags=["AI"],
                        ),
                        CollectedJob(
                            external_id="companion-re",
                            title="Job companion-re",
                            location="Shanghai",
                            matched_tags=["AI"],
                        ),
                    ]
                if self._call == 2:
                    # Run 2: only companion -> ext-1 becomes missing
                    return [
                        CollectedJob(
                            external_id="companion-re",
                            title="Job companion-re",
                            location="Shanghai",
                            matched_tags=["AI"],
                        ),
                    ]
                # Run 3+: both reappear -> ext-1 restored to active
                return [
                    CollectedJob(
                        external_id="ext-1",
                        title="Job ext-1",
                        location="Shanghai",
                        matched_tags=["AI"],
                    ),
                    CollectedJob(
                        external_id="companion-re",
                        title="Job companion-re",
                        location="Shanghai",
                        matched_tags=["AI"],
                    ),
                ]

        register(adapter_name, _ReappearAdapter())
        config = _mock_config(adapter_name, completeness="complete_for_target_scope")

        # Run 1: both active
        _run_collect(db_path, config)

        # Run 2: ext-1 -> missing
        _run_collect(db_path, config)
        session = _init_db(str(db_path))
        try:
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "missing"
            assert job.missing_run_count == 1
        finally:
            session.close()

        # Run 3: ext-1 reappears -> active, missing_run_count=0
        _run_collect(db_path, config)
        session = _init_db(str(db_path))
        try:
            job = session.query(Job).filter(Job.external_id == "ext-1").first()
            assert job is not None
            assert job.status == "active"
            assert job.missing_run_count == 0
        finally:
            session.close()


class TestLiveCollectSkipWarnings:
    """Zero-result and mass-drop skip warnings are persisted and printed."""

    def test_zero_result_skip_warning_persisted_and_printed(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"

        # Pre-seed target jobs so the zero-result guard fires.
        _seed_source_with_job(db_path, external_id="skip-z-1")
        _seed_source_with_job(db_path, external_id="skip-z-2")

        adapter_name = "test_skip_zero_adapter"
        register(adapter_name, _EmptyAdapter())

        config = _mock_config(adapter_name, completeness="complete_for_target_scope")
        echo_out: list[str] = []
        _run_collect(db_path, config, echo_list=echo_out)

        # Check persisted skip warning.
        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "completed"
            assert "lifecycle skipped_zero_target" in (runs[0].errors or "")
        finally:
            session.close()

        # Check printed warning.
        joined = "\n".join(echo_out)
        assert "lifecycle: skipped_zero_target" in joined

    def test_mass_drop_skip_warning_persisted_and_printed(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"

        # Pre-seed 12 active target jobs.
        for i in range(12):
            _seed_source_with_job(db_path, external_id=f"mass-{i}")

        # Adapter returns 4 jobs matching the first 4 pre-seeded jobs.
        adapter_name = "test_mass_drop_adapter"

        class _PartialReturnAdapter:
            def __init__(self) -> None:
                self._returned_jobs = [
                    CollectedJob(
                        external_id=f"mass-{i}",
                        title=f"Job mass-{i}",
                        location="Shanghai",
                        matched_tags=["AI"],
                    )
                    for i in range(4)
                ]

            def collect(self, context: Any) -> list[CollectedJob]:
                return self._returned_jobs

        register(adapter_name, _PartialReturnAdapter())

        config = _mock_config(adapter_name, completeness="complete_for_target_scope")
        echo_out: list[str] = []
        _run_collect(db_path, config, echo_list=echo_out)

        # Check persisted skip warning.
        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "completed"
            assert "lifecycle skipped_mass_drop" in (runs[0].errors or "")
        finally:
            session.close()

        # Check printed warning.
        joined = "\n".join(echo_out)
        assert "lifecycle: skipped_mass_drop" in joined


class TestLiveCollectCliRunner:
    """Public CLI entrypoint integration tested through CliRunner.

    Patches only the config and adapter boundaries; the full Typer command
    chain (option parsing → ``collect --live --db-path`` → ``_run_live_collect``)
    is exercised.
    """

    def test_cli_collect_live_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from typer.testing import CliRunner

        from findjobs.cli import app

        db_path = tmp_path / "test.db"

        config = _mock_config("any_adapter", completeness="complete_for_target_scope")

        def _one_job_adapter(_name: str) -> object:
            class _OneJobAdapter:
                def collect(self, context: object) -> list[CollectedJob]:
                    return [
                        CollectedJob(
                            external_id="CLI-T001",
                            title="Security Engineer",
                            location="Shenzhen",
                            matched_tags=["Security"],
                        ),
                    ]

            return _OneJobAdapter()

        monkeypatch.setattr("findjobs.cli.load_sources", lambda: config)
        monkeypatch.setattr("findjobs.cli.get_adapter", _one_job_adapter)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["collect", "--live", "--db-path", str(db_path)],
        )

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert "collecting..." in result.output
        assert "1 jobs collected, 1 new" in result.output

        # Verify exactly one completed run and one persisted job in the DB.
        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).all()
            assert len(runs) == 1
            assert runs[0].status == "completed"
            assert runs[0].finished_at is not None

            jobs = session.query(Job).all()
            assert len(jobs) == 1
            assert jobs[0].external_id == "CLI-T001"
        finally:
            session.close()


class TestLiveCollectRunCreationFailure:
    """Failure to create the initial running record is reported clearly."""

    def test_run_creation_failure_reported_clearly(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"

        from findjobs.cli import _run_live_collect

        adapter_name = "test_creation_fail_adapter"
        register(adapter_name, _EmptyAdapter())

        config = _mock_config(adapter_name)
        messages: list[str] = []

        def _echo(msg: str) -> None:
            messages.append(msg)

        with patch("findjobs.cli.load_sources", return_value=config), patch(
            "findjobs.collection.create_collect_run",
            side_effect=RuntimeError("cannot create run"),
        ):
            _run_live_collect(str(db_path), _echo)

        joined = "\n".join(messages)
        assert "failed to create collect run" in joined
        assert "cannot create run" in joined

        # No run row should exist.
        session = _init_db(str(db_path))
        try:
            runs = session.query(CollectRun).all()
            assert len(runs) == 0
        finally:
            session.close()
