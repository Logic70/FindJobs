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


def _seed_extras(session) -> int:
    """Add pagination/filter test data to an existing database session.

    Uses the existing company and source from ``_seed_db``. Returns the
    ``(ignored_id, review_id, archived_id)`` of the three special jobs.
    """
    from findjobs.models import Company, Job, Source, UserMark
    from datetime import datetime, timezone, timedelta

    company = session.query(Company).filter(Company.slug == "testcorp").first()
    source = session.query(Source).filter(Source.slug == "testcorp-careers").first()
    assert company is not None
    assert source is not None

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 5 additional active+target jobs with descending last_seen_at.
    # Two share "AI&测试" so we can test encoded query preservation.
    extra_titles = {
        1: "AI&测试 - Frontend",
        2: "AI&测试 - Backend",
        3: "Extra Job 3",
        4: "Extra Job 4",
        5: "Extra Job 5",
    }
    for i in range(1, 6):
        job = Job(
            source_id=source.id,
            company_id=company.id,
            external_id=f"extra-{i:03d}",
            title=extra_titles[i],
            url=f"https://example.com/extra/{i}",
            description=f"Extra job {i} for pagination filtering",
            status="active",
            relevance_status="target",
            location="北京",
            job_type="full-time",
            matched_tags="[]",
            created_at=now,
            updated_at=now,
            first_seen_at=now,
            last_seen_at=now - timedelta(hours=i),
        )
        session.add(job)
    session.flush()

    # Ignored job (active+target but marked ignored)
    ignored = Job(
        source_id=source.id, company_id=company.id,
        external_id="ignored-001", title="Ignored Position",
        url="https://example.com/ignored",
        description="Should be hidden by default",
        status="active", relevance_status="target",
        location="北京", job_type="full-time",
        matched_tags="[]", created_at=now, updated_at=now,
        first_seen_at=now, last_seen_at=now,
    )
    session.add(ignored)
    session.flush()
    session.add(UserMark(job_id=ignored.id, mark_type="ignored", note=""))

    # Review job (active+review)
    review = Job(
        source_id=source.id, company_id=company.id,
        external_id="review-001", title="Review Position",
        url="https://example.com/review",
        description="Non-target (review)",
        status="active", relevance_status="review",
        location="上海", job_type="full-time",
        matched_tags="[]", created_at=now, updated_at=now,
        first_seen_at=now, last_seen_at=now,
    )
    session.add(review)

    # Archived job (archived+target)
    archived = Job(
        source_id=source.id, company_id=company.id,
        external_id="archived-001", title="Archived Position",
        url="https://example.com/archived",
        description="Non-active (archived)",
        status="archived", relevance_status="target",
        location="北京", job_type="full-time",
        matched_tags="[]", created_at=now, updated_at=now,
        first_seen_at=now, last_seen_at=now,
    )
    session.add(archived)

    session.flush()
    return ignored.id, review.id, archived.id


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
def tmp_db_extended(tmp_db):
    """Extend ``tmp_db`` with additional jobs for pagination/filter tests."""
    db_path, client = tmp_db

    from findjobs.db import init_db

    session = init_db(db_path)
    try:
        _seed_extras(session)
        session.commit()
    finally:
        session.close()

    return db_path, client


@pytest.fixture
def client_extended(tmp_db_extended):
    """Shorthand — return only the TestClient for extended data."""
    _, c = tmp_db_extended
    return c


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


# ---------------------------------------------------------------------------
# Phase 4A: Default filters and pagination
# ---------------------------------------------------------------------------


class TestJobsDefaultFilters:
    """Default filter behavior: active + target + hide ignored."""

    def test_default_shows_only_active_target(self, client_extended):
        """Default view shows only active+target jobs, hiding ignored."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        # Original active+target jobs visible
        assert "AI Engineer" in html
        assert "Security Engineer" in html
        # New extra active+target jobs visible
        assert "AI&amp;测试 - Frontend" in html
        assert "AI&amp;测试 - Backend" in html
        for i in range(3, 6):
            assert f"Extra Job {i}" in html
        # Non-target (review) and non-active (archived) are hidden
        assert "Review Position" not in html
        assert "Archived Position" not in html
        # Ignored is hidden
        assert "Ignored Position" not in html

    def test_status_all_shows_all_statuses(self, client_extended):
        """status=all shows all statuses (including archived)."""
        resp = client_extended.get("/jobs", params={"status": "all"})
        assert resp.status_code == 200
        assert "Archived Position" in resp.text
        # Still hides non-target and ignored by default
        assert "Review Position" not in resp.text
        assert "Ignored Position" not in resp.text

    def test_explicit_status_active(self, client_extended):
        """Explicit status=active still applies the same filter."""
        resp = client_extended.get("/jobs", params={"status": "active"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        assert "Archived Position" not in resp.text

    def test_explicit_relevance_status_target(self, client_extended):
        """Explicit relevance_status=target works."""
        resp = client_extended.get("/jobs", params={"relevance_status": "target"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        assert "Review Position" not in resp.text

    def test_relevance_all_shows_all_statuses(self, client_extended):
        """relevance_status=all shows all relevance statuses."""
        resp = client_extended.get("/jobs", params={"relevance_status": "all"})
        assert resp.status_code == 200
        assert "Review Position" in resp.text
        # Still hides non-active and ignored by default
        assert "Archived Position" not in resp.text
        assert "Ignored Position" not in resp.text

    def test_default_status_active_selected(self, client_extended):
        """Plain /jobs has Active highlighted in the status dropdown."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        # The "活跃" option should be the selected one (no "全部状态" selected)
        assert '<option value="active" selected>活跃</option>' in html
        # "全部状态" must NOT be selected
        assert '<option value="all" selected>' not in html

    def test_default_relevance_target_selected(self, client_extended):
        """Plain /jobs has 目标 highlighted in the relevance dropdown."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        assert '<option value="target" selected>目标</option>' in html
        assert '<option value="all" selected>' not in html

    def test_ignored_hidden_by_default(self, client_extended):
        """Ignored-marked jobs are hidden by default."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert "Ignored Position" not in resp.text

    def test_mark_type_ignored_shows_ignored(self, client_extended):
        """mark_type=ignored shows ignored jobs."""
        resp = client_extended.get("/jobs", params={"mark_type": "ignored"})
        assert resp.status_code == 200
        assert "Ignored Position" in resp.text

    def test_show_ignored_displays_all_marks(self, client_extended):
        """show_ignored=true shows all marks including ignored."""
        resp = client_extended.get("/jobs", params={"show_ignored": "true"})
        assert resp.status_code == 200
        # Should show all active+target, including ignored
        assert "Ignored Position" in resp.text
        assert "AI Engineer" in resp.text

    def test_mark_type_and_status_filter_compose(self, client_extended):
        """mark_type=ignored shows ignored jobs with status filter."""
        resp = client_extended.get(
            "/jobs", params={"mark_type": "ignored", "status": "active"}
        )
        assert resp.status_code == 200
        # When mark_type=ignored, only ignored jobs show up
        assert "Ignored Position" in resp.text
        # Non-ignored jobs are excluded by the mark_type filter
        assert "AI Engineer" not in resp.text

    def test_filter_by_bookmark_mark_type(self, tmp_db):
        """Filtering by bookmark shows only bookmarked jobs."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": "Watching"})
        resp = client.get("/jobs", params={"mark_type": "bookmark"})
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        assert "Security Engineer" not in resp.text

    def test_all_filters_empty_shows_nothing_when_mismatch(self, client_extended):
        """Combined filters that match nothing show empty state."""
        resp = client_extended.get("/jobs", params={"q": "NONEXISTENT999"})
        assert resp.status_code == 200
        assert "暂无职位" in resp.text

    def test_status_all_across_pagination(self, client_extended):
        """status=all query parameter survives pagination."""
        resp = client_extended.get(
            "/jobs", params={"status": "all", "page_size": 5, "page": 2}
        )
        assert resp.status_code == 200
        html = resp.text
        # The URL should contain status=all
        assert "status=all" in html


# ---------------------------------------------------------------------------
# Phase 4A: Pagination
# ---------------------------------------------------------------------------


class TestJobsPagination:
    """Server-side pagination behavior."""

    def test_default_page_and_page_size(self, client_extended):
        """Default page=1, page_size=50 returns jobs with pagination info."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        # Default view has 8 active+target jobs (3 original + 5 extra)
        assert "AI Engineer" in html
        assert "Extra Job 5" in html
        # Pagination info shows total
        assert "共 " in html

    def test_bound_page_size_too_small(self, client_extended):
        """page_size=0 returns 422."""
        resp = client_extended.get("/jobs", params={"page_size": 0})
        assert resp.status_code == 422

    def test_bound_page_size_too_large(self, client_extended):
        """page_size=101 returns 422."""
        resp = client_extended.get("/jobs", params={"page_size": 101})
        assert resp.status_code == 422

    def test_bound_page_below_min(self, client_extended):
        """page=0 returns 422."""
        resp = client_extended.get("/jobs", params={"page": 0})
        assert resp.status_code == 422

    def test_bound_page_negative(self, client_extended):
        """page=-1 returns 422."""
        resp = client_extended.get("/jobs", params={"page": -1})
        assert resp.status_code == 422

    def test_invalid_status_rejected(self, client_extended):
        """Invalid status value returns 422."""
        resp = client_extended.get("/jobs", params={"status": "bogus"})
        assert resp.status_code == 422

    def test_invalid_relevance_rejected(self, client_extended):
        """Invalid relevance_status value returns 422."""
        resp = client_extended.get("/jobs", params={"relevance_status": "bogus"})
        assert resp.status_code == 422

    def test_no_overlap_between_pages(self, client_extended):
        """Jobs on consecutive pages do not overlap (stable ordering)."""
        page_size = 3
        resp1 = client_extended.get("/jobs", params={"page": 1, "page_size": page_size})
        resp2 = client_extended.get("/jobs", params={"page": 2, "page_size": page_size})
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        import re

        ids1 = set(re.findall(r"/jobs/(\d+)\"", resp1.text))
        ids2 = set(re.findall(r"/jobs/(\d+)\"", resp2.text))
        assert ids1.isdisjoint(ids2), f"Overlap between pages: {ids1 & ids2}"

    def test_filter_before_pagination_total(self, client_extended):
        """Total reflects all matching jobs, not just page size."""
        resp = client_extended.get("/jobs", params={"page": 1, "page_size": 3})
        assert resp.status_code == 200
        html = resp.text
        import re

        count_links = len(re.findall(r'/jobs/(\d+)"', html))
        assert count_links <= 3

    def test_out_of_range_page_renders_empty(self, client_extended):
        """Out-of-range positive page shows empty with accurate total."""
        resp = client_extended.get("/jobs", params={"page": 999, "page_size": 10})
        assert resp.status_code == 200
        html = resp.text
        assert "暂无职位" in html
        # Total should still be shown in pagination info
        assert "共 " in html

    def test_out_of_range_previous_links_to_last_valid(self, client_extended):
        """Previous on an out-of-range page links to the last valid page."""
        import html, urllib.parse, re
        # With 8 active+target jobs and page_size=3, last valid is page 3
        resp = client_extended.get("/jobs", params={"page": 999, "page_size": 3})
        assert resp.status_code == 200
        # Parse the prev_url from the pager-link that says 上一页
        match = re.search(
            r'<a\s+href="([^"]+)"\s+class="pager-link">上一页</a>', resp.text
        )
        assert match is not None, "Missing 上一页 pager-link"
        href = html.unescape(match.group(1))
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        assert qs.get("page") == ["3"]

    def test_query_param_retention_in_urls(self, client_extended):
        """Active query parameters are retained in the response."""
        resp = client_extended.get(
            "/jobs", params={"q": "Engineer", "page": 1, "page_size": 5}
        )
        assert resp.status_code == 200
        html = resp.text
        assert 'value="Engineer"' in html

    def test_encoded_q_with_special_chars(self, client_extended):
        """q containing & and Chinese text survives pagination URL encoding."""
        import html, urllib.parse, re
        # Two extra jobs have "AI&测试" in the title.
        # With page_size=1 and q=AI&测试, page 1 matches 1 job and has a next link.
        resp = client_extended.get(
            "/jobs", params={"q": "AI&测试", "page_size": 1}
        )
        assert resp.status_code == 200
        # Page 1 should have one of the matching jobs (Jinja-escaped &)
        assert "AI&amp;测试" in resp.text
        # Parse the only pager-link (下一页, since has_prev=False on page 1)
        links = re.findall(
            r'<a\s+href="([^"]+)"\s+class="pager-link">', resp.text
        )
        assert len(links) == 1, f"Expected 1 pager-link on page 1, got {len(links)}"
        href = html.unescape(links[0])
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        assert qs.get("q") == ["AI&测试"]
        assert qs.get("page") == ["2"]
        assert qs.get("page_size") == ["1"]

    def test_page_size_custom_accepted(self, client_extended):
        """Custom page_size in valid range (1-100) is accepted and honored."""
        resp = client_extended.get("/jobs", params={"page_size": 5, "page": 1})
        assert resp.status_code == 200
        resp2 = client_extended.get("/jobs", params={"page_size": 5, "page": 2})
        assert resp2.status_code == 200
        assert "共 " in resp2.text

    def test_page_size_selector_present(self, client_extended):
        """Per-page size selector (25, 50, 100) is present in the filter form."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        assert 'value="25"' in html
        assert 'value="50"' in html
        assert 'value="100"' in html
        # Default 50 should be selected
        assert '<option value="50" selected>50/页</option>' in html

    def test_page_size_25_selected(self, client_extended):
        """page_size=25 is accepted and 25/页 is selected."""
        resp = client_extended.get("/jobs", params={"page_size": 25})
        assert resp.status_code == 200
        html = resp.text
        assert '<option value="25" selected>25/页</option>' in html

    def test_response_hundred_rows_below_limit(self, tmp_db):
        """A 100-row page response is below 300 KB."""
        db_path, client = tmp_db
        from findjobs.db import init_db

        session = init_db(db_path)
        try:
            from findjobs.models import Company, Job, Source
            company = session.query(Company).filter(Company.slug == "testcorp").first()
            source = session.query(Source).filter(Source.slug == "testcorp-careers").first()
            from datetime import datetime, timezone, timedelta

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for i in range(97):
                session.add(Job(
                    source_id=source.id, company_id=company.id,
                    external_id=f"bulk-{i:03d}", title=f"Bulk Job {i}",
                    url=f"https://example.com/bulk/{i}",
                    description=f"Bulk job {i} for size test",
                    status="active", relevance_status="target",
                    location="北京", job_type="full-time",
                    matched_tags="[]", created_at=now, updated_at=now,
                    first_seen_at=now, last_seen_at=now - timedelta(hours=100 + i),
                ))
            session.commit()
        finally:
            session.close()

        resp = client.get("/jobs", params={"page_size": 100})
        assert resp.status_code == 200
        size_bytes = len(resp.text.encode("utf-8"))
        assert size_bytes < 300_000, f"Response {size_bytes} bytes >= 300 KB"

    def test_total_pages_accurate(self, client_extended):
        """Total pages calculation is correct with exact assertion."""
        import html, urllib.parse, re
        # 8 total jobs, page_size=3 → 3 pages (3+3+2)
        resp = client_extended.get("/jobs", params={"page_size": 3, "page": 1})
        assert resp.status_code == 200
        # Page 3 should exist
        resp3 = client_extended.get("/jobs", params={"page_size": 3, "page": 3})
        assert resp3.status_code == 200
        # Page 4 should be out of range
        resp4 = client_extended.get("/jobs", params={"page_size": 3, "page": 4})
        assert resp4.status_code == 200
        assert "暂无职位" in resp4.text
        # Previous should link to page 3 (last valid) — parse exactly
        match = re.search(
            r'<a\s+href="([^"]+)"\s+class="pager-link">上一页</a>', resp4.text
        )
        assert match is not None, "Missing 上一页 link on out-of-range page"
        href = html.unescape(match.group(1))
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        assert qs.get("page") == ["3"]

    def test_pagination_no_missing_ids(self, client_extended):
        """All matching job IDs appear across pages exactly once."""
        import re
        page_size = 3
        all_ids = set()
        for page in range(1, 6):
            resp = client_extended.get("/jobs", params={"page": page, "page_size": page_size})
            if "暂无职位" in resp.text:
                break
            ids = set(re.findall(r"/jobs/(\d+)\"", resp.text))
            assert ids.isdisjoint(all_ids), f"Page {page} overlaps"
            all_ids.update(ids)
        assert len(all_ids) >= 8

    def test_all_filters_across_pagination(self, client_extended):
        """status=all & relevance=all survive page change via encoded URLs."""
        resp = client_extended.get(
            "/jobs",
            params={
                "status": "all",
                "relevance_status": "all",
                "page_size": 5,
                "page": 2,
            },
        )
        assert resp.status_code == 200
        html = resp.text
        # On page 2 there should be a prev link containing both filter sentinels
        assert "status=all" in html
        assert "relevance_status=all" in html


# ---------------------------------------------------------------------------
# Phase 4A: UI labels and pagination controls
# ---------------------------------------------------------------------------


class TestJobsPaginationUI:
    """Chinese UI labels and pagination controls."""

    def test_chinese_search_placeholder(self, client_extended):
        """Search input has Chinese placeholder."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert 'placeholder="搜索' in resp.text

    def test_chinese_filter_labels(self, client_extended):
        """Filter dropdowns use Chinese labels."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        assert "全部公司" in html
        assert "全部地点" in html
        assert "全部类型" in html
        assert "全部标签" in html
        assert "全部状态" in html

    def test_chinese_table_headers(self, client_extended):
        """Table headers use Chinese labels."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        assert "公司" in html
        assert "职位" in html
        assert "标签" in html
        assert "薪资" in html
        assert "标记" in html

    def test_chinese_empty_state(self, client_extended):
        """Empty result shows Chinese text."""
        resp = client_extended.get("/jobs", params={"q": "ZZZZ_NOT_FOUND_ZZZZ"})
        assert resp.status_code == 200
        assert "暂无职位" in resp.text

    def test_disclosed_salary_label(self, tmp_db):
        """Disclosed salary shows salary_text; undisclosed shows 未披露."""
        _, client = tmp_db
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert "30k-50k" in resp.text
        assert "未披露" in resp.text

    def test_mark_type_labels(self, client_extended):
        """Mark type dropdown uses Chinese values."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert "已投递" in resp.text
        assert "已忽略" in resp.text

    def test_relevance_status_options(self, client_extended):
        """Relevance status dropdown has all project enums."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        html = resp.text
        assert "目标" in html
        assert "待定" in html
        assert "排除" in html

    def test_relevance_status_all_option(self, client_extended):
        """Relevance status dropdown includes '全部' via all value."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert 'value="all"' in resp.text

    def test_status_all_option(self, client_extended):
        """Status dropdown has the 'all' sentinel option."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert 'value="all"' in resp.text

    def test_pagination_controls_exist(self, client_extended):
        """Pagination controls (prev/next) are rendered."""
        resp = client_extended.get("/jobs", params={"page_size": 3, "page": 2})
        assert resp.status_code == 200
        assert "上一页" in resp.text
        assert "下一页" in resp.text

    def test_horizontal_scroll_container(self, client_extended):
        """Job table is inside a horizontal-scroll container."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert 'class="table-scroll"' in resp.text
        assert 'class="jobs-table"' in resp.text

    def test_jobs_table_keeps_readable_mobile_width(self):
        """The dense jobs table scrolls instead of collapsing every column."""
        css_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "findjobs"
            / "static"
            / "style.css"
        )
        css = css_path.read_text(encoding="utf-8")
        assert ".jobs-table { min-width: 1040px; }" in css

    def test_show_ignored_checkbox_present(self, client_extended):
        """show_ignored checkbox is present."""
        resp = client_extended.get("/jobs")
        assert resp.status_code == 200
        assert "显示忽略" in resp.text


# ---------------------------------------------------------------------------
# Phase 4B: Mark deletion endpoint
# ---------------------------------------------------------------------------


class TestMarkDeletion:
    """Phase 4B: POST /jobs/{id}/marks/delete — deletion behavior."""

    def test_delete_existing_mark(self, tmp_db):
        """POST to delete endpoint removes the mark and redirects to detail."""
        db_path, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": "Watching"})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"
        from findjobs.db import init_db
        from findjobs.models import UserMark

        session = init_db(db_path)
        try:
            mark = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "bookmark"
            ).first()
            assert mark is None
        finally:
            session.close()

    def test_delete_mark_idempotent(self, tmp_db):
        """Deleting a non-existent mark returns 303 (idempotent)."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_delete_mark_invalid_type(self, tmp_db):
        """Invalid mark_type returns 400."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bogus"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_delete_mark_unknown_job(self, tmp_db):
        """Unknown job_id returns 404."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/9999/marks/delete",
            data={"mark_type": "bookmark"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_delete_mark_with_valid_next_url(self, tmp_db):
        """Delete with safe next_url redirects there."""
        db_path, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "/jobs"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs"

    def test_delete_mark_with_unsafe_next_url(self, tmp_db):
        """Delete with unsafe next_url falls back to job detail."""
        db_path, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "https://evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"


# ---------------------------------------------------------------------------
# Phase 4B: Mark coexistence / transition semantics
# ---------------------------------------------------------------------------


class TestMarkSemantics:
    """Phase 4B: POST /jobs/{id}/marks enforces semantic rules."""

    def test_bookmark_and_applied_coexist(self, tmp_db):
        """bookmark and applied may coexist on the same job."""
        db_path, client = tmp_db
        client.post("/jobs/2/marks", data={"mark_type": "bookmark", "note": "Watch"})
        client.post(
            "/jobs/2/marks",
            data={"mark_type": "applied", "note": "Applied"},
            follow_redirects=False,
        )
        from findjobs.db import init_db
        from findjobs.models import UserMark

        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(UserMark.job_id == 2).all()
            types = {m.mark_type for m in marks}
            assert "bookmark" in types
            assert "applied" in types
        finally:
            session.close()

    def test_ignored_removes_applied_keeps_bookmark(self, tmp_db):
        """Setting ignored removes applied but preserves bookmark."""
        db_path, client = tmp_db
        client.post("/jobs/2/marks", data={"mark_type": "bookmark", "note": "Watch"})
        client.post("/jobs/2/marks", data={"mark_type": "applied", "note": "Applied"})
        resp = client.post(
            "/jobs/2/marks",
            data={"mark_type": "ignored", "note": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        from findjobs.db import init_db
        from findjobs.models import UserMark

        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(UserMark.job_id == 2).all()
            types = {m.mark_type for m in marks}
            assert "bookmark" in types
            assert "applied" not in types
            assert "ignored" in types
        finally:
            session.close()

    def test_applied_removes_ignored_keeps_bookmark(self, tmp_db):
        """Setting applied removes ignored but preserves bookmark."""
        db_path, client = tmp_db
        client.post("/jobs/2/marks", data={"mark_type": "bookmark", "note": "Watch"})
        client.post("/jobs/2/marks", data={"mark_type": "ignored", "note": ""})
        resp = client.post(
            "/jobs/2/marks",
            data={"mark_type": "applied", "note": "Applied"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        from findjobs.db import init_db
        from findjobs.models import UserMark

        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(UserMark.job_id == 2).all()
            types = {m.mark_type for m in marks}
            assert "bookmark" in types
            assert "ignored" not in types
            assert "applied" in types
        finally:
            session.close()

    def test_setting_ignored_leaves_bookmark_alone(self, tmp_db):
        """Setting ignored does not affect an existing bookmark."""
        db_path, client = tmp_db
        client.post("/jobs/2/marks", data={"mark_type": "bookmark", "note": "Safe"})
        client.post(
            "/jobs/2/marks",
            data={"mark_type": "ignored", "note": ""},
            follow_redirects=False,
        )
        from findjobs.db import init_db
        from findjobs.models import UserMark

        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(UserMark.job_id == 2).all()
            types = {m.mark_type for m in marks}
            assert "bookmark" in types
            assert "ignored" in types
            assert len(marks) == 2  # exactly two, not duplicated
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Phase 4B: Extended safe redirects (/jobs list and detail)
# ---------------------------------------------------------------------------


class TestSafeRedirectsExtended:
    """Phase 4B: next_url redirects now include /jobs paths."""

    def test_next_url_jobs_list(self, tmp_db):
        """next_url=/jobs is a safe redirect."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs"

    def test_next_url_jobs_with_query(self, tmp_db):
        """next_url=/jobs?q=test is a safe redirect."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?q=test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs?q=test"

    def test_next_url_jobs_detail(self, tmp_db):
        """next_url=/jobs/1 is a safe redirect."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs/1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_jobs_detail_with_query(self, tmp_db):
        """next_url=/jobs/1?from=detail is rejected (no query on detail)."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={
                "mark_type": "bookmark",
                "note": "",
                "next_url": "/jobs/1?from=detail",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_random_path_rejected(self, tmp_db):
        """next_url=/other falls back to job detail."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/other"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"


    def test_next_url_recommendations_detail_rejected(self, tmp_db):
        """next_url=/recommendations/1 is rejected, falls back to job detail."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/recommendations/1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_empty_query_rejected(self, tmp_db):
        """next_url=/jobs? is rejected (empty query)."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_key_only_rejected(self, tmp_db):
        """next_url=/jobs?key (no =value) is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?key"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_empty_value_rejected(self, tmp_db):
        """next_url=/jobs?key= (empty value) is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?key="},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_malformed_query_rejected(self, tmp_db):
        """next_url=/jobs?=&= is rejected (malformed query components)."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?=&="},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_fragment_rejected(self, tmp_db):
        """next_url with fragment (#) is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs#section"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_backslash_rejected(self, tmp_db):
        """next_url with backslash is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs\\test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_percent_encoded_crlf_rejected(self, tmp_db):
        """next_url with percent-encoded CR/LF is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?q=%0D%0A"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_malformed_percent_rejected(self, tmp_db):
        """next_url with truncated percent sequence is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/jobs?q=%2X"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_next_url_unknown_path_rejected(self, tmp_db):
        """next_url=/other/path is rejected."""
        _, client = tmp_db
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/other/path"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    # ---- These same rejection rules also apply to the delete endpoint ----

    def test_delete_next_url_recommendations_detail_rejected(self, tmp_db):
        """Delete: next_url=/recommendations/1 is rejected."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "/recommendations/1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_delete_next_url_detail_with_query_rejected(self, tmp_db):
        """Delete: next_url=/jobs/1?from=detail is rejected."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "/jobs/1?from=detail"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_delete_next_url_crlf_rejected(self, tmp_db):
        """Delete: next_url with CRLF injection is rejected."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "/recommendations\r\nX-Injected: true"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_delete_next_url_external_rejected(self, tmp_db):
        """Delete: next_url=https://evil.com is rejected."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "https://evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_delete_next_url_scheme_relative_rejected(self, tmp_db):
        """Delete: next_url=//evil.com is rejected."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "//evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_delete_next_url_fragment_rejected(self, tmp_db):
        """Delete: next_url with fragment is rejected."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "bookmark", "next_url": "/jobs#section"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"


class TestJobDetailChinese:
    """Phase 4B: Chinese labels, separate resp/req display, unsafe URL."""

    def test_chinese_meta_labels(self, tmp_db):
        """Detail uses Chinese labels for meta fields."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        html = resp.text
        assert "公司" in html
        assert "地点" in html
        assert "类型" in html
        assert "状态" in html
        assert "标签" in html

    def test_responsibilities_section(self, tmp_db):
        """Responsibilities shown as a separate section."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "岗位职责" in resp.text or "职责" in resp.text

    def test_requirements_section(self, tmp_db):
        """Requirements shown as a separate section."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "岗位要求" in resp.text or "要求" in resp.text

    def test_missing_responsibilities_placeholder(self, tmp_db):
        """Missing responsibilities show an explicit placeholder."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "未提供" in resp.text

    def test_missing_requirements_placeholder(self, tmp_db):
        """Missing requirements show an explicit placeholder."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "未提供" in resp.text

    def test_original_description_retained(self, tmp_db):
        """Original job description is still shown."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        assert "LLM development" in resp.text

    def test_url_link_when_safe(self, tmp_db):
        """Safe job URL renders as a clickable link."""
        _, client = tmp_db
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="https://example.com/jobs/001"' in html

    def test_unsafe_url_shown_as_text(self, tmp_db):
        """javascript: URL in detail is text, not a link."""
        db_path, client = tmp_db
        from findjobs.db import init_db
        from findjobs.models import Job

        session = init_db(db_path)
        try:
            session.query(Job).filter(Job.id == 1).update(
                {"url": "javascript:alert(1)"}
            )
            session.commit()
        finally:
            session.close()
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="javascript:alert(1)"' not in html
        assert "javascript:alert(1)" in html

    def test_whitespace_url_shown_as_text(self, tmp_db):
        """Whitespace-surrounded URL in detail is text, not a link."""
        db_path, client = tmp_db
        from findjobs.db import init_db
        from findjobs.models import Job

        session = init_db(db_path)
        try:
            session.query(Job).filter(Job.id == 1).update(
                {"url": " https://example.com/jobs/001 "}
            )
            session.commit()
        finally:
            session.close()
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        html = resp.text
        assert 'href=" https://' not in html
        assert "https://example.com/jobs/001" in html

    def test_delete_button_in_detail(self, tmp_db):
        """Detail page has × delete buttons for marks."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        html = resp.text
        assert "×" in html or "&#x2716;" in html
        assert "/marks/delete" in html

    def test_chinese_mark_label_in_detail(self, tmp_db):
        """Detail page shows Chinese labels for marks."""
        _, client = tmp_db
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})
        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        # The Chinese label for bookmark should appear
        assert "收藏" in resp.text

    def test_marks_deterministic_order_in_detail(self, tmp_db):
        """Detail page shows marks in bookmark→applied→ignored order."""
        _, client = tmp_db
        # Insert marks in non-display order
        client.post("/jobs/1/marks", data={"mark_type": "applied", "note": ""})
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})

        resp = client.get("/jobs/1")
        assert resp.status_code == 200
        html = resp.text

        # The sort_marks() helper sorts bookmark (收藏) before applied (已投递).
        # Use </td> suffix to distinguish from <option> labels in the form.
        idx_bookmark = html.find("收藏</td>")
        idx_applied = html.find("已投递</td>")
        assert idx_bookmark >= 0, "Missing bookmark label in detail table"
        assert idx_applied >= 0, "Missing applied label in detail table"
        assert idx_bookmark < idx_applied, (
            "Marks not in bookmark→applied order in detail"
        )

    def test_marks_summary_combined_string(self, client_extended):
        """Marks summary uses concatenated Chinese labels (not just filter options)."""
        client = client_extended
        # Add marks in non-alphabetical order
        client.post("/jobs/1/marks", data={"mark_type": "applied", "note": ""})
        client.post("/jobs/1/marks", data={"mark_type": "bookmark", "note": ""})

        resp = client.get("/jobs", params={"show_ignored": "true"})
        assert resp.status_code == 200
        html = resp.text

        # "收藏, 已投递" (with comma and space) only appears in _marks_summary
        # output; filter dropdown options are separate <option> tags, so this
        # concatenated form cannot come from the filter UI.
        assert "收藏, 已投递" in html

    def test_marks_summary_ordering_deterministic(self, tmp_db):
        """_marks_summary returns Chinese labels in bookmark→applied→ignored order."""
        db_path, _ = tmp_db
        from findjobs.db import init_db
        from findjobs.models import Job, UserMark
        from findjobs.web import _marks_summary

        session = init_db(db_path)
        try:
            job = session.query(Job).filter(Job.id == 1).first()
            # Add all three marks in non-deterministic order
            for mt in ("applied", "ignored", "bookmark"):
                session.add(UserMark(job_id=1, mark_type=mt, note=""))
            session.commit()
            session.refresh(job)
            summary = _marks_summary(job)
            # Expected order: bookmark, applied, ignored
            assert summary == "收藏, 已投递, 已忽略"
        finally:
            session.close()
