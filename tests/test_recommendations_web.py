"""Tests for the local recommendations web view."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(path: Path, **overrides: str) -> None:
    """Write a minimal test profile that produces recommendations."""
    lines = [
        "## Background",
        "",
        "- **Role**: AI Engineer / Security Engineer",
        "- **Experience**: 5+ years",
        "- **Skills**: Python, AI security, LLM security, Penetration testing",
        "",
        "## Target Cities",
        "",
        "- Beijing",
        "- Shanghai",
        "- Hangzhou",
        "",
        "## Preferences",
        "",
        "- **Job Type**: Full-time",
        "",
        "## Excluded Companies",
        "",
        "- Huawei",
    ]
    content = overrides.get("content")
    path.write_text(content if content is not None else "\n".join(lines))


def _seed_db(db_path: Path) -> None:
    """Seed a temporary database with mixed jobs for recommendation tests.

    Inserts jobs directly via ORM to bypass collection-pipeline classification
    and domain filtering that would interfere with test data.
    """
    from findjobs.db import init_db
    from findjobs.models import Company, Job, Source, UserMark, CollectRun, JobObservation
    from datetime import datetime, timezone

    def _utcnow():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    session = init_db(db_path)

    # ---- Companies ----
    testcorp = Company(id=1, slug="testcorp", name="Test Corp")
    othercorp = Company(id=2, slug="othercorp", name="Other Corp")
    session.add_all([testcorp, othercorp])
    session.flush()

    # ---- Sources ----
    src1 = Source(
        id=1, slug="testcorp-careers", name="Test Corp Careers",
        company_id=testcorp.id, source_type="official_careers",
        base_url="https://example.com", is_active=True,
    )
    src2 = Source(
        id=2, slug="othercorp-careers", name="Other Corp Careers",
        company_id=othercorp.id, source_type="official_careers",
        base_url="https://other.com", is_active=True,
    )
    session.add_all([src1, src2])
    session.flush()

    # ---- Collect runs ----
    now = _utcnow()
    run1 = CollectRun(id=1, source_id=src1.id, status="completed",
                      started_at=now, finished_at=now, jobs_found=4, jobs_new=4)
    run2 = CollectRun(id=2, source_id=src2.id, status="completed",
                      started_at=now, finished_at=now, jobs_found=1, jobs_new=1)
    session.add_all([run1, run2])
    session.flush()

    # ---- Jobs ----
    jobs_data = [
        # id=1: eligible
        Job(id=1, source_id=src1.id, company_id=testcorp.id,
            external_id="job-001", title="AI Security Engineer",
            url="https://example.com/jobs/001",
            description="AI security testing and LLM security research",
            salary_text="30k-50k", salary_min=30000.0, salary_max=50000.0,
            salary_currency="CNY", salary_period="monthly",
            salary_disclosed=True, location="北京市、杭州市",
            job_type="full-time",
            matched_tags='["AI","Security"]',
            status="active", relevance_status="target",
            responsibilities="", requirements="",
            detail_completeness="missing",
            created_at=now, updated_at=now, first_seen_at=now, last_seen_at=now),
        # id=2: eligible
        Job(id=2, source_id=src1.id, company_id=testcorp.id,
            external_id="job-002", title="Security Intern",
            url="https://example.com/jobs/002",
            description="Security internship",
            salary_text="", salary_min=None, salary_max=None,
            salary_currency="CNY", salary_period="monthly",
            salary_disclosed=False, location="北京",
            job_type="intern",
            matched_tags='["Security"]',
            status="active", relevance_status="target",
            responsibilities="", requirements="",
            detail_completeness="missing",
            created_at=now, updated_at=now, first_seen_at=now, last_seen_at=now),
        # id=3: excluded by algorithm rejection
        Job(id=3, source_id=src1.id, company_id=testcorp.id,
            external_id="job-003", title="Algorithm Engineer",
            url="https://example.com/jobs/003",
            description="ML algorithm development",
            salary_text="40k-60k", salary_min=40000.0, salary_max=60000.0,
            salary_currency="CNY", salary_period="monthly",
            salary_disclosed=True, location="上海市",
            job_type="full-time",
            matched_tags='["AI"]',
            status="active", relevance_status="target",
            responsibilities="", requirements="",
            detail_completeness="missing",
            created_at=now, updated_at=now, first_seen_at=now, last_seen_at=now),
        # id=4: excluded by unsupported tags
        Job(id=4, source_id=src1.id, company_id=testcorp.id,
            external_id="job-004", title="Data Analyst",
            url="https://example.com/jobs/004",
            description="General data analysis",
            salary_text="", salary_min=None, salary_max=None,
            salary_currency="CNY", salary_period="monthly",
            salary_disclosed=False, location="广州",
            job_type="full-time",
            matched_tags='["general"]',
            status="active", relevance_status="target",
            responsibilities="", requirements="",
            detail_completeness="missing",
            created_at=now, updated_at=now, first_seen_at=now, last_seen_at=now),
        # id=5: excluded by non-active + non-target
        Job(id=5, source_id=src2.id, company_id=othercorp.id,
            external_id="job-101", title="Security Engineer (Closed)",
            url="https://other.com/jobs/101",
            description="Closed position",
            salary_text="", salary_min=None, salary_max=None,
            salary_currency="CNY", salary_period="monthly",
            salary_disclosed=False, location="深圳",
            job_type="full-time",
            matched_tags='["Security"]',
            status="closed", relevance_status="monitor",
            responsibilities="", requirements="",
            detail_completeness="missing",
            created_at=now, updated_at=now, first_seen_at=now, last_seen_at=now),
    ]
    session.add_all(jobs_data)
    session.flush()

    for job in jobs_data:
        session.add(JobObservation(
            job_id=job.id,
            collect_run_id=run1.id if job.company_id == testcorp.id else run2.id,
            seen_at=now,
        ))

    # ---- User marks ----
    session.add(UserMark(job_id=1, mark_type="bookmark", note="Watching this role"))
    session.add(UserMark(job_id=2, mark_type="applied", note="Applied on portal"))

    session.commit()
    session.close()


@pytest.fixture
def tmp_rec():
    """Yield ``(db_path, client, profile_path)`` with seeded data."""
    from fastapi.testclient import TestClient
    from findjobs.web import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        profile_path = Path(tmpdir) / "profile.md"
        _seed_db(db_path)
        _make_profile(profile_path)
        app = create_app(db_path=db_path, profile_path=profile_path)
        client = TestClient(app)
        yield db_path, client, profile_path


@pytest.fixture
def client(tmp_rec):
    """Shorthand — return the TestClient alone."""
    _, c, _ = tmp_rec
    return c


# ---------------------------------------------------------------------------
# GET /recommendations — success cases
# ---------------------------------------------------------------------------


class TestRecommendationsList:

    def test_recommendations_renders(self, client):
        """Active target recommendations appear; excluded rows do not."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text

        assert "扫描" in html
        assert "合格" in html
        assert "推荐" in html

        assert "AI Security Engineer" in html
        assert "Security Intern" in html

        assert "Algorithm Engineer" not in html
        assert "Data Analyst" not in html
        assert "Security Engineer (Closed)" not in html

        # Chinese exclusion labels
        assert "算法类职位" in html
        assert "不支持的标签" in html
        assert "非活跃状态" in html

    def test_undisclosed_salary(self, client):
        """Jobs without salary disclosure show `未披露`."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "未披露" in resp.text

    def test_disclosed_salary_present(self, client):
        """Jobs with salary disclosure show the salary text."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "30k-50k" in resp.text

    def test_official_url_as_link(self, client):
        """Valid http/https URLs render as clickable links."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="https://example.com/jobs/001"' in html
        assert '<a href="https://example.com/jobs/001"' in html

    def test_detail_link_present(self, client):
        """Each recommendation links to its job detail page."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert '/jobs/1' in resp.text

    def test_detail_completeness(self, client):
        """Each recommendation shows detail completeness label."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "完整度:" in resp.text

    def test_five_component_table_with_evidence(self, client):
        """All 5 component rows appear with scores, message, source fields, and evidence."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text

        # Exact Chinese dimension labels
        assert "领域" in html
        assert "技能" in html
        assert "需求" in html
        assert "经验" in html
        assert "位置" in html

        # All 7 header labels
        assert "得分" in html
        assert "说明" in html
        assert "匹配来源" in html
        assert "个人资料来源" in html
        assert "匹配项" in html
        assert "差距项" in html

        # Exact seeded score-component messages for job-001 (AI Security Engineer)
        assert "Profile domain AI Security fully matches job tags." in html
        assert "Matched 1/1 recognized skills." in html
        assert "Requirements not available; needs verification. Neutral score applied." in html
        assert "No explicit experience requirement in job; neutral score." in html
        assert "Location matches: 北京, 杭州." in html

        # Source field labels from the engine
        assert "matched_tags" in html
        assert "requirements" in html
        assert "experience_years" in html
        assert "target_cities" in html

        # Score format X/Y
        assert "25.0/25.0" in html
        assert "30.0/30.0" in html

        # Dash for empty values
        assert "-" in html

        # Second recommendation message
        assert "Profile domain AI Security partially matches job tags." in html

    def test_tier_styled_and_labeled(self, client):
        """Tier values render with CSS class and Chinese label."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        # Job-001 scores 83.0 -> tier="high" -> CSS class "rec-tier-high"
        assert 'rec-tier-high' in html
        # Chinese label for high tier
        assert '高匹配' in html
        # Job-002 (Security Intern) is tier="medium"
        assert '中匹配' in html

    def test_rank_present(self, client):
        """Each recommendation shows its rank number."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "#1" in resp.text

    def test_company_name_present(self, client):
        """Company name shows in recommendations."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "Test Corp" in resp.text

    def test_job_type_intern(self, client):
        """Job type 'intern' appears for Security Intern."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "intern" in resp.text.lower()

    def test_tags_present(self, client):
        """Tags appear."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "Security" in resp.text
        assert "AI" in resp.text

    def test_location_present(self, client):
        """Location appears."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "北京" in resp.text

    def test_responsibility_details_always_shown(self, client):
        """Responsibilities <details> always rendered even when blank."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert "<summary>职责</summary>" in html
        assert "<summary>要求</summary>" in html
        # Both detail sections exist
        assert html.count("<summary>职责</summary>") >= 1
        assert html.count("<summary>要求</summary>") >= 1

    def test_missing_requirements_message(self, client):
        """Blank requirements show Chinese placeholder."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "未采集到岗位职责" in resp.text
        assert "未采集到岗位要求" in resp.text

    def test_gaps_present(self, client):
        """Gap terms appear."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "差距" in resp.text

    def test_application_advice_present(self, client):
        """Application advice text is rendered."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        # Advice is generated in English by the engine
        assert "rec-advice" in resp.text
        assert "Salary" in resp.text

    def test_matched_skills_present(self, client):
        """Matched skills appear."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "匹配技能" in resp.text

    def test_salary_range_format(self, client):
        """Disclosed salary shows range with currency and period."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "CNY/" in resp.text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestRecommendationsEdgeCases:

    def test_missing_profile(self, tmp_rec):
        """When profile file is missing, show Chinese error state (HTTP 200)."""
        db_path, _, profile_path = tmp_rec
        profile_path.unlink()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        assert "未找到" in resp.text
        assert "个人资料" in resp.text

    def test_invalid_profile(self, tmp_rec):
        """When profile path is a directory, show exact Chinese error state."""
        db_path, _, profile_path = tmp_rec
        profile_path.unlink()
        profile_path.mkdir()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        assert "个人资料格式无效" in resp.text

    def test_no_pii_leakage(self, tmp_rec):
        """Email, phone, and contact strings in profile do NOT appear in HTML."""
        db_path, _, profile_path = tmp_rec
        # Rewrite profile with explicit PII content
        _make_profile(profile_path, content=(
            "## Background\n\n"
            "- **Role**: AI Engineer\n"
            "- **Skills**: AI security\n"
            "- **Email**: test@example.com\n"
            "- **Phone**: +86-138-0000-0000\n"
            "## Target Cities\n\n- Beijing\n"
        ))
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert "test@example.com" not in html
        assert "+86-138-0000-0000" not in html
        assert "zhd" not in html.lower()  # no local name fragments

    def test_unsafe_url_javascript(self, tmp_rec):
        """javascript: URL renders as plain text, not an <a> href."""
        db_path, _, profile_path = tmp_rec
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
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="javascript:alert(1)"' not in html
        assert "javascript:alert(1)" in html  # shown as text

    def test_unsafe_url_hostless(self, tmp_rec):
        """Hostless https: URL renders as plain text, not an <a> href."""
        db_path, _, profile_path = tmp_rec
        from findjobs.db import init_db
        from findjobs.models import Job
        session = init_db(db_path)
        try:
            session.query(Job).filter(Job.id == 1).update({"url": "https://"})
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert 'href="https://"' not in html

    def test_unsafe_url_crlf(self, tmp_rec):
        """CRLF-containing URL renders as plain text, not linked."""
        db_path, _, profile_path = tmp_rec
        from findjobs.db import init_db
        from findjobs.models import Job
        session = init_db(db_path)
        try:
            session.query(Job).filter(Job.id == 1).update(
                {"url": "https://example.com\r\nX-Injected: true"}
            )
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        import re
        assert resp.status_code == 200
        html = resp.text
        # Unsafe URL class is used for CRLF-containing URLs
        assert "rec-url-unsafe" in html
        # Verify NO href attribute contains CR/LF (response splitting)
        for href in re.findall(r'href="([^"]*)"', html):
            assert "\n" not in href, f"CRLF in href: {href!r}"
            assert "\r" not in href, f"CRLF in href: {href!r}"

    def test_diagnostic_filter_preserves_limit_no_js(self, client):
        """show_ignored filter preserves limit and can submit without JavaScript."""
        resp = client.get("/recommendations", params={"limit": 10})
        assert resp.status_code == 200
        html = resp.text
        # No onchange JavaScript dependency
        assert 'onchange="this.form.submit()"' not in html
        # Has an explicit submit button
        assert '<button type="submit"' in html
        # The limit is preserved as a hidden input
        assert 'name="limit"' in html
        assert 'value="10"' in html

    def test_valid_url_still_linked(self, tmp_rec):
        """Valid job URL still renders as a clickable link."""
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        assert 'href="https://example.com/jobs/001"' in resp.text

    def test_unsafe_url_whitespace(self, tmp_rec):
        """URL with leading/trailing whitespace renders as plain text."""
        db_path, _, profile_path = tmp_rec
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
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        # Whitespace-surrounded URL must NOT appear inside an href
        assert 'href=" https://example.com/jobs/001 "' not in html
        # Unsafe URL class is used
        assert "rec-url-unsafe" in html

    def test_limit_one(self, client):
        """limit=1 returns at most one recommendation."""
        resp = client.get("/recommendations", params={"limit": 1})
        assert resp.status_code == 200
        assert "AI Security Engineer" in resp.text
        assert "Security Intern" not in resp.text

    def test_limit_minimum(self, client):
        """limit=1 (minimum) is accepted."""
        resp = client.get("/recommendations", params={"limit": 1})
        assert resp.status_code == 200

    def test_limit_maximum(self, client):
        """limit=100 (maximum) is accepted."""
        resp = client.get("/recommendations", params={"limit": 100})
        assert resp.status_code == 200

    def test_limit_below_min_rejected(self, client):
        """limit=0 returns validation error."""
        resp = client.get("/recommendations", params={"limit": 0})
        assert resp.status_code == 422

    def test_limit_above_max_rejected(self, client):
        """limit=101 returns validation error."""
        resp = client.get("/recommendations", params={"limit": 101})
        assert resp.status_code == 422

    def test_limit_negative_rejected(self, client):
        """limit=-1 returns validation error."""
        resp = client.get("/recommendations", params={"limit": -1})
        assert resp.status_code == 422

    def test_zero_recommendations(self, tmp_rec):
        """When all jobs excluded, renders exact empty state with no cards."""
        from findjobs.db import init_db
        from findjobs.models import Job
        db_path, _, profile_path = tmp_rec
        session = init_db(db_path)
        try:
            session.query(Job).update({"status": "closed"})
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert "暂无推荐结果" in html
        assert "rec-card" not in html  # no recommendation card rendered
        # Still has the summary band
        assert "扫描" in html

    def test_jinja_escaping(self, tmp_rec):
        """HTML in job fields is escaped; next_url stays as a single attribute."""
        import html as html_mod
        import re
        db_path, _, profile_path = tmp_rec
        from findjobs.db import init_db
        from findjobs.models import Job
        session = init_db(db_path)
        try:
            session.query(Job).filter(Job.id == 1).update(
                {"title": '"><script>alert("xss")</script>'}
            )
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        raw = resp.text

        # 1. Raw attack literal (markup + attribute-breaking sequence) must
        #    NOT appear in the HTML output.
        assert '"><script>alert("xss")</script>' not in raw
        assert '<script>alert' not in raw

        # 2. After HTML unescaping the original content IS recoverable.
        decoded = html_mod.unescape(raw)
        assert '"><script>alert("xss")</script>' in decoded

        # 3. Each hidden next_url input has a single well-formed value
        #    attribute whose content is a safe local redirect path.
        for m in re.finditer(r'<input[^>]*name="next_url"[^>]*>', raw):
            tag = m.group(0)
            if 'value="' not in tag:
                continue
            vix = tag.index('value="')
            vstart = vix + 7
            vend = tag.index('"', vstart)
            val = tag[vstart:vend]
            assert val.startswith("/recommendations") or val.startswith("/jobs/"), \
                f"Unexpected next_url value: {val!r}"


class TestRecommendationsReadOnly:
    """GET /recommendations does not mutate data."""

    def test_read_only_preserves_counts(self, tmp_rec):
        """Job, observation, and user_mark counts unchanged after GET."""
        from findjobs.db import init_db
        from findjobs.models import Job, JobObservation, UserMark
        db_path, c, _ = tmp_rec
        session = init_db(db_path)
        try:
            jobs_before = session.query(Job).count()
            marks_before = session.query(UserMark).count()
            obs_before = session.query(JobObservation).count()
        finally:
            session.close()
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        session = init_db(db_path)
        try:
            assert session.query(Job).count() == jobs_before
            assert session.query(UserMark).count() == marks_before
            assert session.query(JobObservation).count() == obs_before
        finally:
            session.close()

    def test_persisted_fields_unchanged(self, tmp_rec):
        """Persisted fields not modified by GET."""
        from findjobs.db import init_db
        from findjobs.models import Job
        db_path, c, _ = tmp_rec
        session = init_db(db_path)
        try:
            original_titles = {j.id: j.title for j in session.query(Job).all()}
        finally:
            session.close()
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        session = init_db(db_path)
        try:
            for j in session.query(Job).all():
                assert j.title == original_titles[j.id]
        finally:
            session.close()


# ---------------------------------------------------------------------------
# UserMark rendering and shared POST /jobs/{job_id}/marks endpoint
# ---------------------------------------------------------------------------


class TestRecommendationsMarks:

    def test_existing_marks_shown(self, client):
        """Existing marks appear on recommendation cards."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert "bookmark" in html
        assert "applied" in html
        assert "Watching this role" in html
        assert "Applied on portal" in html

    def test_mark_form_present(self, client):
        """Each recommendation has a mark form."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert '/jobs/' in resp.text
        assert '/marks' in resp.text

    def test_mark_deterministic_order(self, tmp_rec):
        """Marks appear in (mark_type, note) order sorted by mark_type."""
        db_path, _, profile_path = tmp_rec
        from findjobs.db import init_db
        from findjobs.models import UserMark
        session = init_db(db_path)
        try:
            session.add(UserMark(job_id=1, mark_type="applied", note="Applied"))
            session.add(UserMark(job_id=1, mark_type="ignored", note=""))
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        # Use show_ignored so marks on an ignored job are still rendered
        resp = c.get("/recommendations", params={"show_ignored": "true"})
        assert resp.status_code == 200
        html = resp.text
        # Three marks on job 1: bookmark (existing), applied, ignored
        # Should be ordered: bookmark, applied, ignored
        idx_bookmark = html.find("rec-mark-bookmark")
        idx_applied = html.find("rec-mark-applied")
        idx_ignored = html.find("rec-mark-ignored")
        assert idx_bookmark >= 0
        assert idx_applied >= 0
        assert idx_ignored >= 0
        assert idx_bookmark < idx_applied < idx_ignored

    # --- Strict POST tests: create/update each mark type ---

    def test_post_create_bookmark(self, tmp_rec):
        """POST bookmark on job 2 creates a bookmark, redirects to /recommendations."""
        from findjobs.db import init_db
        from findjobs.models import UserMark
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/2/marks",
            data={"mark_type": "bookmark", "note": "Second look",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"
        session = init_db(db_path)
        try:
            mark = session.query(UserMark).filter(
                UserMark.job_id == 2, UserMark.mark_type == "bookmark"
            ).first()
            assert mark is not None
            assert mark.note == "Second look"
        finally:
            session.close()

    def test_post_create_applied(self, tmp_rec):
        """POST applied on job 1 creates an applied mark, redirects to /recommendations."""
        from findjobs.db import init_db
        from findjobs.models import UserMark
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "applied", "note": "Submitted via portal",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"
        session = init_db(db_path)
        try:
            mark = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "applied"
            ).first()
            assert mark is not None
            assert mark.note == "Submitted via portal"
        finally:
            session.close()

    def test_post_create_ignored(self, tmp_rec):
        """POST ignored on job 1 creates an ignored mark, redirects to /recommendations."""
        from findjobs.db import init_db
        from findjobs.models import UserMark
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "ignored", "note": "Not interested",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"
        session = init_db(db_path)
        try:
            mark = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "ignored"
            ).first()
            assert mark is not None
            assert mark.note == "Not interested"
        finally:
            session.close()

    def test_post_update_bookmark_note(self, tmp_rec):
        """POST bookmark with existing mark updates the note."""
        from findjobs.db import init_db
        from findjobs.models import UserMark
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "Updated note",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"
        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "bookmark"
            ).all()
            assert len(marks) == 1  # still one, not duplicated
            assert marks[0].note == "Updated note"
        finally:
            session.close()

    def test_post_update_applied_note(self, tmp_rec):
        """POST applied updates existing applied note."""
        db_path, _, profile_path = tmp_rec
        from findjobs.db import init_db
        from findjobs.models import UserMark
        session = init_db(db_path)
        try:
            session.add(UserMark(job_id=1, mark_type="applied", note="Original"))
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "applied", "note": "Updated applied",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"
        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "applied"
            ).all()
            assert len(marks) == 1
            assert marks[0].note == "Updated applied"
        finally:
            session.close()

    def test_post_update_ignored_note(self, tmp_rec):
        """POST ignored updates existing ignored note."""
        db_path, _, profile_path = tmp_rec
        from findjobs.db import init_db
        from findjobs.models import UserMark
        session = init_db(db_path)
        try:
            session.add(UserMark(job_id=1, mark_type="ignored", note="Original ignore"))
            session.commit()
        finally:
            session.close()
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "ignored", "note": "Still not interested",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"
        session = init_db(db_path)
        try:
            marks = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "ignored"
            ).all()
            assert len(marks) == 1
            assert marks[0].note == "Still not interested"
        finally:
            session.close()

    # --- Redirect tests ---

    def test_mark_without_next_url_redirects_detail(self, client):
        """Without next_url, redirect defaults to /jobs/{job_id}."""
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_mark_with_next_url_redirects_recommendations(self, tmp_rec):
        """POST with next_url redirects to /recommendations."""
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        db_path, _, profile_path = tmp_rec
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations"

    def test_mark_with_next_url_with_query_redirects(self, tmp_rec):
        """next_url with a safe query string redirects."""
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        db_path, _, profile_path = tmp_rec
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "",
                  "next_url": "/recommendations?limit=10"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/recommendations?limit=10"

    def test_mark_external_next_url_rejected(self, client):
        """External URL redirects to job detail."""
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "https://evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_mark_scheme_relative_rejected(self, client):
        """Scheme-relative URL redirects to job detail."""
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "", "next_url": "//evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"

    def test_mark_crlf_rejected(self, client):
        """CRLF injection in next_url redirects to job detail."""
        resp = client.post(
            "/jobs/1/marks",
            data={"mark_type": "bookmark", "note": "",
                  "next_url": "/recommendations\r\nLocation: https://evil.com"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location", "") == "/jobs/1"


# ---------------------------------------------------------------------------
# Template / CSS regression
# ---------------------------------------------------------------------------


class TestTemplateRegression:

    def test_chinese_lang_attribute(self, client):
        """HTML has lang=zh-CN."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert 'lang="zh-CN"' in resp.text

    def test_recommendations_nav_link(self, client):
        """Navigation includes Recommendations link."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "推荐" in resp.text

    def test_existing_jobs_link_works(self, client):
        """Jobs route still works."""
        resp = client.get("/jobs")
        assert resp.status_code == 200

    def test_existing_runs_link_works(self, client):
        """Runs route still works."""
        resp = client.get("/runs")
        assert resp.status_code == 200

    def test_existing_detail_works(self, client):
        """Job detail route still works."""
        resp = client.get("/jobs/1")
        assert resp.status_code == 200

    def test_no_card_inside_card_selector(self):
        """CSS has no nested recommendation-card selectors."""
        css = _read_css()
        assert "rec-card .rec-card" not in css

    def test_overflow_anywhere_on_location(self):
        """CSS has overflow-wrap: anywhere on .rec-location."""
        css = _read_css()
        assert "overflow-wrap: anywhere" in css

    def test_white_space_normal_on_location(self):
        """CSS has 'white-space: normal' on .rec-location."""
        css = _read_css()
        assert "white-space: normal" in css
        # Verify it's the rec-location rule, not some other
        assert "rec-location { white-space: normal;" in css

    def test_exploratory_tier_style_exists(self):
        """CSS has a .rec-tier-exploratory rule."""
        css = _read_css()
        assert "rec-tier-exploratory" in css

    def test_responsive_breakpoint_present(self):
        """CSS has a max-width media query."""
        css = _read_css()
        assert "@media" in css and "max-width" in css

    def test_no_nowrap_on_location(self):
        """CSS does NOT have 'white-space: nowrap' inside the rec-location rule."""
        css = _read_css()
        import re
        match = re.search(r'\.rec-location\s*\{([^}]+)\}', css)
        assert match is not None, "Could not find .rec-location rule"
        block = match.group(1)
        assert "white-space: nowrap" not in block
        assert "white-space: normal" in block

    def test_stable_min_width_on_controls(self):
        """CSS has min-width on action controls."""
        css = _read_css()
        assert "min-width: 48px" in css
        assert "min-width: 72px" in css

    def test_score_table_overflow_x_auto(self):
        """Score table scroll container has overflow-x: auto."""
        css = _read_css()
        assert "overflow-x: auto" in css

    def test_score_table_stable_min_width(self):
        """Score table has stable min-width that is not removed at mobile."""
        css = _read_css()
        assert "min-width: 820px" in css
        # The mobile breakpoint must NOT override it to min-width: auto
        assert "min-width: auto; width: 100%" not in css


class TestRecommendationsMarksExtended:
    """Phase 4B: Extended mark features — delete controls, next_url preservation."""

    def test_next_url_safe_escaped(self, tmp_rec):
        """recommendations_next is NOT marked |safe; & is HTML-escaped."""
        import html
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app

        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        # Pass an unknown query value containing characters that would be
        # dangerous if |safe were used
        resp = c.get(
            "/recommendations",
            params={"limit": 25, "show_ignored": "true", "x": '"><script>'},
        )
        assert resp.status_code == 200
        raw_html = resp.text
        # The & in the query should be HTML-escaped (not raw)
        assert "&amp;" in raw_html, "& not escaped in hidden input value"
        # Verify the rendered HTML doesn't contain unescaped attribute-breaking quotes
        for match in __import__('re').finditer(r'<input[^>]*value="([^"]*)"', raw_html):
            val = match.group(1)
            assert '"' not in val, f"Unescaped quote in value attribute: {val!r}"
        # Double-check the decoded value is correct
        decoded = html.unescape(raw_html)
        assert '/recommendations?limit=25&show_ignored=true' in decoded

    def test_mark_delete_forms_on_cards(self, client):
        """Recommendation cards have × delete buttons for existing marks."""
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        assert "/marks/delete" in resp.text

    def test_mark_form_preserves_query(self, tmp_rec):
        """Recommendation mark forms include current query in next_url."""
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app

        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations", params={"limit": 10})
        assert resp.status_code == 200
        assert '/recommendations?limit=10' in resp.text

    def test_mark_form_preserves_full_query(self, tmp_rec):
        """next_url preserves multiple query params (HTML-decoded)."""
        import html
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app

        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get(
            "/recommendations",
            params={"limit": 25, "show_ignored": "true"},
        )
        assert resp.status_code == 200
        # Without |safe, & is HTML-escaped to &amp; so decode before checking
        decoded = html.unescape(resp.text)
        assert '/recommendations?limit=25&show_ignored=true' in decoded

    def test_post_ignored_from_card_hides_job(self, tmp_rec):
        """POST ignored with next_url hides the job; bookmark persists."""
        from findjobs.db import init_db
        from findjobs.models import UserMark
        db_path, _, profile_path = tmp_rec
        from fastapi.testclient import TestClient
        from findjobs.web import create_app

        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)

        # 1. Initially both jobs are visible
        resp1 = c.get("/recommendations")
        assert resp1.status_code == 200
        assert "AI Security Engineer" in resp1.text
        assert "Security Intern" in resp1.text

        # 2. POST ignored on job 1 with next_url=/recommendations
        resp2 = c.post(
            "/jobs/1/marks",
            data={"mark_type": "ignored", "note": "Not interested",
                  "next_url": "/recommendations"},
            follow_redirects=False,
        )
        assert resp2.status_code == 303
        assert resp2.headers.get("location", "") == "/recommendations"

        # 3. Follow the redirect — job 1 should disappear
        resp3 = c.get("/recommendations")
        assert resp3.status_code == 200
        assert "AI Security Engineer" not in resp3.text
        assert "Security Intern" in resp3.text

        # 4. The original bookmark on job 1 is still stored
        session = init_db(db_path)
        try:
            bookmark = session.query(UserMark).filter(
                UserMark.job_id == 1, UserMark.mark_type == "bookmark"
            ).first()
            assert bookmark is not None
            assert bookmark.note == "Watching this role"
        finally:
            session.close()


class TestRecommendationsIgnored:
    """Phase 4B: Recommendations hide ignored jobs by default."""

    def _make_ignored(self, db_path, job_id):
        from findjobs.db import init_db
        from findjobs.models import UserMark
        session = init_db(db_path)
        try:
            session.add(UserMark(job_id=job_id, mark_type="ignored", note=""))
            session.commit()
        finally:
            session.close()

    def test_hides_ignored_by_default(self, tmp_rec):
        """Ignored-marked jobs are excluded from recommendations."""
        db_path, _, profile_path = tmp_rec
        self._make_ignored(db_path, 1)
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        html = resp.text
        assert "AI Security Engineer" not in html
        assert "Security Intern" in html

    def test_show_ignored_includes_all(self, tmp_rec):
        """show_ignored=true includes ignored jobs."""
        db_path, _, profile_path = tmp_rec
        self._make_ignored(db_path, 1)
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations", params={"show_ignored": "true"})
        assert resp.status_code == 200
        assert "AI Security Engineer" in resp.text
        assert "Security Intern" in resp.text

    def test_ignored_filtered_before_limit(self, tmp_rec):
        """Ignored exclusion happens before the recommendation limit truncation."""
        db_path, _, profile_path = tmp_rec
        self._make_ignored(db_path, 1)
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        # With limit=1 and job 1 (score 83.0) ignored, job 2 (score ~55) fills in
        resp = c.get("/recommendations", params={"limit": 1})
        assert resp.status_code == 200
        assert "AI Security Engineer" not in resp.text
        assert "Security Intern" in resp.text

    def test_ignored_not_excluded_when_no_ignored_marks(self, tmp_rec):
        """No ignored marks → all eligible jobs shown."""
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        db_path, _, profile_path = tmp_rec
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        assert "AI Security Engineer" in resp.text
        assert "Security Intern" in resp.text

    def test_delete_button_ignored_on_mark_removes_it(self, tmp_rec):
        """Posting mark delete for an ignored job restores it in recommendations."""
        db_path, _, profile_path = tmp_rec
        self._make_ignored(db_path, 1)
        from fastapi.testclient import TestClient
        from findjobs.web import create_app
        app = create_app(db_path=db_path, profile_path=profile_path)
        c = TestClient(app)
        # Delete the ignored mark
        c.post(
            "/jobs/1/marks/delete",
            data={"mark_type": "ignored"},
        )
        # Now job 1 should reappear
        resp = c.get("/recommendations")
        assert resp.status_code == 200
        assert "AI Security Engineer" in resp.text


def _read_css() -> str:
    """Read the project CSS file."""
    return (Path(__file__).resolve().parent.parent /
            "src" / "findjobs" / "static" / "style.css").read_text(encoding="utf-8")
