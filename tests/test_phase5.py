"""Phase 5 tests: export format correctness, CLI export, workflow guardrails.

All tests are deterministic and offline. Uses temp databases to verify
export content, filters, and output modes.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Provide a fresh SQLite temp database session for each test."""
    from findjobs.db import init_db

    session = init_db(Path(tempfile.mktemp(suffix=".db")))
    yield session
    session.close()


def _seed_jobs(db_session) -> None:
    """Seed the database with sample company, source, and jobs for export tests."""
    from findjobs.repository import sync_company, sync_source
    from findjobs.config import CompanyConfig, SourceConfig
    from findjobs.collection import (
        CollectedJob,
        collect_jobs,
        create_collect_run,
        complete_collect_run,
    )

    cc = CompanyConfig(slug="testcorp", name="Test Corp")
    company = sync_company(db_session, cc)

    sc = SourceConfig(
        slug="testcorp-careers",
        name="Test Corp Careers",
        company_slug="testcorp",
        source_type="official_careers",
        base_url="https://example.com",
        is_active=True,
    )
    source = sync_source(db_session, sc, company.id)

    jobs = [
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
            location="Beijing",
            job_type="full-time",
            matched_tags=["AI"],
        ),
        CollectedJob(
            external_id="job-002",
            title="Security Engineer",
            url="https://example.com/jobs/002",
            description="AppSec testing",
            salary_text="40-60万/年",
            salary_min=400000.0,
            salary_max=600000.0,
            salary_currency="CNY",
            salary_period="yearly",
            salary_disclosed=True,
            location="Beijing",
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
            location="Shanghai",
            job_type="full-time",
            matched_tags=["AI"],
        ),
    ]

    run = create_collect_run(db_session, source.id)
    total, new_count = collect_jobs(db_session, source.id, company.id, run.id, jobs)
    complete_collect_run(db_session, run, total, new_count)
    db_session.commit()


# ---------------------------------------------------------------------------
# exporter.py — query_jobs
# ---------------------------------------------------------------------------


class TestQueryJobs:
    """query_jobs returns correct data and respects filters."""

    def test_query_all_jobs(self, db_session):
        """query_jobs should return all seeded jobs."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session)
        assert len(results) == 3

    def test_query_jobs_has_expected_fields(self, db_session):
        """Each result dict should contain all EXPORT_COLUMNS."""
        from findjobs.exporter import EXPORT_COLUMNS, query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session)
        for row in results:
            for col in EXPORT_COLUMNS:
                assert col in row, f"Missing column: {col}"

    def test_query_jobs_includes_company_info(self, db_session):
        """company_slug and company_name should be present."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session)
        row = results[0]
        assert row["company_slug"] == "testcorp"
        assert row["company_name"] == "Test Corp"

    def test_filter_by_tag(self, db_session):
        """Filtering by tag should return only matching jobs."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session, tag="AI")
        assert len(results) == 2
        for result in results:
            assert "AI" in result["matched_tags"]

    def test_filter_by_company(self, db_session):
        """Filtering by company slug should return only that company's jobs."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session, company="testcorp")
        assert len(results) == 3

        results_none = query_jobs(db_session, company="nonexistent")
        assert len(results_none) == 0

    def test_filter_by_status(self, db_session):
        """Filtering by status should return only jobs with that status."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session, status="active")
        assert len(results) == 3

        results_archived = query_jobs(db_session, status="archived")
        assert len(results_archived) == 0

    def test_filter_by_salary_disclosed_true(self, db_session):
        """Filter salary_disclosed=true returns only disclosed-salary jobs."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session, salary_disclosed=True)
        assert len(results) == 2
        for r in results:
            assert r["salary_disclosed"] is True

    def test_filter_by_salary_disclosed_false(self, db_session):
        """Filter salary_disclosed=false returns only undisclosed-salary jobs."""
        from findjobs.exporter import query_jobs

        _seed_jobs(db_session)
        results = query_jobs(db_session, salary_disclosed=False)
        assert len(results) == 1
        for r in results:
            assert r["salary_disclosed"] is False

    def test_filter_by_since_days(self, db_session):
        """Filter since_days should exclude old jobs."""
        from findjobs.exporter import query_jobs
        from findjobs.models import Job

        _seed_jobs(db_session)

        # Set one job's last_seen_at far in the past
        job = db_session.query(Job).filter(Job.external_id == "job-003").first()
        job.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365)
        db_session.commit()

        results = query_jobs(db_session, since_days=30)
        # job-003 is 365 days old, should be excluded
        assert len(results) == 2
        for r in results:
            assert r["id"] != job.id


# ---------------------------------------------------------------------------
# exporter.py — JSONL export
# ---------------------------------------------------------------------------


class TestExportJsonl:
    """JSONL export produces one object per line with correct content."""

    def test_jsonl_export_content(self, db_session):
        """JSONL export should produce valid JSON objects, one per line."""
        from findjobs.exporter import query_jobs, export_jsonl
        import io

        _seed_jobs(db_session)
        jobs = query_jobs(db_session)
        buf = io.StringIO()
        export_jsonl(jobs, buf)
        buf.seek(0)

        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 3

        for i, line in enumerate(lines):
            obj = json.loads(line)
            assert obj["company_slug"] == "testcorp"
            assert obj["salary_disclosed"] in (True, False)

    def test_jsonl_undisclosed_salary_fields(self, db_session):
        """Undisclosed salary jobs should have salary_disclosed=false and null min/max."""
        from findjobs.exporter import query_jobs, export_jsonl
        import io

        _seed_jobs(db_session)
        jobs = query_jobs(db_session, salary_disclosed=False)
        buf = io.StringIO()
        export_jsonl(jobs, buf)
        buf.seek(0)

        obj = json.loads(buf.readline())
        assert obj["salary_disclosed"] is False
        assert obj["salary_min"] is None
        assert obj["salary_max"] is None
        # No estimated fields
        assert "salary_estimated_min" not in obj
        assert "salary_estimated_max" not in obj
        assert "salary_estimated" not in obj


# ---------------------------------------------------------------------------
# exporter.py — CSV export
# ---------------------------------------------------------------------------


class TestExportCsv:
    """CSV export has stable columns and correct rows."""

    def test_csv_has_header_and_rows(self, db_session):
        """CSV export should produce a header row and data rows."""
        from findjobs.exporter import EXPORT_COLUMNS, query_jobs, export_csv
        import csv
        import io

        _seed_jobs(db_session)
        jobs = query_jobs(db_session)
        buf = io.StringIO()
        export_csv(jobs, buf)
        buf.seek(0)

        reader = csv.DictReader(buf)
        rows = list(reader)
        assert len(rows) == 3

        # Verify header columns match EXPORT_COLUMNS
        assert reader.fieldnames == EXPORT_COLUMNS

    def test_csv_undisclosed_salary(self, db_session):
        """CSV row for undisclosed salary should have empty/null fields."""
        from findjobs.exporter import query_jobs, export_csv
        import csv
        import io

        _seed_jobs(db_session)
        jobs = query_jobs(db_session, salary_disclosed=False)
        buf = io.StringIO()
        export_csv(jobs, buf)
        buf.seek(0)

        reader = csv.DictReader(buf)
        row = next(reader)
        assert row["salary_disclosed"] == "False"
        assert row["salary_min"] == ""
        assert row["salary_max"] == ""


# ---------------------------------------------------------------------------
# exporter.py — do_export (integration)
# ---------------------------------------------------------------------------


class TestDoExport:
    """do_export orchestrates query + format correctly."""

    def test_do_export_returns_string_when_no_output(self, db_session):
        """do_export should return a string when output is None."""
        from findjobs.exporter import do_export

        _seed_jobs(db_session)
        result = do_export(db_session)
        assert isinstance(result, str)
        assert len(result.strip().split("\n")) == 3

    def test_do_export_writes_to_stream(self, db_session):
        """do_export should write to an output stream when provided."""
        from findjobs.exporter import do_export
        import io

        _seed_jobs(db_session)
        buf = io.StringIO()
        ret = do_export(db_session, output=buf)
        assert ret is None  # returns None when writing to stream
        assert len(buf.getvalue().strip().split("\n")) == 3


# ---------------------------------------------------------------------------
# CLI — export command
# ---------------------------------------------------------------------------


class TestCliExport:
    """CLI export command end-to-end tests."""

    def _seed_and_get_db(self) -> str:
        """Seed a temp DB and return its path."""
        from findjobs.db import init_db

        db_path = Path(tempfile.mktemp(suffix=".db"))
        session = init_db(db_path)
        _seed_jobs(session)
        session.close()
        return str(db_path)

    def test_cli_export_stdout_jsonl(self):
        """CLI export to stdout should produce JSONL."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(app, ["export", "--db-path", db_path])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        lines = result.output.strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert "company_slug" in obj

    def test_cli_export_csv_stdout(self):
        """CLI export --format csv to stdout should produce CSV."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(app, ["export", "--db-path", db_path, "--format", "csv"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert "company_slug,company_name,title" in result.output

    def test_cli_export_writes_file(self):
        """CLI export --output should write to a file."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        output_path = Path(tempfile.mktemp(suffix=".jsonl"))
        result = runner.invoke(
            app,
            ["export", "--db-path", db_path, "--output", str(output_path)],
        )
        assert result.exit_code == 0
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 3
        output_path.unlink(missing_ok=True)

    def test_cli_export_filter_tag(self):
        """CLI export --tag should filter results."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(app, ["export", "--db-path", db_path, "--tag", "AI"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "AI" in obj["matched_tags"]

    def test_cli_export_filter_salary_disclosed(self):
        """CLI export --salary-disclosed false should return undisclosed jobs."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(
            app, ["export", "--db-path", db_path, "--salary-disclosed", "false"]
        )
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["salary_disclosed"] is False

    def test_cli_export_filter_since(self):
        """CLI export --since should respect the day filter."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(app, ["export", "--db-path", db_path, "--since", "1"])
        assert result.exit_code == 0
        # All jobs were just created, so --since 1 should include all
        lines = result.output.strip().split("\n")
        assert len(lines) >= 1

    def test_cli_export_filter_company(self):
        """CLI export --company should filter by company slug."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(
            app, ["export", "--db-path", db_path, "--company", "testcorp"]
        )
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 3

        result_none = runner.invoke(
            app, ["export", "--db-path", db_path, "--company", "nonexistent"]
        )
        assert result_none.exit_code == 0
        assert result_none.output.strip() == ""

    def test_cli_export_invalid_salary_disclosed(self):
        """CLI export with invalid --salary-disclosed should error."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(
            app, ["export", "--db-path", db_path, "--salary-disclosed", "invalid"]
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Workflow guardrails — template files contain required phrases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# analysis.py / CLI analyze weekly
# ---------------------------------------------------------------------------


class TestWeeklyAnalysis:
    """Local weekly analysis consumes exported facts and writes reports."""

    def _write_jobs_jsonl(self, tmpdir: Path) -> Path:
        rows = [
            {
                "id": 1,
                "company_slug": "jd",
                "company_name": "JD",
                "title": "AI\u5b89\u5168\u4e13\u5bb6",
                "location": "\u5317\u4eac\u5e02",
                "job_type": "\u7814\u53d1\u7c7b",
                "status": "active",
                "salary_text": "",
                "salary_min": None,
                "salary_max": None,
                "salary_currency": "CNY",
                "salary_period": "monthly",
                "salary_disclosed": False,
                "matched_tags": ["AI", "Security", "AI Security"],
                "url": "https://example.com/1",
                "first_seen_at": "2026-06-30T01:00:00",
                "last_seen_at": "2026-06-30T02:00:00",
                "published_at": "2026-06-22T00:00:00",
            },
            {
                "id": 2,
                "company_slug": "meituan",
                "company_name": "Meituan",
                "title": "\u7b97\u6cd5\u5de5\u7a0b\u5e08-\u5b89\u5168",
                "location": "\u4e0a\u6d77\u5e02",
                "job_type": "\u7b97\u6cd5/\u6280\u672f\u7c7b",
                "status": "active",
                "salary_text": "",
                "salary_min": None,
                "salary_max": None,
                "salary_currency": "CNY",
                "salary_period": "monthly",
                "salary_disclosed": False,
                "matched_tags": ["Security"],
                "url": "https://example.com/2",
                "first_seen_at": "2026-06-30T01:00:00",
                "last_seen_at": "2026-06-30T02:00:00",
                "published_at": None,
            },
        ]
        path = tmpdir / "jobs.jsonl"
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        return path

    def test_run_weekly_analysis_writes_reports(self, tmp_path: Path):
        from findjobs.analysis import run_weekly_analysis

        jobs_path = self._write_jobs_jsonl(tmp_path)
        reports_dir = tmp_path / "reports"

        result = run_weekly_analysis(
            jobs_path=jobs_path,
            reports_dir=reports_dir,
            run_date="2026-06-30",
            profile_path=tmp_path / "missing-profile.md",
        )

        assert result.total_jobs == 2
        assert result.ai_security_jobs == 1
        assert result.summary_path.exists()
        assert result.ai_security_path.exists()
        assert result.profile_needed_path is not None
        assert result.profile_needed_path.exists()

        summary = result.summary_path.read_text(encoding="utf-8")
        assert "本次导出岗位总数：2" in summary
        assert "未披露薪资不估算" in summary
        assert "AI安全专家" in summary
        assert "仍打 AI 的数量：0" in summary

        ai_lines = result.ai_security_path.read_text(encoding="utf-8").splitlines()
        assert len(ai_lines) == 1
        assert json.loads(ai_lines[0])["company_slug"] == "jd"

    def test_cli_analyze_weekly(self, tmp_path: Path):
        from findjobs.cli import app

        jobs_path = self._write_jobs_jsonl(tmp_path)
        reports_dir = tmp_path / "reports"
        runner = CliRunner()

        result = runner.invoke(
            app,
            [
                "analyze",
                "weekly",
                "--jobs",
                str(jobs_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing-profile.md"),
                "--date",
                "2026-06-30",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Weekly analysis complete: 2 jobs" in result.output
        assert (reports_dir / "weekly" / "2026-06-30-summary.md").exists()
        assert (reports_dir / "weekly" / "ai-security.jsonl").exists()
        assert (reports_dir / "match" / "2026-06-30-profile-needed.md").exists()

    def test_weekly_analysis_with_profile_writes_matches_and_priorities(
        self, tmp_path: Path
    ):
        from findjobs.analysis import run_weekly_analysis

        jobs_path = self._write_jobs_jsonl(tmp_path)
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "\n".join(
                [
                    "# Profile",
                    "## Target Cities",
                    "- Beijing",
                    "## Preferences",
                    "- AI Security",
                    "- LLM",
                    "- AppSec",
                    "## Salary Expectation",
                    "- Minimum: 400000 CNY/year",
                ]
            ),
            encoding="utf-8",
        )

        result = run_weekly_analysis(
            jobs_path=jobs_path,
            reports_dir=reports_dir,
            run_date="2026-06-30",
            profile_path=profile_path,
        )

        assert result.profile_needed_path is None
        assert result.matches_path is not None
        assert result.priorities_path is not None
        assert result.career_advice_path is not None
        assert result.matches_path.exists()
        assert result.priorities_path.exists()
        assert result.career_advice_path.exists()

        matches = result.matches_path.read_text(encoding="utf-8")
        priorities = result.priorities_path.read_text(encoding="utf-8")
        advice = result.career_advice_path.read_text(encoding="utf-8")
        assert "个人匹配分析" in matches
        assert "AI安全专家" in matches
        assert "同时匹配 AI 与安全标签" in matches
        assert "投递优先级" in priorities
        assert "Top priority" in priorities
        assert "发展与学习建议" in advice
        assert "推荐岗位方向" in advice
        assert "发展建议" in advice
        assert "学习建议" in advice
        assert "不读取或写入数据库" in advice
        assert "不估算未披露薪资" in advice

    def test_profile_company_mentions_only_exclude_in_excluded_section(
        self, tmp_path: Path
    ):
        from findjobs.analysis import parse_profile

        profile_path = tmp_path / "profile.md"
        profile_path.write_text(
            "\n".join(
                [
                    "# Profile",
                    "## Preferences",
                    "- Prefer Tencent cloud security teams",
                    "## Excluded Companies",
                    "- Huawei",
                ]
            ),
            encoding="utf-8",
        )

        profile = parse_profile(profile_path)
        assert "huawei" in profile.excluded_companies
        assert "tencent" not in profile.excluded_companies


class TestProfileCli:
    """Profile setup helpers keep the matching workflow easy to start."""

    def test_profile_init_writes_example_template(self, tmp_path: Path):
        from findjobs.cli import app

        output = tmp_path / "profile.md"
        runner = CliRunner()

        result = runner.invoke(app, ["profile", "init", "--output", str(output)])

        assert result.exit_code == 0, result.output
        assert output.exists()
        assert "# Profile" in output.read_text(encoding="utf-8")
        assert "Profile initialized:" in result.output

    def test_profile_init_refuses_to_overwrite_without_force(self, tmp_path: Path):
        from findjobs.cli import app

        output = tmp_path / "profile.md"
        output.write_text("keep me", encoding="utf-8")
        runner = CliRunner()

        result = runner.invoke(app, ["profile", "init", "--output", str(output)])

        assert result.exit_code == 1
        assert output.read_text(encoding="utf-8") == "keep me"
        assert "Use --force to overwrite" in result.output


class TestSourcesCli:
    """Configured-source audit output makes company coverage explicit."""

    def test_sources_lists_active_and_inactive_sources(self):
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["sources"])

        assert result.exit_code == 0, result.output
        assert "Configured sources: 23/24 active" in result.output
        assert "tencent" in result.output
        assert "zhipu" in result.output
        assert "01ai" in result.output
        assert "inactive" in result.output
        assert "reason=" in result.output
        assert "Central official directory" in result.output

    def test_sources_active_only_hides_inactive_sources(self):
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["sources", "--active-only"])

        assert result.exit_code == 0, result.output
        assert "deepseek" in result.output
        assert "zhipu" in result.output
        assert "alibaba-aliyun-careers" in result.output
        assert "alibaba-talent" not in result.output
        assert "inactive" not in result.output

    def test_sources_includes_latest_run_status_when_db_is_available(self):
        from findjobs.cli import app
        from findjobs.collection import complete_collect_run, create_collect_run
        from findjobs.config import load_sources
        from findjobs.db import init_db
        from findjobs.repository import sync_config

        db_path = Path(tempfile.mktemp(suffix=".db"))
        session = init_db(db_path)
        try:
            maps = sync_config(session, load_sources())
            source = maps["sources"]["tencent-careers"]
            run = create_collect_run(session, source.id)
            complete_collect_run(
                session,
                run,
                jobs_found=12,
                jobs_new=3,
                errors="",
            )
            session.commit()
        finally:
            session.close()

        runner = CliRunner()
        result = runner.invoke(app, ["sources", "--db-path", str(db_path)])

        assert result.exit_code == 0, result.output
        assert "tencent-careers" in result.output
        assert "last_status=completed" in result.output
        assert "last_jobs=12 last_new=3" in result.output

    def test_sources_includes_failed_run_error_summary(self):
        from findjobs.cli import app
        from findjobs.collection import create_collect_run
        from findjobs.config import load_sources
        from findjobs.db import init_db
        from findjobs.models import _utcnow
        from findjobs.repository import sync_config

        db_path = Path(tempfile.mktemp(suffix=".db"))
        session = init_db(db_path)
        try:
            maps = sync_config(session, load_sources())
            source = maps["sources"]["baidu-talent"]
            run = create_collect_run(session, source.id)
            run.status = "failed"
            run.finished_at = _utcnow()
            run.errors = "SSL EOF while fetching source details"
            session.commit()
        finally:
            session.close()

        runner = CliRunner()
        result = runner.invoke(app, ["sources", "--db-path", str(db_path)])

        assert result.exit_code == 0, result.output
        assert "baidu-talent" in result.output
        assert "last_status=failed" in result.output
        assert "SSL EOF while fetching source details" in result.output


class TestWeeklyWorkflowCli:
    """Top-level weekly workflow command and scheduler integration."""

    def _seed_and_get_db(self) -> str:
        from findjobs.db import init_db

        db_path = Path(tempfile.mktemp(suffix=".db"))
        session = init_db(db_path)
        _seed_jobs(session)
        session.close()
        return str(db_path)

    def test_cli_weekly_no_live_exports_and_analyzes(self, tmp_path: Path):
        from findjobs.cli import app

        db_path = self._seed_and_get_db()
        reports_dir = tmp_path / "reports"
        runner = CliRunner()

        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                db_path,
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing-profile.md"),
                "--date",
                "2026-06-30",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Skipping live collection." in result.output
        assert "Weekly workflow complete: 3 jobs" in result.output
        assert (reports_dir / "weekly" / "jobs.jsonl").exists()
        assert (reports_dir / "weekly" / "jobs.csv").exists()
        assert (reports_dir / "weekly" / "ai-security.jsonl").exists()
        assert (reports_dir / "weekly" / "2026-06-30-summary.md").exists()
        assert (reports_dir / "match" / "2026-06-30-profile-needed.md").exists()

    def test_schedule_install_defaults_to_weekly_workflow(self):
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["schedule", "install"])

        assert result.exit_code == 0, result.output
        assert "FindJobsWeeklyWorkflow" in result.output
        assert "powershell.exe" in result.output
        assert "'findjobs' 'weekly' '--live'" in result.output

    def test_schedule_install_collect_only(self):
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["schedule", "install", "--collect-only"])

        assert result.exit_code == 0, result.output
        assert "powershell.exe" in result.output
        assert "'findjobs' 'collect' '--live'" in result.output


class TestOpencodeScript:
    def test_weekly_opencode_script_does_not_force_default_model(self):
        text = Path("tools/run_weekly_opencode.cmd").read_text(encoding="utf-8")

        assert "opencode/deepseek-v4-flash-free" not in text
        assert "--model" not in text
        assert "OPENCODE_MODEL" not in text


class TestClaudeScript:
    def test_weekly_claude_script_uses_read_only_deepseek_workflow(self):
        text = Path("tools/run_weekly_claude.cmd").read_text(encoding="utf-8")
        normalized = text.replace("/", "\\").lower()

        assert '--model "deepseek-v4-flash[1M]"' in text
        assert "claude-weekly-output.md" in text
        assert "workflows\\weekly_summary.md" in normalized
        assert "reports\\weekly\\jobs.jsonl" in normalized
        assert "read-only analysis" in text.lower()
        assert "database" in text.lower()
        assert "--tools \"Read,Grep,Glob\"" in text
        assert "--disallowedTools \"Bash,Edit,Write\"" in text
        assert "opencode" not in text.lower()


WORKFLOW_FILES = [
    "workflows/weekly_summary.md",
    "workflows/match_analysis.md",
    "workflows/priority_ranking.md",
    "workflows/career_advice.md",
    "workflows/adapter_repair.md",
]

GUARDRAIL_PHRASES = [
    "do not invent jobs",
    "do not estimate",
    "exported facts only",
    "do not write to the database",
]


@pytest.mark.parametrize("wf_path", WORKFLOW_FILES)
class TestWorkflowGuardrails:
    """Every workflow template enforces the required guardrails."""

    def test_contains_do_not_invent_jobs(self, wf_path):
        content = Path(wf_path).read_text(encoding="utf-8")
        assert (
            "do not invent jobs" in content.lower()
            or "do not fetch or invent jobs" in content.lower()
        ), f"{wf_path} is missing 'do not invent jobs' guardrail"

    def test_contains_do_not_estimate(self, wf_path):
        content = Path(wf_path).read_text(encoding="utf-8")
        assert (
            "do not estimate" in content.lower()
        ), f"{wf_path} is missing 'do not estimate' guardrail"

    def test_contains_exported_facts_only(self, wf_path):
        content = Path(wf_path).read_text(encoding="utf-8")
        assert (
            "exported facts only" in content.lower()
        ), f"{wf_path} is missing 'exported facts only' guardrail"

    def test_contains_do_not_write_db(self, wf_path):
        content = Path(wf_path).read_text(encoding="utf-8")
        assert (
            "do not write to the database" in content.lower()
        ), f"{wf_path} is missing 'do not write to the database' guardrail"
