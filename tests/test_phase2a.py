"""Phase 2A tests: explainable high-precision classification contract.

Tests cover:

* :func:`~findjobs.classify.classify_job_detailed` — the new detailed API
* :func:`~findjobs.classify.classify_job` backward compatibility
* :func:`~findjobs.collection.collect_jobs` centralised classification
* :func:`~findjobs.maintenance.reclassify_jobs` moved-to-review counting

Every fixture in the "known false positives / representative positives"
sections asserts tags, relevance status, and at least one expected reason code.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from findjobs.classify import (
    CLASSIFICATION_VERSION,
    DetailedClassification,
    classify_job,
    classify_job_detailed,
    REASON_ALGORITHM,
    REASON_PRODUCT,
    REASON_BUSINESS_OPS,
    REASON_ANALYSIS,
    REASON_PLANNING,
    REASON_SALES,
    REASON_PROJECT,
    REASON_INTERN,
    REASON_QA_DESIGN_LEGAL,
    REASON_NO_SIGNALS,
    REASON_NON_TARGET_INFRASTRUCTURE,
    REASON_AI_SURFACE,
    REASON_SECURITY_SURFACE,
    REASON_AI_SECURITY_SURFACE,
    REASON_REVIEW_AI,
    REASON_REVIEW_SECURITY,
    REASON_REVIEW_AI_SECURITY,
)


# ===================================================================
# classify_job_detailed  —  excluded cases
# ===================================================================


class TestDetailedExcluded:
    """classify_job_detailed returns excluded for out-of-scope roles.

    Each test asserts empty tags, ``excluded`` status, and a reason code.
    """

    def test_minimax_creative_planning(self):
        """Creative planning roles are excluded."""
        result = classify_job_detailed(
            "创意策划师（AI科技方向）",
            "职责: 负责AI创意策划和内容生成。",
            "策划类",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_PLANNING in result.reasons
        assert result.version == CLASSIFICATION_VERSION

    def test_generic_dba(self):
        """Generic DBA roles without AI signals are excluded."""
        result = classify_job_detailed(
            "DBA",
            "Responsible for database administration and performance tuning.",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_NON_TARGET_INFRASTRUCTURE in result.reasons

    def test_generic_linux_kernel(self):
        """Generic Linux/kernel engineer without AI signals is excluded."""
        result = classify_job_detailed(
            "Linux 内核开发工程师",
            "负责Linux内核驱动开发和性能优化。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_NO_SIGNALS in result.reasons

    def test_generic_vehicle_system_architect(self):
        """Generic vehicle-system architect is excluded (no AI/security)."""
        result = classify_job_detailed(
            "整车系统架构师",
            "负责整车系统架构设计。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        # Not caught by a hard-exclusion pattern, so no_target_signals
        assert REASON_NO_SIGNALS in result.reasons

    def test_baidu_risk_product_manager(self):
        """Risk product-manager roles are excluded."""
        result = classify_job_detailed(
            "百度风控产品经理",
            "职责: 负责风控产品规划和需求管理。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_PRODUCT in result.reasons

    def test_risk_strategy_operations(self):
        """Risk strategy operations are excluded (business/risk ops)."""
        result = classify_job_detailed(
            "风控策略运营",
            "负责风控策略制定、用户分层和指标分析。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_BUSINESS_OPS in result.reasons

    def test_bytedance_ad_risk_operations(self):
        """ByteDance ad risk operations are excluded."""
        result = classify_job_detailed(
            "广告风控运营专家",
            "职责: 负责反欺诈、风控策略和风险治理。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_BUSINESS_OPS in result.reasons

    def test_game_economic_security_operations(self):
        """Game economic-security operations are excluded."""
        result = classify_job_detailed(
            "游戏经济安全运营",
            "职责: 负责游戏经济系统风控运营。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_BUSINESS_OPS in result.reasons

    def test_game_system_planner_security_direction(self):
        """Game system planner security direction is excluded."""
        result = classify_job_detailed(
            "游戏系统策划（安全方向）",
            "职责: 负责游戏系统规划和安全设计。",
            "策划类",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_BUSINESS_OPS in result.reasons

    def test_ai_product_manager(self):
        """AI product manager roles are excluded."""
        result = classify_job_detailed(
            "AI 产品经理",
            "负责 AI 产品体验与服务。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_PRODUCT in result.reasons

    def test_ai_privacy_product_operations(self):
        """AI privacy product operations are excluded."""
        result = classify_job_detailed(
            "AI隐私产品运营",
            "职责: 负责隐私产品运营和用户反馈。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_PRODUCT in result.reasons

    def test_sales_of_ai_security_products(self):
        """Sales of AI/security products is excluded."""
        result = classify_job_detailed(
            "销售中心-东区销售负责人",
            (
                "岗位描述: 负责网易云信、易盾（内容安全）、"
                "AI 大模型 / 智能体等全线产品在华东区域的"
                "销售策略制定、业绩目标拆解与落地执行。"
            ),
            "销售",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_SALES in result.reasons

    def test_security_algorithm(self):
        """Security algorithm roles are excluded."""
        result = classify_job_detailed(
            "资深反作弊算法工程师",
            "负责风控反作弊",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_ALGORITHM in result.reasons

    def test_ai_algorithm(self):
        """AI algorithm roles are excluded."""
        result = classify_job_detailed(
            "AI安全算法工程师",
            "负责AI安全算法",
            "算法",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_ALGORITHM in result.reasons


# ===================================================================
# classify_job_detailed  —  target cases
# ===================================================================


class TestDetailedTarget:
    """classify_job_detailed returns target for high-confidence roles.

    Each test asserts non-empty tags, ``target`` status, and a reason code
    matching the surface signal that triggered classification.
    """

    def test_ai_gateway_platform_rd(self):
        """AI gateway platform R&D is target AI."""
        result = classify_job_detailed(
            "AI网关平台研发工程师",
            "负责AI网关平台架构设计和研发。",
        )
        assert "AI" in result.tags
        assert result.relevance_status == "target"
        assert REASON_AI_SURFACE in result.reasons

    def test_large_model_application_developer(self):
        """Large-model application developer is target AI."""
        result = classify_job_detailed(
            "大模型应用开发工程师",
            "负责大模型应用开发和推理优化。",
        )
        assert "AI" in result.tags
        assert result.relevance_status == "target"
        assert REASON_AI_SURFACE in result.reasons

    def test_agent_engineer(self):
        """Agent engineer is target AI."""
        result = classify_job_detailed(
            "Agent工程师",
            "Build and deploy AI agent workflows.",
        )
        assert "AI" in result.tags
        assert result.relevance_status == "target"
        assert REASON_AI_SURFACE in result.reasons

    def test_mlops_engineer(self):
        """MLOps engineer is target AI."""
        result = classify_job_detailed(
            "MLOps Engineer",
            "CI/CD for ML pipelines and model deployment.",
        )
        assert "AI" in result.tags
        assert result.relevance_status == "target"
        assert REASON_AI_SURFACE in result.reasons

    def test_model_inference_deployment_engineer(self):
        """Model inference/deployment engineer is target AI."""
        result = classify_job_detailed(
            "模型推理部署工程师",
            "负责模型推理、部署和推理框架优化。",
        )
        assert "AI" in result.tags
        assert result.relevance_status == "target"
        assert REASON_AI_SURFACE in result.reasons

    def test_ai_security_researcher(self):
        """AI security researcher is target AI and Security."""
        result = classify_job_detailed(
            "AI Security Researcher",
            "Red teaming LLMs and AI security research.",
        )
        assert "AI" in result.tags
        assert "Security" in result.tags
        assert result.relevance_status == "target"
        assert REASON_AI_SURFACE in result.reasons
        assert REASON_SECURITY_SURFACE in result.reasons
        assert REASON_AI_SECURITY_SURFACE in result.reasons

    def test_appsec_sdl_engineer(self):
        """AppSec/SDL engineer is target Security."""
        result = classify_job_detailed(
            "AppSec Engineer",
            "Application security and SDL implementation.",
        )
        assert "Security" in result.tags
        assert result.relevance_status == "target"
        assert REASON_SECURITY_SURFACE in result.reasons

    def test_penetration_vulnerability_engineer(self):
        """Penetration/vulnerability engineer is target Security."""
        result = classify_job_detailed(
            "漏洞渗透工程师",
            "负责渗透测试和漏洞研究。",
        )
        assert "Security" in result.tags
        assert result.relevance_status == "target"
        assert REASON_SECURITY_SURFACE in result.reasons

    def test_data_cloud_security_rd(self):
        """Data/cloud security R&D is target Security."""
        result = classify_job_detailed(
            "数据安全开发工程师",
            "负责数据安全产品开发和体系设计。",
        )
        assert "Security" in result.tags
        assert result.relevance_status == "target"
        assert REASON_SECURITY_SURFACE in result.reasons

    def test_incident_response_detection_engineer(self):
        """Incident-response/detection engineer is target Security."""
        result = classify_job_detailed(
            "安全响应工程师",
            "负责安全事件响应和检测分析。",
            "安全",
        )
        assert "Security" in result.tags
        assert result.relevance_status == "target"
        assert REASON_SECURITY_SURFACE in result.reasons

    def test_anti_fraud_engineering_backend(self):
        """Anti-fraud engineering backend is target Security."""
        result = classify_job_detailed(
            "反作弊工程师",
            "建设黑灰产识别、账号安全、风控系统和实时拦截平台。",
        )
        assert "Security" in result.tags
        assert result.relevance_status == "target"
        assert REASON_SECURITY_SURFACE in result.reasons


# ===================================================================
# classify_job_detailed  —  review cases
# ===================================================================


class TestDetailedReview:
    """classify_job_detailed returns review for ambiguous engineering roles
    with strong description evidence."""

    def test_backend_platform_ai_responsibilities(self):
        """Backend/platform title with direct AI platform/model-serving
        responsibilities is review."""
        result = classify_job_detailed(
            "后端开发工程师",
            "职责: 负责大模型推理平台开发、模型部署和AI应用架构设计。",
        )
        assert "AI" in result.tags
        assert result.relevance_status == "review"
        assert REASON_REVIEW_AI in result.reasons

    def test_generic_platform_engineer_security_responsibilities(self):
        """Generic platform engineer with direct security-platform
        development responsibilities is review."""
        result = classify_job_detailed(
            "平台工程师",
            "职责: 负责零信任、WAF 等安全系统控制面、日志流等模块的研发以及维护工作。",
            "技术",
        )
        assert "Security" in result.tags
        assert result.relevance_status == "review"
        assert REASON_REVIEW_SECURITY in result.reasons

    def test_database_engineer_ai_platform(self):
        """A DBA title remains outside the target scope despite AI context."""
        result = classify_job_detailed(
            "MySQL DBA",
            "职责: 负责大模型训练平台数据库架构设计、推理集群数据库优化。",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"


# ===================================================================
# classify_job_detailed  —  AI/security in requirements only
# ===================================================================


class TestDetailedRequirementsOnly:
    """AI/security mentioned only in requirements/preferred qualifications
    must NOT promote the job."""

    def test_ai_in_requirements_not_responsibilities(self):
        """AI mentioned only in requirements section: excluded."""
        result = classify_job_detailed(
            "游戏服务器开发工程师",
            (
                "岗位要求: 具备计算机网络安全、编码安全的基本知识。"
                "岗位描述: 负责游戏服务器引擎开发和游戏逻辑开发。"
            ),
            "游戏程序",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_NO_SIGNALS in result.reasons

    def test_preferred_ai_security_experience(self):
        """Preferred AI/security experience in requirements: excluded."""
        result = classify_job_detailed(
            "销售中心-生态销售",
            (
                "岗位要求: 有数字内容风控、网络、信息安全相关业务经验者优先；"
                "对AI技术及AI商业应用领域发展趋势有持续关注者优先。"
                "岗位描述: 负责开拓客户及合作伙伴。"
            ),
            "销售",
        )
        assert result.tags == ()
        assert result.relevance_status == "excluded"
        assert REASON_SALES in result.reasons


# ===================================================================
# classify_job  backward compatibility
# ===================================================================


class TestClassifyJobBackwardCompatibility:
    """classify_job must return the same results as before for all existing
    callers; review cases return their tags, excluded cases return [].

    These tests mirror the Phase 2 tests in test_phase2.py but also verify
    consistency with classify_job_detailed.
    """

    def test_review_job_returns_tags(self):
        """A review job (backend + AI description) returns tags via the
        legacy API, preserving adapter collection of ambiguous rows."""
        tags = classify_job(
            "后端开发工程师",
            "职责: 负责大模型推理平台开发。",
        )
        assert "AI" in tags

    def test_excluded_returns_empty(self):
        """An excluded role returns [] from the legacy API."""
        tags = classify_job("AI 产品经理", "")
        assert tags == []


# ===================================================================
# collection.py  –  centralised classification
# ===================================================================


@pytest.fixture
def db_session():
    """Provide a fresh SQLite on-disk session for each test."""
    from findjobs.db import init_db

    session = init_db(Path(tempfile.mktemp(suffix=".db")))
    yield session
    session.close()


@pytest.fixture
def demo_company_and_source(db_session):
    """Seed a minimal company + source, returning their ORM instances."""
    from findjobs.repository import sync_company, sync_source
    from findjobs.config import CompanyConfig, SourceConfig

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

    db_session.commit()
    return company, source


class TestCollectJobsCentralClassification:
    """collect_jobs must recompute classification centrally and persist all
    four classification facts."""

    def test_review_job_persisted(self, db_session, demo_company_and_source):
        """A review job (engineering title + AI description) is persisted."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run

        company, source = demo_company_and_source
        run = create_collect_run(db_session, source.id)

        # Job that classify_job_detailed would mark as review.
        jobs = [
            CollectedJob(
                external_id="review-001",
                title="后端开发工程师",
                url="https://example.com/jobs/review",
                description="职责: 负责大模型推理平台开发。",
                location="Beijing",
                job_type="技术",
                matched_tags=[],  # adapter did not set tags — central recompute
            )
        ]

        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, jobs
        )
        db_session.commit()

        assert total == 1
        assert new_count == 1

        from findjobs.models import Job

        persisted = (
            db_session.query(Job)
            .filter(Job.external_id == "review-001")
            .first()
        )
        assert persisted is not None
        assert "AI" in json.loads(persisted.matched_tags)
        assert persisted.relevance_status == "review"
        assert persisted.classification_version == CLASSIFICATION_VERSION
        reasons = json.loads(persisted.classification_reasons)
        assert REASON_REVIEW_AI in reasons

    def test_excluded_job_filtered_out(self, db_session, demo_company_and_source):
        """An excluded job is filtered out before persistence."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run

        company, source = demo_company_and_source
        run = create_collect_run(db_session, source.id)

        jobs = [
            CollectedJob(
                external_id="excluded-001",
                title="AI 产品经理",
                url="https://example.com/jobs/excluded",
                description="负责 AI 产品体验与服务。",
                location="Beijing",
                matched_tags=["AI"],  # adapter incorrectly marked it
            )
        ]

        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, jobs
        )
        db_session.commit()

        assert total == 0
        assert new_count == 0

        from findjobs.models import Job

        persisted = (
            db_session.query(Job)
            .filter(Job.external_id == "excluded-001")
            .first()
        )
        assert persisted is None

    def test_target_job_persisted(self, db_session, demo_company_and_source):
        """A target job is persisted with all classification fields."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run

        company, source = demo_company_and_source
        run = create_collect_run(db_session, source.id)

        jobs = [
            CollectedJob(
                external_id="target-001",
                title="AI Engineer",
                url="https://example.com/jobs/target",
                description="LLM development and agent building.",
                location="Beijing",
                job_type="full-time",
                matched_tags=[],
            )
        ]

        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, jobs
        )
        db_session.commit()

        assert total == 1
        assert new_count == 1

        from findjobs.models import Job

        persisted = (
            db_session.query(Job)
            .filter(Job.external_id == "target-001")
            .first()
        )
        assert persisted is not None
        assert "AI" in json.loads(persisted.matched_tags)
        assert persisted.relevance_status == "target"
        assert persisted.classification_version == CLASSIFICATION_VERSION
        reasons = json.loads(persisted.classification_reasons)
        assert REASON_AI_SURFACE in reasons

    def test_target_and_review_jobs_persisted_together(
        self, db_session, demo_company_and_source
    ):
        """A batch with target, review, and excluded jobs persists only the
        relevant ones."""
        from findjobs.collection import CollectedJob, collect_jobs, create_collect_run

        company, source = demo_company_and_source
        run = create_collect_run(db_session, source.id)

        jobs = [
            CollectedJob(
                external_id="target-002",
                title="AI Engineer",
                url="https://example.com/jobs/target2",
                description="LLM development.",
                location="Beijing",
                matched_tags=[],
            ),
            CollectedJob(
                external_id="review-002",
                title="后端开发工程师",
                url="https://example.com/jobs/review2",
                description="职责: 负责大模型推理平台开发。",
                location="Shanghai",
                matched_tags=[],
            ),
            CollectedJob(
                external_id="excluded-002",
                title="AI 产品经理",
                url="https://example.com/jobs/excluded2",
                description="负责 AI 产品规划。",
                location="Shenzhen",
                matched_tags=[],
            ),
        ]

        total, new_count = collect_jobs(
            db_session, source.id, company.id, run.id, jobs
        )
        db_session.commit()

        assert total == 2  # only target + review
        assert new_count == 2

        from findjobs.models import Job

        persisted = (
            db_session.query(Job)
            .filter(Job.source_id == source.id)
            .all()
        )
        assert len(persisted) == 2
        external_ids = {j.external_id for j in persisted}
        assert "target-002" in external_ids
        assert "review-002" in external_ids
        assert "excluded-002" not in external_ids


# ===================================================================
# maintenance.py  –  reclassify with moved_to_review
# ===================================================================


class TestReclassificationMovedToReview:
    """reclassify_jobs must use the detailed result and count transitions
    to review."""

    def test_moved_to_review_counted_in_preview(self):
        """A job transitioning from target to review increments
        moved_to_review in preview mode."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):
            _seed_company_and_source(session)

            # A job previously classified as target but now only review
            # (e.g. backend engineer with AI description).
            _insert_job(
                session,
                title="后端开发工程师",
                description="职责: 负责大模型推理平台开发。",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            result = reclassify_jobs(session, apply=False)

            assert result.scanned == 1
            assert result.moved_to_review == 1
            assert result.excluded == 0
            assert result.restored == 0
            assert result.deleted == 0

    def test_moved_to_review_applied(self):
        """Apply mode persists the review status and classification fields."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):
            _seed_company_and_source(session)

            jid = _insert_job(
                session,
                title="后端开发工程师",
                description="职责: 负责大模型推理平台开发。",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="target",
            )
            session.commit()

            result = reclassify_jobs(session, apply=True)
            session.commit()

            assert result.moved_to_review == 1

            from findjobs.models import Job

            session.expire_all()
            job = session.get(Job, jid)
            assert job.relevance_status == "review"
            assert "AI" in json.loads(job.matched_tags)
            assert job.classification_version == CLASSIFICATION_VERSION
            reasons = json.loads(job.classification_reasons)
            assert REASON_REVIEW_AI in reasons

    def test_no_change_when_already_review(self):
        """A job already classified as review should show zero transitions."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):
            _seed_company_and_source(session)

            _insert_job(
                session,
                title="后端开发工程师",
                description="职责: 负责大模型推理平台开发。",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
                relevance_status="review",
                classification_version=CLASSIFICATION_VERSION,
                classification_reasons=json.dumps(
                    [REASON_REVIEW_AI], ensure_ascii=False
                ),
            )
            session.commit()

            result = reclassify_jobs(session, apply=False)

            assert result.scanned == 1
            assert result.moved_to_review == 0
            assert result.updated == 0  # classification fields already correct

    def test_deleted_always_zero(self):
        """reclassify_jobs must always report deleted=0."""
        from findjobs.maintenance import reclassify_jobs

        with _managed_db() as (session, _):
            _seed_company_and_source(session)

            _insert_job(
                session,
                title="后端开发工程师",
                description="职责: 负责大模型推理平台开发。",
                matched_tags=json.dumps(["AI"], ensure_ascii=False),
            )
            session.commit()

            result = reclassify_jobs(session, apply=True)
            assert result.deleted == 0


# ===================================================================
# Helpers  (copied locally to avoid cross-test coupling)
# ===================================================================


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _close_session(session):
    session.close()
    if session.bind:
        session.bind.dispose()


from contextlib import contextmanager


@contextmanager
def _managed_db():
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
    title="AI Engineer",
    description="",
    job_type="",
    location="",
    relevance_status="target",
    matched_tags=None,
    external_id="ext-1",
    url="https://example.com/job/1",
    status="active",
    source_id=1,
    company_id=1,
    classification_version="",
    classification_reasons="[]",
):
    from findjobs.models import Job

    now = _utcnow()
    tags = matched_tags if matched_tags is not None else json.dumps(
        ["AI"], ensure_ascii=False
    )
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
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(job)
    session.flush()
    return job.id


def _seed_company_and_source(session):
    from findjobs.models import Company, Source

    c = Company(slug="acme", name="Acme Inc.")
    session.add(c)
    session.flush()
    s = Source(company_id=c.id, slug="acme-careers", name="Acme Careers")
    session.add(s)
    session.flush()
    return c.id, s.id
