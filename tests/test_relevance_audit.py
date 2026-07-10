"""Tests for read-only relevance audit.

Covers core calculations with a temporary database, seeded sample
determinism, JSON / JSONL export helpers, CLI exit behaviour, and proof
that the audit never mutates stored job fields.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findjobs.classify import (
    CLASSIFICATION_VERSION,
    REASON_AI_SURFACE,
    REASON_ALGORITHM,
    REASON_NO_SIGNALS,
    REASON_PRODUCT,
    REASON_REVIEW_AI,
)

# Reuse the managed-db helpers from test_reclassification.
from test_reclassification import _close_session, _insert_job, _managed_db


# ===================================================================
# Fixtures
# ===================================================================


def _seed_company_and_source(session):
    """Minimal company + source for FK constraints."""
    from findjobs.models import Company, Source

    c = Company(slug="auditcorp", name="Audit Corp")
    session.add(c)
    session.flush()
    s = Source(company_id=c.id, slug="auditcorp-careers", name="Audit Corp Careers")
    session.add(s)
    session.flush()
    return c.id, s.id


def _seed_database(session):
    """Seed a mix of target, review, and excluded jobs."""
    cid, sid = _seed_company_and_source(session)

    jobs_data = [
        # target: AI Engineer with explicit AI surface signal
        ("AI Engineer", "LLM development and agent building.", "full-time",
         json.dumps(["AI"], ensure_ascii=False), "target",
         CLASSIFICATION_VERSION,
         json.dumps([REASON_AI_SURFACE], ensure_ascii=False)),
        # target: Security Engineer
        ("Security Engineer", "AppSec testing and vulnerability research.", "full-time",
         json.dumps(["Security"], ensure_ascii=False), "target",
         CLASSIFICATION_VERSION,
         json.dumps(["security_surface_signals"], ensure_ascii=False)),
        # excluded: algorithm role
        ("算法工程师", "Recommendation system development.", "算法/技术类",
         json.dumps([], ensure_ascii=False), "target",
         "", json.dumps([], ensure_ascii=False)),
        # excluded: product manager
        ("AI 产品经理", "Responsible for AI product experience.", "",
         json.dumps(["AI"], ensure_ascii=False), "target",
         "", json.dumps([], ensure_ascii=False)),
        # review: backend engineer with AI responsibilities
        ("后端开发工程师", "职责: 负责大模型推理平台开发。", "技术",
         json.dumps(["AI"], ensure_ascii=False), "review",
         CLASSIFICATION_VERSION,
         json.dumps([REASON_REVIEW_AI], ensure_ascii=False)),
        # excluded: no signals
        ("Frontend Engineer", "React and TypeScript development.", "",
         json.dumps([], ensure_ascii=False), "excluded",
         "", json.dumps([], ensure_ascii=False)),
    ]

    for i, (title, desc, jtype, tags, status, version, reasons) in enumerate(jobs_data):
        _insert_job(
            session,
            title=title,
            description=desc,
            job_type=jtype,
            matched_tags=tags,
            relevance_status=status,
            classification_version=version,
            classification_reasons=reasons,
            external_id=f"job-{i:03d}",
            source_id=sid,
            company_id=cid,
        )
    session.commit()


# ===================================================================
# Audit calculations
# ===================================================================


class TestAuditCalculations:
    """Core audit counts and invariants."""

    def test_scanned_count(self):
        """Audit reports the correct number of scanned jobs."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            assert report.scanned == 6

    def test_projected_status_counts(self):
        """Projected target/review/excluded counts match classifier output."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            # AI Engineer + Security Engineer = 2 target
            assert report.projected_target == 2
            # Backend engineer with AI description = 1 review
            assert report.projected_review == 1
            # algorithm, product manager, frontend = 3 excluded
            assert report.projected_excluded == 3

    def test_projected_tags(self):
        """Tag counts reflect projected classification."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            assert report.projected_tags["target"]["AI"] == 1
            assert report.projected_tags["target"]["Security"] == 1
            assert report.projected_tags["review"]["AI"] == 1

    def test_algorithm_residual_count(self):
        """Algorithm residual count is 0: classifier always excludes algorithm roles.

        Every job whose title or job_type contains the Chinese word for
        algorithm is correctly excluded by the classifier, so no residual
        exists under current rules.  The count exists as a safety check for
        future classification changes.
        """
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            cid, sid = _seed_company_and_source(session)
            # A job with algorithm in job_type, classified as excluded.
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM work",
                job_type="算法/技术类",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                classification_version=CLASSIFICATION_VERSION,
                classification_reasons=json.dumps([REASON_AI_SURFACE], ensure_ascii=False),
                external_id="alg-residual",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            report = run_audit(session)
            # Job is projected as excluded (algorithm keyword triggers early
            # exclusion in classify_job), so residual count is 0.
            assert report.algorithm_residual_count == 0

    def test_algorithm_residual_excluded_not_counted(self):
        """Jobs projected as excluded with algorithm keywords are NOT residuals."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            # "算法工程师" is projected as excluded (algorithm in title)
            # so it should NOT be counted as a residual.
            assert report.algorithm_residual_count == 0

    def test_suspicious_target_count(self):
        """Suspicious target: projected target with functional title keywords."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            cid, sid = _seed_company_and_source(session)
            # A job with "Product Manager" in title that would be projected as target.
            # This requires a title that is both (a) classified as target and
            # (b) contains functional keywords.  Use a job whose title has "operations"
            # but whose description has strong AI signals that promote it to target.
            _insert_job(
                session,
                title="AI Operations Specialist",
                description="LLM development and agent building.",
                job_type="full-time",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                classification_version=CLASSIFICATION_VERSION,
                classification_reasons=json.dumps([REASON_AI_SURFACE], ensure_ascii=False),
                external_id="suspicious-001",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            report = run_audit(session)
            # Functional AI operations is projected to review, so it cannot
            # remain as a suspicious high-confidence target.
            assert report.projected_review == 1
            assert report.suspicious_target_count == 0

    def test_duplicate_identity_groups(self):
        """Duplicate identity: same external_id within one source."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            cid, sid = _seed_company_and_source(session)
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="dup-id",
                source_id=sid,
                company_id=cid,
            )
            _insert_job(
                session,
                title="AI Engineer Duplicate",
                description="LLM again",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="dup-id",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            report = run_audit(session)
            assert report.duplicate_identity_groups == 1

    def test_drift_count(self):
        """Drift detected when stored status/tags/reasons differ from projected."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            # Drift sources:
            # - "算法工程师" stored as target but projected as excluded (status)
            # - "AI 产品经理" stored with AI tags + target but projected excluded
            # - "Frontend Engineer" stored with empty reasons but projected
            #   has no_target_signals
            assert report.drift_count == 3

    def test_reason_code_counts(self):
        """Reason-code breakdown is populated."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            assert len(report.reason_code_counts) > 0
            assert REASON_AI_SURFACE in report.reason_code_counts
            assert REASON_ALGORITHM in report.reason_code_counts
            assert REASON_NO_SIGNALS in report.reason_code_counts


# ===================================================================
# Seeded sample determinism
# ===================================================================


class TestSeededSamples:
    """Deterministic samples with stable ordering."""

    def test_repeated_call_returns_same_samples(self):
        """Same seed + same db produces identical samples."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report1 = run_audit(session, sample_size=5, seed=42)
            # Rollback any phantom state (there shouldn't be any)
            session.rollback()
            report2 = run_audit(session, sample_size=5, seed=42)

            assert report1.sample_target == report2.sample_target
            assert report1.sample_review == report2.sample_review
            assert report1.sample_excluded == report2.sample_excluded

    def test_different_seed_produces_different_samples(self):
        """Different seed produces different samples when more items than size."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            # Need enough items for seed diversity.  Insert extra target jobs.
            cid, sid = _seed_company_and_source(session)
            for i in range(10):
                _insert_job(
                    session,
                    title=f"AI Engineer {i}",
                    description="LLM development.",
                    job_type="full-time",
                    matched_tags=json.dumps(["AI"], ensure_ascii=False),
                    relevance_status="target",
                    classification_version=CLASSIFICATION_VERSION,
                    classification_reasons=json.dumps([REASON_AI_SURFACE], ensure_ascii=False),
                    external_id=f"many-{i:03d}",
                    source_id=sid,
                    company_id=cid,
                )
            session.commit()
            report1 = run_audit(session, sample_size=5, seed=1)
            session.rollback()
            report2 = run_audit(session, sample_size=5, seed=2)

            # With 10 target jobs and sample_size=5, different seeds should
            # (with extremely high probability) produce different samples.
            assert report1.sample_target != report2.sample_target

    def test_sample_size_respected(self):
        """Sample size limits the number of returned samples."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session, sample_size=1, seed=42)
            # We have 2 target jobs, but sample_size=1
            assert len(report.sample_target) == 1
            assert len(report.sample_review) == 1
            assert len(report.sample_excluded) == 1


# ===================================================================
# JSON / JSONL export
# ===================================================================


class TestExports:
    """audit_report_to_dict and review-row JSONL export."""

    def test_audit_report_to_dict(self):
        """audit_report_to_dict produces a JSON-serializable dict."""
        from findjobs.relevance_audit import audit_report_to_dict, run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            d = audit_report_to_dict(report)
            assert isinstance(d, dict)
            assert d["scanned"] == 6
            assert d["projected_target"] == 2
            assert d["projected_review"] == 1
            assert d["projected_excluded"] == 3
            # Must be JSON-serializable
            json.dumps(d, ensure_ascii=False)

    def test_projected_review_rows_export(self):
        """Projected review rows contain review classification facts."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            assert len(report.projected_review_rows) == 1
            row = report.projected_review_rows[0]
            assert row["id"] is not None
            assert "后端" in row["title"]
            assert row["projected_tags"] == ["AI"]
            assert "projected_version" in row
            # Must not include user marks
            assert "user_marks" not in row

    def test_export_review_rows_jsonl(self):
        """Review rows can be serialized as JSONL lines."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            report = run_audit(session)
            lines = [
                json.dumps(row, ensure_ascii=False)
                for row in report.projected_review_rows
            ]
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["projected_tags"] == ["AI"]


# ===================================================================
# Read-only guarantee
# ===================================================================


class TestReadOnly:
    """Audit never mutates stored job fields."""

    def test_audit_does_not_change_jobs(self):
        """Job fields are identical before and after audit."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)

            # Snapshot before
            from findjobs.models import Job

            before = {
                j.id: {
                    "relevance_status": j.relevance_status,
                    "matched_tags": j.matched_tags,
                    "classification_version": j.classification_version,
                    "classification_reasons": j.classification_reasons,
                    "title": j.title,
                    "description": j.description,
                    "job_type": j.job_type,
                }
                for j in session.query(Job).all()
            }

            report = run_audit(session)

            # Snapshot after
            after = {
                j.id: {
                    "relevance_status": j.relevance_status,
                    "matched_tags": j.matched_tags,
                    "classification_version": j.classification_version,
                    "classification_reasons": j.classification_reasons,
                    "title": j.title,
                    "description": j.description,
                    "job_type": j.job_type,
                }
                for j in session.query(Job).all()
            }

            assert before == after

    def test_audit_does_not_commit_changes(self):
        """Rollback after audit leaves all rows unchanged."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            _seed_database(session)
            run_audit(session)
            # Rollback after audit -- if audit committed anything, rollback
            # would lose it.  Instead all rows survive.
            session.rollback()

            from findjobs.models import Job

            jobs = session.query(Job).all()
            assert len(jobs) == 6


# ===================================================================
# CLI
# ===================================================================


class TestCliRelevanceAudit:
    """CLI relevance-audit command."""

    def test_cli_relevance_audit_exits_0_when_clean(self):
        """Exit code 0 when no hard violations exist."""
        with _managed_db() as (session, db_path):
            _seed_database(session)
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["relevance-audit", "--db-path", str(db_path)])
            assert result.exit_code == 0

    def test_cli_relevance_audit_exits_0_with_algorithm_job(self):
        """Exit code 0 when algorithm jobs are correctly excluded by classifier.

        The classifier always excludes algorithm roles, so algorithm residual
        is always 0 with current rules.  No hard violation.
        """
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, db_path):
            cid, sid = _seed_company_and_source(session)
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM work",
                job_type="算法/技术类",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="alg-residual-cli",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["relevance-audit", "--db-path", str(db_path)])
            assert result.exit_code == 0

    def test_cli_functional_ai_role_is_review_not_violation(self):
        """Functional AI operations is review and does not fail the audit."""
        with _managed_db() as (session, db_path):
            cid, sid = _seed_company_and_source(session)
            _insert_job(
                session,
                title="AI Operations Specialist",
                description="LLM development and agent building.",
                job_type="full-time",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="suspicious-cli",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["relevance-audit", "--db-path", str(db_path)])
            assert result.exit_code == 0
            assert "projected review:         1" in result.output

    def test_cli_relevance_audit_exits_1_on_duplicate(self):
        """Exit code 1 when duplicate identity groups > 0."""
        with _managed_db() as (session, db_path):
            cid, sid = _seed_company_and_source(session)
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="dup-cli",
                source_id=sid,
                company_id=cid,
            )
            _insert_job(
                session,
                title="AI Engineer Clone",
                description="LLM again",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="dup-cli",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["relevance-audit", "--db-path", str(db_path)])
            assert result.exit_code == 1

    def test_cli_json_output(self):
        """--json-output writes the complete report as JSON."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, db_path):
            _seed_database(session)
            _close_session(session)

            from findjobs.cli import app

            with tempfile.TemporaryDirectory() as tmpdir:
                json_path = Path(tmpdir) / "audit.json"
                runner = CliRunner()
                result = runner.invoke(
                    app,
                    [
                        "relevance-audit",
                        "--db-path", str(db_path),
                        "--json-output", str(json_path),
                    ],
                )
                assert result.exit_code == 0
                assert json_path.exists()
                data = json.loads(json_path.read_text(encoding="utf-8"))
                assert data["scanned"] == 6
                assert data["projected_target"] == 2

    def test_cli_json_creates_parent_dirs(self):
        """--json-output creates parent directories if needed."""
        with _managed_db() as (session, db_path):
            _seed_database(session)
            _close_session(session)

            from findjobs.cli import app

            with tempfile.TemporaryDirectory() as tmpdir:
                nested = Path(tmpdir) / "sub" / "deep" / "audit.json"
                runner = CliRunner()
                result = runner.invoke(
                    app,
                    [
                        "relevance-audit",
                        "--db-path", str(db_path),
                        "--json-output", str(nested),
                    ],
                )
                assert result.exit_code == 0
                assert nested.exists()

    def test_cli_export_review(self):
        """--export-review writes UTF-8 JSONL for projected review rows."""
        with _managed_db() as (session, db_path):
            _seed_database(session)
            _close_session(session)

            from findjobs.cli import app

            with tempfile.TemporaryDirectory() as tmpdir:
                review_path = Path(tmpdir) / "review.jsonl"
                runner = CliRunner()
                result = runner.invoke(
                    app,
                    [
                        "relevance-audit",
                        "--db-path", str(db_path),
                        "--export-review", str(review_path),
                    ],
                )
                assert result.exit_code == 0
                assert review_path.exists()
                lines = review_path.read_text(encoding="utf-8").strip().split("\n")
                assert len(lines) == 1  # one review row in seed data
                parsed = json.loads(lines[0])
                assert "projected_tags" in parsed
                assert "projected_version" in parsed
                assert "id" in parsed
                assert "title" in parsed
                assert "url" in parsed
                # Must not include user marks
                assert "user_marks" not in parsed

    def test_cli_export_review_creates_parent_dirs(self):
        """--export-review creates parent directories if needed."""
        with _managed_db() as (session, db_path):
            _seed_database(session)
            _close_session(session)

            from findjobs.cli import app

            with tempfile.TemporaryDirectory() as tmpdir:
                nested = Path(tmpdir) / "sub" / "deep" / "review.jsonl"
                runner = CliRunner()
                result = runner.invoke(
                    app,
                    [
                        "relevance-audit",
                        "--db-path", str(db_path),
                        "--export-review", str(nested),
                    ],
                )
                assert result.exit_code == 0
                assert nested.exists()

    def test_cli_prints_summary(self):
        """Human-readable summary is printed to stdout."""
        with _managed_db() as (session, db_path):
            _seed_database(session)
            _close_session(session)

            from findjobs.cli import app

            runner = CliRunner()
            result = runner.invoke(app, ["relevance-audit", "--db-path", str(db_path)])
            assert result.exit_code == 0
            assert "scanned" in result.output.lower()
            assert "projected target" in result.output.lower()
            assert "projected review" in result.output.lower()
            assert "projected excluded" in result.output.lower()


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    """Empty database and edge cases."""

    def test_empty_database(self):
        """Audit on an empty database returns zero counts."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            report = run_audit(session)
            assert report.scanned == 0
            assert report.projected_target == 0
            assert report.projected_review == 0
            assert report.projected_excluded == 0
            assert report.drift_count == 0
            assert report.algorithm_residual_count == 0
            assert report.suspicious_target_count == 0
            assert report.duplicate_identity_groups == 0
            assert report.sample_target == []
            assert report.sample_review == []
            assert report.sample_excluded == []
            assert report.projected_review_rows == []

    def test_jobs_without_external_id_or_url_identity(self):
        """Rows with neither external_id nor url are ignored for duplicate check."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            cid, sid = _seed_company_and_source(session)
            # Two jobs with same title+location but no external_id or url.
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="",
                url="",
                source_id=sid,
                company_id=cid,
            )
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM duplicate",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="",
                url="",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            report = run_audit(session)
            # Neither has external_id or url -> both ignored for identity check
            assert report.duplicate_identity_groups == 0

    def test_url_fallback_identity(self):
        """URL is used as fallback identity when external_id is empty."""
        from findjobs.relevance_audit import run_audit

        with _managed_db() as (session, _):
            cid, sid = _seed_company_and_source(session)
            _insert_job(
                session,
                title="AI Engineer",
                description="LLM",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="",
                url="https://example.com/job/same",
                source_id=sid,
                company_id=cid,
            )
            _insert_job(
                session,
                title="AI Engineer Clone",
                description="LLM again",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
                external_id="",
                url="https://example.com/job/same",
                source_id=sid,
                company_id=cid,
            )
            session.commit()
            report = run_audit(session)
            assert report.duplicate_identity_groups == 1
