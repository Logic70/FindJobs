"""Phase 6 tests: CLI collect --live integration (deterministic, offline).

These tests verify that the live collection path:
- Persists jobs and creates CollectRun records
- Does not duplicate jobs on repeated runs
- Reports per-source counts including new_count
- Records a failed CollectRun when the adapter raises

All adapter calls are monkeypatched — no network I/O.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findjobs.adapters.base import BaseAdapter
from findjobs.collection import CollectedJob


class FakeTencentAdapter(BaseAdapter):
    """Returns predetermined jobs without network calls."""

    def __init__(self) -> None:
        self.jobs = [
            CollectedJob(
                external_id="LIVE-T001",
                title="Security Engineer",
                url="https://careers.tencent.com/job/LIVE-T001",
                location="Shenzhen",
                job_type="技术类",
                matched_tags=["Security"],
            ),
            CollectedJob(
                external_id="LIVE-T002",
                title="AI Engineer",
                url="https://careers.tencent.com/job/LIVE-T002",
                location="Beijing",
                job_type="技术类",
                matched_tags=["AI"],
            ),
        ]

    def collect(self, context):  # type: ignore[override]
        return list(self.jobs)  # return a copy


class FakeFailingAdapter(BaseAdapter):
    """An adapter that always raises during collection."""

    def collect(self, context):  # type: ignore[override]
        raise RuntimeError("API unavailable")


def _make_test_config() -> object:
    """Create a minimal SourcesConfig with one active Tencent source."""
    from findjobs.config import CompanyConfig, SourceConfig, SourcesConfig

    return SourcesConfig(
        companies=[CompanyConfig(slug="tencent", name="Tencent")],
        sources=[
            SourceConfig(
                slug="tencent-careers",
                name="Tencent Careers",
                company_slug="tencent",
                base_url="https://careers.tencent.com",
                fetch_url="https://careers.tencent.com/test-endpoint",
                adapter="tencent_official",
                is_active=True,
            )
        ],
    )


def _make_two_source_config() -> object:
    """Config with a working Tencent source and a failing second source."""
    from findjobs.config import CompanyConfig, SourceConfig, SourcesConfig

    return SourcesConfig(
        companies=[
            CompanyConfig(slug="tencent", name="Tencent"),
            CompanyConfig(slug="badcorp", name="Bad Corp"),
        ],
        sources=[
            SourceConfig(
                slug="tencent-careers",
                name="Tencent Careers",
                company_slug="tencent",
                base_url="https://careers.tencent.com",
                fetch_url="https://careers.tencent.com/test-endpoint",
                adapter="tencent_official",
                is_active=True,
            ),
            SourceConfig(
                slug="badcorp-careers",
                name="Bad Corp Careers",
                company_slug="badcorp",
                base_url="https://badcorp.example.com",
                fetch_url="https://badcorp.example.com/api",
                adapter="tencent_official",
                is_active=True,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCliLiveCollect:
    """CLI collect --live integration."""

    def test_live_collect_persists_jobs_and_creates_run(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """First --live run should persist jobs and create a completed CollectRun."""
        from findjobs.cli import app

        monkeypatch.setattr("findjobs.cli.load_sources", _make_test_config)
        monkeypatch.setattr(
            "findjobs.cli.get_adapter",
            lambda _name: FakeTencentAdapter(),
        )

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            result = runner.invoke(
                app, ["collect", "--live", "--db-path", str(db_path)]
            )
            assert result.exit_code == 0, f"CLI failed: {result.output}"
            assert "2 jobs collected, 2 new" in result.output

            # Verify DB contents.
            from findjobs.db import init_db
            from findjobs.models import CollectRun, Job

            session = init_db(db_path)
            try:
                jobs = session.query(Job).all()
                assert len(jobs) == 2

                runs = session.query(CollectRun).all()
                assert len(runs) == 1
                assert runs[0].status == "completed"
                assert runs[0].jobs_found == 2
                assert runs[0].jobs_new == 2
            finally:
                session.close()

    def test_repeated_live_collect_does_not_duplicate(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Second --live run with same data should not duplicate jobs."""
        from findjobs.cli import app

        monkeypatch.setattr("findjobs.cli.load_sources", _make_test_config)
        monkeypatch.setattr(
            "findjobs.cli.get_adapter",
            lambda _name: FakeTencentAdapter(),
        )

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            # First run.
            r1 = runner.invoke(
                app, ["collect", "--live", "--db-path", str(db_path)]
            )
            assert r1.exit_code == 0
            assert "2 new" in r1.output

            # Second run (same data).
            r2 = runner.invoke(
                app, ["collect", "--live", "--db-path", str(db_path)]
            )
            assert r2.exit_code == 0
            assert "0 new" in r2.output

            # Verify DB.
            from findjobs.db import init_db
            from findjobs.models import CollectRun, Job

            session = init_db(db_path)
            try:
                jobs = session.query(Job).all()
                assert len(jobs) == 2  # not duplicated

                runs = session.query(CollectRun).all()
                assert len(runs) == 2  # two runs
            finally:
                session.close()

    def test_failed_source_records_failed_collect_run(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """When an adapter raises, a CollectRun with status=failed is recorded."""
        from findjobs.cli import app

        monkeypatch.setattr("findjobs.cli.load_sources", _make_test_config)
        monkeypatch.setattr(
            "findjobs.cli.get_adapter",
            lambda _name: FakeFailingAdapter(),
        )

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            result = runner.invoke(
                app, ["collect", "--live", "--db-path", str(db_path)]
            )
            assert result.exit_code == 0
            assert "error" in result.output.lower()
            assert "API unavailable" in result.output

            from findjobs.db import init_db
            from findjobs.models import CollectRun

            session = init_db(db_path)
            try:
                runs = session.query(CollectRun).all()
                assert len(runs) == 1
                assert runs[0].status == "failed"
                assert runs[0].finished_at is not None
                assert "API unavailable" in runs[0].errors
                assert runs[0].jobs_found == 0
                assert runs[0].jobs_new == 0
            finally:
                session.close()

    def test_failed_source_does_not_block_other_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A failing source is skipped and the next source still collects."""
        from findjobs.cli import app

        monkeypatch.setattr(
            "findjobs.cli.load_sources", _make_two_source_config
        )

        call_count: int = 0

        def _adapter_for_name(name: str) -> BaseAdapter:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FakeTencentAdapter()  # tencent-careers: succeeds
            return FakeFailingAdapter()  # badcorp-careers: fails

        monkeypatch.setattr("findjobs.cli.get_adapter", _adapter_for_name)

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            result = runner.invoke(
                app, ["collect", "--live", "--db-path", str(db_path)]
            )
            assert result.exit_code == 0
            assert "2 jobs collected" in result.output
            assert "error" in result.output.lower()

            from findjobs.db import init_db
            from findjobs.models import CollectRun, Job

            session = init_db(db_path)
            try:
                jobs = session.query(Job).all()
                assert len(jobs) == 2  # only from the successful source

                runs = session.query(CollectRun).all()
                assert len(runs) == 2
                statuses = {r.status for r in runs}
                assert "completed" in statuses
                assert "failed" in statuses
            finally:
                session.close()
