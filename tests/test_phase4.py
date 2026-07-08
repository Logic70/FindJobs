"""Phase 4 tests: web UI routes and CLI schedule commands.

All tests are deterministic and offline. The FastAPI TestClient is used for
web route testing, and the Typer CliRunner for CLI commands.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _seed_db(db_path: Path) -> None:
    """Seed a temporary database with a company, source, jobs, and a collect run."""
    from findjobs.db import init_db
    from findjobs.config import CompanyConfig, SourceConfig, SourcesConfig
    from findjobs.repository import sync_config
    from findjobs.collection import CollectedJob, collect_jobs, create_collect_run, complete_collect_run

    session = init_db(db_path)

    config = SourcesConfig(
        companies=[CompanyConfig(slug="testcorp", name="Test Corp")],
        sources=[
            SourceConfig(
                slug="testcorp-careers",
                name="Test Corp Careers",
                company_slug="testcorp",
                source_type="official_careers",
                base_url="https://example.com",
                is_active=True,
            )
        ],
    )
    maps = sync_config(session, config)
    session.commit()

    company = maps["companies"]["testcorp"]
    source = maps["sources"]["testcorp-careers"]

    run = create_collect_run(session, source.id)

    jobs_to_insert = [
        CollectedJob(
            external_id="job-001",
            title="AI Engineer",
            url="https://example.com/jobs/001",
            description="LLM development",
            salary_text="30k-50k",
            salary_min=30000.0,
            salary_max=50000.0,
            salary_currency="CNY",
            salary_period="monthly",
            salary_disclosed=True,
            location="北京市、杭州市",
            job_type="full-time",
            matched_tags=["AI"],
        ),
        CollectedJob(
            external_id="job-002",
            title="Security Engineer",
            url="https://example.com/jobs/002",
            description="AppSec testing",
            salary_text="",
            salary_disclosed=False,
            location="北京",
            job_type="full-time",
            matched_tags=["Security"],
        ),
        CollectedJob(
            external_id="job-003",
            title="AI Frontend Engineer",
            url="https://example.com/jobs/003",
            description="React UI development for AI assistant products",
            salary_text="",
            salary_disclosed=False,
            location="上海市",
            job_type="contract",
            matched_tags=["AI"],
        ),
    ]

    total, new_count = collect_jobs(session, source.id, company.id, run.id, jobs_to_insert)
    complete_collect_run(session, run, total, new_count)
    session.commit()
    session.close()


@pytest.fixture
def tmp_db():
    """Yield a ``(db_path, client)`` tuple with a seeded database and TestClient."""
    from fastapi.testclient import TestClient
    from findjobs.web import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _seed_db(db_path)

        app = create_app(db_path=db_path)
        client = TestClient(app)
        yield db_path, client


@pytest.fixture
def cli_runner():
    """Return a Typer CliRunner."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Web: GET / redirects
# ---------------------------------------------------------------------------


class TestIndex:
    def test_redirects_to_jobs(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/jobs" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# Web: GET /jobs — listing & filtering
# ---------------------------------------------------------------------------


class TestJobsList:
    def test_lists_all_jobs(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        assert "AI Engineer" in html
        assert "Security Engineer" in html
        assert "AI Frontend Engineer" in html
        assert "Test Corp" in html

    def test_filter_by_company(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"company": "testcorp"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text

    def test_filter_by_tag(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"tag": "AI"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        assert "Security Engineer" not in resp.text

    def test_filter_by_salary_disclosed_true(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"salary_disclosed": "true"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        assert "Security Engineer" not in resp.text

    def test_filter_by_salary_disclosed_false(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"salary_disclosed": "false"})
        assert resp.status_code == 200
        assert "Security Engineer" in resp.text
        assert "AI Frontend Engineer" in resp.text
        assert "AI Engineer" not in resp.text

    def test_location_filter_options_split_and_normalize(self, tmp_db):
        db_path, _ = tmp_db
        from findjobs.db import init_db
        from findjobs.web import _get_filter_options

        session = init_db(db_path)
        try:
            options = _get_filter_options(session)
        finally:
            session.close()

        assert "北京" in options["locations"]
        assert "杭州" in options["locations"]
        assert "上海" in options["locations"]
        assert "北京市" not in options["locations"]
        assert "北京市、杭州市" not in options["locations"]
        assert "Beijing" not in options["locations"]

    def test_job_type_filter_options_normalize_values(self, tmp_db):
        db_path, _ = tmp_db
        from findjobs.db import init_db
        from findjobs.web import _get_filter_options

        session = init_db(db_path)
        try:
            options = _get_filter_options(session)
        finally:
            session.close()

        assert "全职" in options["job_types"]
        assert "合同" in options["job_types"]
        assert "full-time" not in options["job_types"]
        assert "contract" not in options["job_types"]

    def test_filter_by_normalized_multi_location(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"location": "杭州"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        assert "Security Engineer" not in resp.text

    def test_filter_by_normalized_job_type(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"job_type": "合同"})
        assert resp.status_code == 200
        assert "AI Frontend Engineer" in resp.text
        assert "AI Engineer" not in resp.text

    def test_filter_by_status(self, tmp_db):
        _, client = tmp_db
        # All start as active
        resp = client.get("/jobs", params={"status": "active"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text

    def test_search_by_title(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs", params={"q": "Security"})
        assert resp.status_code == 200
        assert "Security Engineer" in resp.text
        assert "AI Engineer" not in resp.text

    def test_undisclosed_shows_question_marks(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs")
        assert resp.status_code == 200
        # Security Engineer has undisclosed salary, should show Chinese text for undisclosed
        assert "未披露" in resp.text


# ---------------------------------------------------------------------------
# Web: GET /jobs/{job_id} — detail view
# ---------------------------------------------------------------------------


class TestJobDetail:
    def test_shows_official_url(self, tmp_db):
        _, client = tmp_db
        # AI Engineer is job-001, which should have id=1 after seeding
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "https://example.com/jobs/001" in resp.text

    def test_shows_undisclosed_salary_text(self, tmp_db):
        _, client = tmp_db
        # Security Engineer is id=2
        resp = client.get("/jobs/2")
        assert resp.status_code == 200
        assert "未披露" in resp.text

    def test_returns_404_for_unknown_job(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs/9999")
        assert resp.status_code == 404

    def test_shows_description(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "LLM development" in resp.text


# ---------------------------------------------------------------------------
# Web: POST /jobs/{job_id}/marks
# ---------------------------------------------------------------------------


class TestJobMarks:
    def test_create_bookmark_mark(self, tmp_db):
        _, client = tmp_db
        resp = client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": "Interesting"}, follow_redirects=False)
        # Should redirect back to detail
        assert resp.status_code == 303
        assert "/jobs/1" in resp.headers.get("location", "")

    def test_create_applied_mark(self, tmp_db):
        _, client = tmp_db
        resp = client.post("/jobs/1/marks", data={"mark_type": "applied", "note": "Applied on website"}, follow_redirects=False)
        assert resp.status_code == 303

    def test_create_ignored_mark(self, tmp_db):
        _, client = tmp_db
        resp = client.post("/jobs/1/marks", data={"mark_type": "ignored", "note": ""}, follow_redirects=False)
        assert resp.status_code == 303

    def test_update_existing_mark(self, tmp_db):
        _, client = tmp_db
        # Create
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": "First note"})
        # Update
        resp = client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": "Updated note"}, follow_redirects=False)
        assert resp.status_code == 303

        # Verify on detail page
        detail = client.get("/jobs/1")
        assert detail.status_code == 200
        assert "Updated note" in detail.text

    def test_rejects_invalid_mark_type(self, tmp_db):
        _, client = tmp_db
        resp = client.post("/jobs/1/marks", data={"mark_type": "invalid_type", "note": ""})
        assert resp.status_code == 400

    def test_mark_filter_works(self, tmp_db):
        _, client = tmp_db
        # Create a bookmark on job 1
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": "Watching"})

        # Filter by bookmark should show job 1
        resp = client.get("/jobs", params={"mark_type": "bookmark"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text

        # Job 2 (Security Engineer) has no bookmark and should not appear
        assert "Security Engineer" not in resp.text

    def test_mark_on_unknown_job_returns_404(self, tmp_db):
        _, client = tmp_db
        resp = client.post("/jobs/9999/marks", data={"mark_type": "bookmark"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Web: GET /runs
# ---------------------------------------------------------------------------


class TestRunsList:
    def test_shows_collect_runs(self, tmp_db):
        _, client = tmp_db
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert "completed" in resp.text
        assert "3" in resp.text  # jobs_found = 3


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_create_app_with_default_path(self):
        """create_app should succeed without arguments using the default path."""
        from findjobs.web import create_app

        app = create_app()
        assert app.title == "FindJobs"

    def test_create_app_with_explicit_path(self, tmp_db):
        """create_app should work with an explicit db_path."""
        # Already covered by tmp_db fixture, just double-check
        from findjobs.web import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "explicit.db"
            _seed_db(db_path)
            app = create_app(db_path=db_path)
            assert app.title == "FindJobs"


# ---------------------------------------------------------------------------
# CLI: serve command — test create_app only (uvicorn is blocking)
# ---------------------------------------------------------------------------


class TestCliServe:
    def test_serve_command_invocation(self, cli_runner):
        """The serve command should not crash at parse time.

        We can't start a real server in tests, so we just verify the
        command group accepts the expected options.
        """
        from findjobs.cli import app

        result = cli_runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--db-path" in result.output


# ---------------------------------------------------------------------------
# CLI: schedule install --dry-run
# ---------------------------------------------------------------------------


class TestScheduleInstall:
    def assert_schedule_output(self, output: str, *, has_db_path: bool = False):
        """Assert common schtasks command characteristics."""
        assert "schtasks" in output
        assert "/create" in output
        assert "/sc" in output
        assert "weekly" in output
        assert "/st" in output
        assert "powershell.exe" in output
        assert "uv" in output
        assert "findjobs" in output
        assert "--live" in output

    def test_dry_run_default(self, cli_runner):
        """Default dry-run should print the schtasks command."""
        from findjobs.cli import app

        result = cli_runner.invoke(app, ["schedule", "install"])
        assert result.exit_code == 0
        self.assert_schedule_output(result.output)
        # Default task name
        assert "FindJobsWeeklyWorkflow" in result.output
        assert "'findjobs' 'weekly' '--live'" in result.output

    def test_dry_run_explicit(self, cli_runner):
        """--dry-run should print the command without executing it."""
        from findjobs.cli import app

        result = cli_runner.invoke(app, ["schedule", "install", "--dry-run"])
        assert result.exit_code == 0
        self.assert_schedule_output(result.output)
        assert "'findjobs' 'weekly' '--live'" in result.output

    def test_dry_run_with_custom_options(self, cli_runner):
        """Custom task name, time, and db-path should appear in the output."""
        from findjobs.cli import app

        custom_db = "C:\\data\\findjobs.db"
        result = cli_runner.invoke(
            app,
            [
                "schedule",
                "install",
                "--task-name",
                "MyCollector",
                "--time",
                "14:30",
                "--db-path",
                custom_db,
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "MyCollector" in result.output
        assert "14:30" in result.output
        assert custom_db in result.output
        self.assert_schedule_output(result.output, has_db_path=True)
        assert "'findjobs' 'weekly' '--live'" in result.output

    def test_help_shows_options(self, cli_runner):
        """schedule install --help should list all options."""
        from findjobs.cli import app

        result = cli_runner.invoke(app, ["schedule", "install", "--help"])
        assert result.exit_code == 0
        assert "--task-name" in result.output
        assert "--time" in result.output
        assert "--db-path" in result.output
        assert "--dry-run" in result.output

    def test_status_dry_run_prints_query_command(self, cli_runner):
        """schedule status --dry-run should print the schtasks query command."""
        from findjobs.cli import app

        result = cli_runner.invoke(app, ["schedule", "status", "--dry-run"])

        assert result.exit_code == 0
        assert "schtasks" in result.output
        assert "/query" in result.output
        assert "FindJobsWeeklyWorkflow" in result.output

    def test_status_reports_scheduler_output(self, cli_runner, monkeypatch):
        """schedule status should print Task Scheduler output on Windows."""
        import subprocess
        import sys

        from findjobs.cli import app

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="TaskName: FindJobsWeeklyWorkflow\nStatus: Ready\n",
                stderr="",
            )

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "run", fake_run)

        result = cli_runner.invoke(app, ["schedule", "status"])

        assert result.exit_code == 0
        assert "Status: Ready" in result.output
