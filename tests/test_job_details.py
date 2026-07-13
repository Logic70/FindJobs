"""Tests for canonical job detail normalization (Phase 3B1).

Covers :func:`~findjobs.job_details.normalize_job_details`,
:func:`~findjobs.job_details.compute_detail_completeness`, and the persistence
integration in :func:`~findjobs.collection.upsert_job`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text as sa_text
from sqlalchemy.orm import Session, sessionmaker

from findjobs.job_details import (
    NormalizedJobDetails,
    compute_detail_completeness,
    normalize_job_details,
)

# ---------------------------------------------------------------------------
# Fixtures for integration tests
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
# Unit: normalize_job_details
# ===================================================================


class TestNormalizeChinese:
    def test_full_split_both_headings(self):
        """Chinese description with both headings splits correctly."""
        desc = "公司简介\n\n岗位职责\n开发软件\n\n任职要求\n本科以上"
        result = normalize_job_details(desc)
        assert result.responsibilities == "开发软件"
        assert result.requirements == "本科以上"
        assert result.detail_completeness == "full"
        assert result.description == desc

    def test_duties_only_tencent_like(self):
        """Only a 岗位职责 heading — responsibilities_only."""
        desc = "## 岗位职责\n负责后端开发\n维护系统"
        result = normalize_job_details(desc)
        assert result.responsibilities == "负责后端开发\n维护系统"
        assert result.requirements == ""
        assert result.detail_completeness == "responsibilities_only"

    def test_requirements_only(self):
        """Only a 任职要求 heading — requirements_only."""
        desc = "任职要求：\n熟悉Python\n有3年经验"
        result = normalize_job_details(desc)
        assert result.responsibilities == ""
        assert result.requirements == "熟悉Python\n有3年经验"
        assert result.detail_completeness == "requirements_only"

    def test_markdown_numbered_headings(self):
        """Markdown and numbering prefixes are accepted."""
        desc = "### 1. 工作职责\n写代码\n### 2. 任职要求\n懂算法"
        result = normalize_job_details(desc)
        assert result.responsibilities == "写代码"
        assert result.requirements == "懂算法"

    def test_all_chinese_variants(self):
        """Various Chinese responsibility headings all work."""
        for heading in ["职位描述", "岗位描述", "职责"]:
            desc = f"{heading}\n内容A"
            result = normalize_job_details(desc)
            assert result.responsibilities == "内容A", f"Failed for {heading}"

    def test_all_chinese_requirement_variants(self):
        """Various Chinese requirement headings all work."""
        for heading in ["岗位要求", "职位要求", "资格要求", "任职资格"]:
            desc = f"{heading}\n内容B"
            result = normalize_job_details(desc)
            assert result.requirements == "内容B", f"Failed for {heading}"


class TestNormalizeEnglish:
    def test_full_split(self):
        """English description with both headings splits correctly."""
        desc = (
            "Company overview\n\n"
            "Responsibilities\n"
            "Build software\n\n"
            "Requirements\n"
            "5+ years exp"
        )
        result = normalize_job_details(desc)
        assert result.responsibilities == "Build software"
        assert result.requirements == "5+ years exp"
        assert result.detail_completeness == "full"

    def test_what_youll_do(self):
        """'What you'll do' recognized as responsibility heading."""
        desc = "What you'll do\nDesign APIs\n\nQualifications\nDegree"
        result = normalize_job_details(desc)
        assert result.responsibilities == "Design APIs"
        assert result.requirements == "Degree"

    def test_what_were_looking_for(self):
        """'What we're looking for' recognized as requirement heading."""
        desc = "Responsibilities\nLead team\n\nWhat we're looking for\n5 yrs exp"
        result = normalize_job_details(desc)
        assert result.responsibilities == "Lead team"
        assert result.requirements == "5 yrs exp"

    def test_numbered_markdown_english(self):
        """Numbered English headings with markdown."""
        desc = "## 1. Responsibilities\nCode\n## 2. Requirements\nTests"
        result = normalize_job_details(desc)
        assert result.responsibilities == "Code"
        assert result.requirements == "Tests"


class TestNormalizeEdgeCases:
    def test_combined_only(self):
        """Description without headings yields combined_only."""
        desc = "This is a job posting with a description but no sections."
        result = normalize_job_details(desc)
        assert result.responsibilities == ""
        assert result.requirements == ""
        assert result.detail_completeness == "combined_only"

    def test_missing(self):
        """Empty description with no inputs yields missing."""
        result = normalize_job_details("")
        assert result.responsibilities == ""
        assert result.requirements == ""
        assert result.detail_completeness == "missing"

    def test_explicit_fields_override_split(self):
        """Explicit fields win; only the missing field is inferred."""
        desc = "岗位职责\n开发\n任职要求\n本科"
        result = normalize_job_details(desc, responsibilities="lead dev")
        assert result.responsibilities == "lead dev"  # explicit wins
        assert result.requirements == "本科"  # inferred
        assert result.detail_completeness == "full"

    def test_heading_like_in_prose_no_split(self):
        """'要求'/'职责' in ordinary prose does not trigger a split."""
        desc = "我们要求候选人具备团队合作精神。职责包括日常开发。"
        result = normalize_job_details(desc)
        assert result.responsibilities == ""
        assert result.requirements == ""
        assert result.detail_completeness == "combined_only"

    def test_original_description_preserved(self):
        """The description field is never modified."""
        desc = "岗位职责\nwork\n任职要求\nskills"
        result = normalize_job_details(desc)
        assert result.description == desc

    def test_only_explicit_fields_no_description(self):
        """Both explicit fields without description yields full."""
        result = normalize_job_details("", responsibilities="a", requirements="b")
        assert result.responsibilities == "a"
        assert result.requirements == "b"
        assert result.detail_completeness == "full"

    def test_text_before_first_heading_stays_in_description(self):
        """Text before the first heading remains only in description."""
        desc = "公司介绍\n我们是一家好公司\n岗位职责\n写代码\n任职要求\n本科"
        result = normalize_job_details(desc)
        assert result.responsibilities == "写代码"
        assert result.requirements == "本科"
        assert "公司介绍" not in result.responsibilities
        assert "公司介绍" not in result.requirements

    def test_source_order_full_split(self):
        """Requirements before responsibilities — correct mapping."""
        desc = "任职要求\n经验\n岗位职责\n干活"
        result = normalize_job_details(desc)
        assert result.requirements == "经验"
        assert result.responsibilities == "干活"

    def test_only_markdown_heading_no_content(self):
        """Heading line with nothing after it yields empty field, combined_only."""
        desc = "## Responsibilities\n\n## Requirements\n\n"
        result = normalize_job_details(desc)
        assert result.responsibilities == ""
        assert result.requirements == ""
        assert result.detail_completeness == "combined_only"  # desc has heading text

    def test_colon_variants(self):
        """Chinese and English colons after heading are accepted."""
        desc_cn = "岗位职责：\n工作A\n任职要求：\n要求A"
        assert normalize_job_details(desc_cn).detail_completeness == "full"

        desc_en = "Responsibilities:\nWork\nRequirements:\nSkills"
        assert normalize_job_details(desc_en).detail_completeness == "full"


# ===================================================================
# Unit: compute_detail_completeness
# ===================================================================


class TestComputeCompleteness:
    def test_full(self):
        assert compute_detail_completeness("desc", "resp", "req") == "full"

    def test_responsibilities_only(self):
        assert compute_detail_completeness("desc", "resp", "") == "responsibilities_only"
        assert compute_detail_completeness("", "resp", "") == "responsibilities_only"

    def test_requirements_only(self):
        assert compute_detail_completeness("desc", "", "req") == "requirements_only"
        assert compute_detail_completeness("", "", "req") == "requirements_only"

    def test_combined_only(self):
        assert compute_detail_completeness("desc", "", "") == "combined_only"

    def test_missing(self):
        assert compute_detail_completeness("", "", "") == "missing"


# ===================================================================
# Integration: persistence via upsert_job
# ===================================================================


class TestDetailPersistence:
    """Require a seeded database session (db_session fixture)."""

    def test_insert_persistence(self, db_session):
        """Insert stores responsibilities, requirements, and completeness."""
        from findjobs.collection import CollectedJob, upsert_job

        cj = CollectedJob(
            title="SWE",
            description="岗位职责\ncode\n任职要求\ndegree",
        )
        upsert_job(db_session, 1, 1, 1, cj)
        db_session.commit()

        row = db_session.execute(
            sa_text("SELECT responsibilities, requirements, detail_completeness "
                    "FROM jobs WHERE title='SWE'")
        ).fetchone()
        assert row[0] == "code"
        assert row[1] == "degree"
        assert row[2] == "full"

    def test_update_fills_missing_details(self, db_session):
        """Update fills blank responsibilities/requirements via description."""
        from findjobs.collection import CollectedJob, upsert_job

        _insert_job(db_session, id=10, title="JobA", description="old desc")
        db_session.commit()

        cj = CollectedJob(
            title="JobA",
            description="任职要求\n本科",
        )
        upsert_job(db_session, 1, 1, 2, cj)
        db_session.commit()

        row = db_session.execute(
            sa_text("SELECT responsibilities, requirements, detail_completeness "
                    "FROM jobs WHERE id=10")
        ).fetchone()
        assert row[0] == ""  # no resp heading in description
        assert row[1] == "本科"
        assert row[2] == "requirements_only"

    def test_update_does_not_erase_richer_details(self, db_session):
        """Existing nonempty resp/req survives when incoming is empty."""
        from findjobs.collection import CollectedJob, upsert_job

        _insert_job(
            db_session,
            id=20,
            title="JobB",
            description="old desc",
            responsibilities="existing resp",
            requirements="existing req",
            detail_completeness="full",
        )
        db_session.commit()

        # New collect has no explicit fields and description has no headings.
        cj = CollectedJob(title="JobB", description="new desc no headings")
        upsert_job(db_session, 1, 1, 3, cj)
        db_session.commit()

        row = db_session.execute(
            sa_text("SELECT responsibilities, requirements, detail_completeness "
                    "FROM jobs WHERE id=20")
        ).fetchone()
        assert row[0] == "existing resp"  # not erased
        assert row[1] == "existing req"  # not erased
        assert row[2] == "full"  # recomputed: both nonempty merged

    def test_completeness_recomputed_on_update(self, db_session):
        """Completeness correctly recalculated after merge."""
        from findjobs.collection import CollectedJob, upsert_job

        _insert_job(
            db_session,
            id=30,
            title="JobC",
            description="old",
            responsibilities="old resp",
            requirements="",
            detail_completeness="responsibilities_only",
        )
        db_session.commit()

        # New collect brings requirements via description split
        cj = CollectedJob(
            title="JobC",
            description="职位要求\nnew req\n岗位职责\nnew resp",
        )
        upsert_job(db_session, 1, 1, 4, cj)
        db_session.commit()

        row = db_session.execute(
            sa_text("SELECT responsibilities, requirements, detail_completeness "
                    "FROM jobs WHERE id=30")
        ).fetchone()
        # responsibilities: incoming 'new resp' is nonempty → overrides 'old resp'
        assert row[0] == "new resp"
        assert row[1] == "new req"
        assert row[2] == "full"

    def test_insert_with_explicit_fields(self, db_session):
        """Explicit responsibilities/requirements on insert."""
        from findjobs.collection import CollectedJob, upsert_job

        cj = CollectedJob(
            title="ExplicitJob",
            description="Some description text",
            responsibilities="custom resp",
            requirements="custom req",
        )
        upsert_job(db_session, 1, 1, 5, cj)
        db_session.commit()

        row = db_session.execute(
            sa_text("SELECT responsibilities, requirements, detail_completeness "
                    "FROM jobs WHERE title='ExplicitJob'")
        ).fetchone()
        assert row[0] == "custom resp"
        assert row[1] == "custom req"
        assert row[2] == "full"

    def test_insert_missing_all_fields(self, db_session):
        """Insert with empty description and no details yields missing."""
        from findjobs.collection import CollectedJob, upsert_job

        cj = CollectedJob(title="EmptyJob", description="")
        upsert_job(db_session, 1, 1, 6, cj)
        db_session.commit()

        row = db_session.execute(
            sa_text("SELECT responsibilities, requirements, detail_completeness "
                    "FROM jobs WHERE title='EmptyJob'")
        ).fetchone()
        assert row[0] == ""
        assert row[1] == ""
        assert row[2] == "missing"


# ===================================================================
# Integration: collect_jobs
# ===================================================================


class TestCollectJobsIntegration:
    def test_collect_jobs_sets_detail_completeness(self, db_session):
        """The batch collect_jobs path also sets detail fields."""
        from findjobs.collection import CollectedJob, collect_jobs

        jobs = [
            CollectedJob(
                title="AI Engineer",
                description="Responsibilities\nwork\nRequirements\nskills",
            ),
            CollectedJob(
                title="MissingJob",
                description="",
            ),
        ]
        total, new = collect_jobs(db_session, 1, 1, 1, jobs)
        db_session.commit()

        assert total == 1  # MissingJob filtered out (no AI/security tags)
        assert new == 1
        row = db_session.execute(
            sa_text("SELECT detail_completeness FROM jobs WHERE title='AI Engineer'")
        ).fetchone()
        assert row[0] == "full"
