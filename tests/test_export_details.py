"""Tests for summary and full job fact exports."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def db_session():
    """Provide a fresh SQLite temp database session for each test."""
    from findjobs.db import init_db

    session = init_db(Path(tempfile.mktemp(suffix=".db")))
    yield session
    session.close()


def _seed_detail_jobs(db_session):
    """Seed sample company, source, and jobs with classification details."""
    from findjobs.collection import (
        CollectedJob,
        collect_jobs,
        complete_collect_run,
        create_collect_run,
    )
    from findjobs.config import CompanyConfig, SourceConfig
    from findjobs.models import Job
    from findjobs.repository import sync_company, sync_source

    cc = CompanyConfig(slug="detailcorp", name="Detail Corp")
    company = sync_company(db_session, cc)

    sc = SourceConfig(
        slug="detailcorp-careers",
        name="Detail Corp Careers",
        company_slug="detailcorp",
        source_type="official_careers",
        base_url="https://example.com",
        is_active=True,
    )
    source = sync_source(db_session, sc, company.id)

    jobs_data = [
        CollectedJob(
            external_id="det-001",
            title="AI Engineer",
            url="https://example.com/det/001",
            description="LLM development",
            salary_text="50k-80k",
            salary_min=50000.0,
            salary_max=80000.0,
            salary_currency="CNY",
            salary_period="monthly",
            salary_disclosed=True,
            location="Beijing",
            job_type="full-time",
            matched_tags=["AI"],
        ),
        CollectedJob(
            external_id="det-002",
            title="Security Engineer",
            url="https://example.com/det/002",
            description="AppSec testing",
            salary_text="",
            salary_disclosed=False,
            location="Shanghai",
            job_type="full-time",
            matched_tags=["Security"],
        ),
    ]

    run = create_collect_run(db_session, source.id)
    total, new_count = collect_jobs(
        db_session, source.id, company.id, run.id, jobs_data
    )
    complete_collect_run(db_session, run, total, new_count)
    db_session.commit()

    # Set classification and detail fields for full export testing
    jobs = db_session.query(Job).order_by(Job.id).all()

    jobs[0].relevance_status = "target"
    jobs[0].classification_version = "v2"
    jobs[0].classification_reasons = json.dumps(
        ["skill_match", "title_match"], ensure_ascii=False
    )
    jobs[0].responsibilities = "Develop ML models\nDeploy to production"
    jobs[0].requirements = "Python\nTensorFlow\n5+ years experience"
    jobs[0].detail_completeness = "full"

    jobs[1].relevance_status = "review"
    jobs[1].classification_version = "v2"
    jobs[1].classification_reasons = json.dumps(
        ["general_security", "no_ai_overlap"], ensure_ascii=False
    )
    jobs[1].responsibilities = "Monitor security events\nRespond to incidents"
    jobs[1].requirements = "Security experience\nSIEM tools"
    jobs[1].detail_completeness = "responsibilities_only"

    db_session.commit()


# ---------------------------------------------------------------------------
# query_jobs — detail_level
# ---------------------------------------------------------------------------


class TestQueryJobsDetailLevel:
    """query_jobs detail_level controls which columns and values appear."""

    def test_summary_excludes_long_fields(self, db_session):
        """Summary mode must not include long fields or classification data."""
        from findjobs.exporter import SUMMARY_COLUMNS, query_jobs

        _seed_detail_jobs(db_session)
        results = query_jobs(db_session, detail_level="summary")

        assert len(results) == 2
        forbidden = {
            "description",
            "responsibilities",
            "requirements",
            "classification_reasons",
            "relevance_status",
            "classification_version",
            "detail_completeness",
        }
        for row in results:
            for col in forbidden:
                assert col not in row, f"Summary should not contain: {col}"
            for col in SUMMARY_COLUMNS:
                assert col in row, f"Summary missing column: {col}"

    def test_full_includes_all_extra_fields(self, db_session):
        """Full mode must include every summary field plus extra database facts."""
        from findjobs.exporter import FULL_COLUMNS, SUMMARY_COLUMNS, query_jobs

        _seed_detail_jobs(db_session)
        results = query_jobs(db_session, detail_level="full")

        assert len(results) == 2
        for row in results:
            for col in SUMMARY_COLUMNS:
                assert col in row, f"Missing summary column in full: {col}"
            for col in FULL_COLUMNS:
                assert col in row, f"Missing full column: {col}"

    def test_full_contains_factual_values(self, db_session):
        """Full mode returns exact database values for extra fields."""
        from findjobs.exporter import query_jobs

        _seed_detail_jobs(db_session)
        results = query_jobs(db_session, detail_level="full")

        # Sort by id for deterministic assertion
        results.sort(key=lambda r: r["id"])

        assert results[0]["relevance_status"] == "target"
        assert results[0]["classification_version"] == "v2"
        assert results[0]["classification_reasons"] == [
            "skill_match",
            "title_match",
        ]
        assert results[0]["description"] == "LLM development"
        assert (
            results[0]["responsibilities"]
            == "Develop ML models\nDeploy to production"
        )
        assert (
            results[0]["requirements"]
            == "Python\nTensorFlow\n5+ years experience"
        )
        assert results[0]["detail_completeness"] == "full"

        assert results[1]["relevance_status"] == "review"
        assert results[1]["classification_reasons"] == [
            "general_security",
            "no_ai_overlap",
        ]
        assert results[1]["detail_completeness"] == "responsibilities_only"

    def test_classification_reasons_decoded_from_json(self, db_session):
        """classification_reasons should be decoded from JSON into string lists."""
        from findjobs.exporter import query_jobs

        _seed_detail_jobs(db_session)
        results = query_jobs(db_session, detail_level="full")

        for row in results:
            reasons = row["classification_reasons"]
            assert isinstance(reasons, list)
            assert len(reasons) >= 1
            for r in reasons:
                assert isinstance(r, str)

    def test_classification_reasons_fallback_for_plain_text(self, db_session):
        """Malformed classification_reasons falls back to comma-split."""
        from findjobs.exporter import query_jobs
        from findjobs.models import Job

        _seed_detail_jobs(db_session)
        job = db_session.query(Job).first()
        # Store plain text instead of JSON to trigger the legacy fallback
        job.classification_reasons = "alpha,beta,gamma"
        db_session.commit()

        results = query_jobs(db_session, detail_level="full")
        row = next(
            r
            for r in results
            if r["classification_reasons"] == ["alpha", "beta", "gamma"]
        )
        assert row is not None

    def test_default_detail_level_is_summary(self, db_session):
        """Calling query_jobs without detail_level defaults to summary exclusion."""
        from findjobs.exporter import query_jobs

        _seed_detail_jobs(db_session)
        results = query_jobs(db_session)

        assert len(results) >= 1
        for row in results:
            assert "description" not in row
            assert "responsibilities" not in row
            assert "requirements" not in row
            assert "classification_reasons" not in row

    def test_invalid_detail_level_raises_value_error(self, db_session):
        """Invalid detail_level must raise ValueError before querying."""
        from findjobs.exporter import query_jobs

        with pytest.raises(ValueError, match="detail_level"):
            query_jobs(db_session, detail_level="invalid")

    def test_stable_tie_ordering(self, db_session):
        """Jobs with same last_seen_at are ordered by id DESC."""
        from findjobs.exporter import query_jobs
        from findjobs.models import Job

        _seed_detail_jobs(db_session)

        # All jobs get the same last_seen_at to test tie-breaking
        now = datetime(2026, 7, 13, 10, 0, 0)
        db_session.query(Job).update({"last_seen_at": now})
        db_session.commit()

        results = query_jobs(db_session)
        assert len(results) >= 2
        for i in range(len(results) - 1):
            assert results[i]["id"] > results[i + 1]["id"]


# ---------------------------------------------------------------------------
# CSV export — detail_level
# ---------------------------------------------------------------------------


class TestExportCsvDetailLevel:
    """export_csv detail_level controls headers and list flattening."""

    def test_csv_full_headers(self, db_session):
        """Full CSV export should use FULL_COLUMNS as fieldnames."""
        from findjobs.exporter import FULL_COLUMNS, export_csv, query_jobs
        import csv
        import io

        _seed_detail_jobs(db_session)
        jobs = query_jobs(db_session, detail_level="full")
        buf = io.StringIO()
        export_csv(jobs, buf, detail_level="full")
        buf.seek(0)

        reader = csv.DictReader(buf)
        assert reader.fieldnames == FULL_COLUMNS

    def test_csv_full_list_flattening(self, db_session):
        """Full CSV flattens matched_tags and classification_reasons with comma+space."""
        from findjobs.exporter import export_csv, query_jobs
        import csv
        import io

        _seed_detail_jobs(db_session)
        jobs = query_jobs(db_session, detail_level="full")
        buf = io.StringIO()
        export_csv(jobs, buf, detail_level="full")
        buf.seek(0)

        reader = csv.DictReader(buf)
        rows = list(reader)
        # Both list fields are flattened to comma+space separated strings
        for row in rows:
            assert isinstance(row["matched_tags"], str)
            # classification_reasons always has 2+ items → contains comma+space
            assert ", " in row["classification_reasons"]

    def test_csv_full_headers_empty_export(self, db_session):
        """Empty full export CSV should still emit the full header row."""
        from findjobs.exporter import FULL_COLUMNS, export_csv, query_jobs
        import io

        _seed_detail_jobs(db_session)
        # Query with a filter that matches nothing
        jobs = query_jobs(db_session, detail_level="full", status="nonexistent")
        assert len(jobs) == 0

        buf = io.StringIO()
        export_csv(jobs, buf, detail_level="full")
        buf.seek(0)

        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 1  # Only header
        for col in FULL_COLUMNS:
            assert col in lines[0]

    def test_csv_invalid_detail_level_raises_value_error(self, db_session):
        """export_csv with invalid detail_level must raise ValueError before writing."""
        from findjobs.exporter import export_csv, query_jobs
        import io

        _seed_detail_jobs(db_session)
        jobs = query_jobs(db_session, detail_level="full")
        buf = io.StringIO("existing content")

        with pytest.raises(ValueError, match="detail_level"):
            export_csv(jobs, buf, detail_level="bogus")
        # Buffer must be unchanged — no header/data was written
        assert buf.getvalue() == "existing content"

    def test_do_export_invalid_detail_level_value_error(self, db_session):
        """do_export with invalid detail_level must raise ValueError before output."""
        from findjobs.exporter import do_export
        import io

        _seed_detail_jobs(db_session)

        # No output stream — ValueError before any string is built
        with pytest.raises(ValueError, match="detail_level"):
            do_export(db_session, detail_level="bogus")

    def test_do_export_invalid_detail_level_stream_unchanged(self, db_session):
        """do_export with invalid detail_level must not write partial output to stream."""
        from findjobs.exporter import do_export
        import io

        _seed_detail_jobs(db_session)

        buf = io.StringIO("preexisting")
        with pytest.raises(ValueError, match="detail_level"):
            do_export(db_session, output=buf, detail_level="bogus")
        # Stream must be unchanged
        assert buf.getvalue() == "preexisting"


# ---------------------------------------------------------------------------
# CLI export — detail_level
# ---------------------------------------------------------------------------


class TestCliExportDetailLevel:
    """CLI export --detail-level end-to-end tests."""

    def _seed_and_get_db(self) -> str:
        """Seed a temp DB with detail jobs and return its path."""
        from findjobs.db import init_db

        db_path = Path(tempfile.mktemp(suffix=".db"))
        session = init_db(db_path)
        _seed_detail_jobs(session)
        session.close()
        return str(db_path)

    def test_cli_export_summary_omits_long_fields(self):
        """Default CLI export (summary) should not include long fields."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(app, ["export", "--db-path", db_path])
        assert result.exit_code == 0
        for line in result.output.strip().split("\n"):
            obj = json.loads(line)
            assert "description" not in obj
            assert "classification_reasons" not in obj

    def test_cli_export_full_jsonl(self):
        """CLI export --detail-level full includes extra fields."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        result = runner.invoke(
            app, ["export", "--db-path", db_path, "--detail-level", "full"]
        )
        assert result.exit_code == 0
        for line in result.output.strip().split("\n"):
            obj = json.loads(line)
            assert "description" in obj
            assert "relevance_status" in obj
            assert "classification_reasons" in obj
            assert isinstance(obj["classification_reasons"], list)

    def test_cli_export_full_writes_file(self):
        """CLI export --detail-level full --output writes a file with extra fields."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        output_path = Path(tempfile.mktemp(suffix=".jsonl"))
        result = runner.invoke(
            app,
            [
                "export",
                "--db-path",
                db_path,
                "--detail-level",
                "full",
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code == 0
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        obj = json.loads(content.strip().split("\n")[0])
        assert "description" in obj
        assert "classification_reasons" in obj
        output_path.unlink(missing_ok=True)

    def test_cli_export_invalid_detail_level_no_artifact(self):
        """Invalid --detail-level exits non-zero and does not create an output file."""
        from findjobs.cli import app

        runner = CliRunner()
        db_path = self._seed_and_get_db()
        output_path = Path(tempfile.mktemp(suffix=".jsonl"))
        assert not output_path.exists()

        result = runner.invoke(
            app,
            [
                "export",
                "--db-path",
                db_path,
                "--detail-level",
                "bogus",
                "--output",
                str(output_path),
            ],
        )
        assert result.exit_code != 0
        assert not output_path.exists()


# ---------------------------------------------------------------------------
# _safe_stdout_emit — GBK stdout encoding repair
# ---------------------------------------------------------------------------


class TestSafeStdoutEmit:
    """_safe_stdout_emit reconfigures stdout when the active encoding is
    too narrow for the content."""

    def test_writes_nbsp_and_chinese_through_gbk(self):
        """NBSP + Chinese text through a gbk-encoded TextIOWrapper is
        recovered by reconfiguring to UTF-8."""
        from findjobs.cli import _safe_stdout_emit

        buffer = io.BytesIO()
        wrapper = io.TextIOWrapper(buffer, encoding="gbk")
        saved = sys.stdout
        try:
            sys.stdout = wrapper
            text = "安全工程师\xa0salary：50k"

            _safe_stdout_emit(text)

            wrapper.flush()
            written = buffer.getvalue()

            # The underlying bytes are valid UTF-8
            decoded = written.decode("utf-8")
            # The original text appears exactly once (no duplicated partial)
            assert decoded.count(text) == 1
            # Trailing newline from the helper (platform-native via
            # TextIOWrapper, which may be \r\n on Windows)
            assert decoded.rstrip("\r\n") == text
        finally:
            sys.stdout = saved
            wrapper.close()
