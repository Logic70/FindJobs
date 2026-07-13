"""Tests for the Chaitin (长亭科技) official adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "adapters"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _context():
    from findjobs.adapters import AdapterContext

    return AdapterContext(
        company_slug="chaitin",
        source_slug="chaitin-careers",
        base_url="https://join.chaitin.cn/plugins/career_site/sites/default",
        fetch_url="https://join.chaitin.cn/plugins/career_site/api/default/jobs",
    )


class TestChaitinOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        import findjobs.adapters.chaitin  # noqa: F401
        from findjobs.adapters import get_adapter

        return get_adapter("chaitin_official")

    @pytest.fixture
    def context(self):
        return _context()

    # ------------------------------------------------------------------ #
    # Parse tests
    # ------------------------------------------------------------------ #

    def test_parses_all_jobs(self, adapter, context):
        """Verify every fixture item becomes a CollectedJob."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        assert len(jobs) == 5

    def test_ai_security_role_has_compound_tags(self, adapter, context):
        """AI安全研究员 → AI, Security, AI Security (target)."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-ai-security-001")
        assert "AI" in job.matched_tags
        assert "Security" in job.matched_tags
        assert "AI Security" in job.matched_tags

    def test_security_technical_role_has_security_tag(self, adapter, context):
        """安全渗透测试工程师 → Security (target)."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-pentest-002")
        assert "Security" in job.matched_tags
        assert "AI" not in job.matched_tags

    def test_algorithm_title_is_excluded(self, adapter, context):
        """算法工程师 → excluded (empty tags)."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-algo-003")
        assert job.matched_tags == []

    def test_non_target_functional_role_excluded(self, adapter, context):
        """产品经理 → excluded (empty tags, non-target surface)."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-pm-004")
        assert job.matched_tags == []

    def test_external_id_stability(self, adapter, context):
        """external_id must be stable and non-empty."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        for job in jobs:
            assert job.external_id, f"Missing external_id for {job.title}"

    def test_official_url_constructed(self, adapter, context):
        """URL must point to the Chaitin careers domain with /jobs/ path."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        for job in jobs:
            assert job.url.startswith(
                "https://join.chaitin.cn/plugins/career_site/sites/default/jobs/"
            ), f"Unexpected URL for {job.title}: {job.url}"

    def test_url_contains_external_id(self, adapter, context):
        """URL should incorporate the job's external_id."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = jobs[0]
        assert job.external_id in job.url

    def test_url_has_job_id_query_param(self, adapter, context):
        """URL should contain ?job_id=<external_id>."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        for job in jobs:
            assert f"?job_id={job.external_id}" in job.url

    def test_description_preserved(self, adapter, context):
        """Description must contain the raw original text."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        ai_job = next(j for j in jobs if j.external_id == "chaitin-ai-security-001")
        assert "AI安全" in ai_job.description
        assert "【岗位职责】" in ai_job.description

    # ------------------------------------------------------------------ #
    # Salary fact behaviour
    # ------------------------------------------------------------------ #

    def test_salary_disclosed_when_both_values_present(self, adapter, context):
        """salary_disclosed is True when both min and max are valid positives."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-ai-security-001")
        assert job.salary_disclosed is True
        assert job.salary_min == 30000
        assert job.salary_max == 50000
        assert job.salary_currency == "CNY"
        assert job.salary_period == "monthly"

    def test_salary_mentions_months_in_text(self, adapter, context):
        """salary_months is represented in salary_text but not multiplied."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-ai-security-001")
        assert "15 payments/year" in job.salary_text

    def test_salary_not_disclosed_when_missing(self, adapter, context):
        """salary_disclosed is False when no salary fields are present."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-algo-003")
        assert job.salary_disclosed is False
        assert job.salary_min is None
        assert job.salary_max is None

    def test_salary_not_disclosed_with_zero_values(self, adapter, context):
        """Zero/invalid salary values are treated as undisclosed."""
        raw = {
            "code": 0,
            "data": {
                "items": [
                    {
                        "job_id": "zero-salary",
                        "title": "Zero Salary",
                        "category": "技术",
                        "work_type": "full_time",
                        "location": "北京",
                        "description": "Test",
                        "salary_min": 0,
                        "salary_max": 0,
                    }
                ]
            },
        }
        jobs = adapter.parse(raw, context)
        assert jobs[0].salary_disclosed is False
        assert jobs[0].salary_min is None
        assert jobs[0].salary_max is None
        assert jobs[0].salary_text == ""

    def test_salary_disclosed_without_months(self, adapter, context):
        """Salary with min/max but no months should still be disclosed."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-sec-engineer-005")
        assert job.salary_disclosed is True
        assert job.salary_min == 25000
        assert job.salary_max == 45000

    def test_salary_period_monthly_for_full_time(self, adapter, context):
        """Full-time roles have monthly salary period."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        full_time_ids = {
            "chaitin-ai-security-001",
            "chaitin-pentest-002",
            "chaitin-algo-003",
            "chaitin-pm-004",
        }
        for job in jobs:
            if job.external_id in full_time_ids:
                assert job.salary_period == "monthly"

    def test_salary_period_daily_for_internship(self, adapter, context):
        """Internship roles have daily salary period."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-sec-engineer-005")
        assert job.salary_period == "daily"

    # ------------------------------------------------------------------ #
    # Location & job type
    # ------------------------------------------------------------------ #

    def test_multi_location_normalized(self, adapter, context):
        """Multi-location (北京、上海) is preserved after normalization."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-pentest-002")
        assert "北京" in job.location
        assert "上海" in job.location

    def test_job_type_from_job_category_name(self, adapter, context):
        """job_category_name is used as job_type when present."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)

        ai_job = next(j for j in jobs if j.external_id == "chaitin-ai-security-001")
        assert ai_job.job_type == "技术"

        pm_job = next(j for j in jobs if j.external_id == "chaitin-pm-004")
        assert pm_job.job_type == "产品"

    def test_job_type_fallback_to_category(self, adapter, context):
        """Fallback to category when job_category_name is absent."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        algo_job = next(j for j in jobs if j.external_id == "chaitin-algo-003")
        # Item has category="研发" and no job_category_name
        assert algo_job.job_type == "研发"

    def test_job_type_normalized_via_normalize_collected_job(self, adapter, context):
        """研发类 normalizes to 技术 through central normalize_collected_job."""
        from findjobs.collection import normalize_collected_job

        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        # Item 2 has job_category_name="研发类"
        job = next(j for j in jobs if j.external_id == "chaitin-pentest-002")
        assert job.job_type == "研发类"

        normalized = normalize_collected_job(job)
        assert normalized.job_type == "技术"

    # ------------------------------------------------------------------ #
    # Published date
    # ------------------------------------------------------------------ #

    def test_published_at_parsed_from_career_site_published_at(self, adapter, context):
        """published_at is parsed from career_site_published_at (UNIX seconds)."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        job = next(j for j in jobs if j.external_id == "chaitin-ai-security-001")
        assert job.published_at is not None
        assert job.published_at.year == 2026
        assert job.published_at.month == 6
        assert job.published_at.day == 1

    def test_published_at_not_none(self, adapter, context):
        """All fixture jobs have career_site_published_at so published_at should be set."""
        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        for job in jobs:
            assert job.published_at is not None, (
                f"published_at is None for {job.external_id}"
            )

    def test_published_at_fallback_to_created_at(self, adapter, context):
        """Fallback to created_at (ISO) when career_site_published_at is missing."""
        raw = {
            "code": 0,
            "data": {
                "items": [
                    {
                        "job_id": "fallback-test",
                        "title": "Fallback",
                        "category": "技术",
                        "work_type": "full_time",
                        "location": "北京",
                        "description": "Test",
                        "created_at": "2026-03-15T00:00:00Z",
                    }
                ]
            },
        }
        jobs = adapter.parse(raw, context)
        assert jobs[0].published_at is not None
        assert jobs[0].published_at.year == 2026
        assert jobs[0].published_at.month == 3
        assert jobs[0].published_at.day == 15

    # ------------------------------------------------------------------ #
    # Central normalization — responsibilities / requirements / job_type
    # ------------------------------------------------------------------ #

    def test_normalize_collected_job_extracts_details(self, adapter, context):
        """After normalize_collected_job, responsibilities and requirements are split."""
        from findjobs.collection import normalize_collected_job

        jobs = adapter.parse(_load_fixture("chaitin.json"), context)
        # Use job with 岗位职责： format (recognized by detail parser)
        job = next(j for j in jobs if j.external_id == "chaitin-pentest-002")
        normalized = normalize_collected_job(job)

        assert "渗透测试" in normalized.responsibilities
        assert "安全修复" in normalized.responsibilities
        assert (
            "Burp Suite" in normalized.requirements or "Nmap" in normalized.requirements
        )
        assert normalized.detail_completeness == "full"

    # ------------------------------------------------------------------ #
    # Fetch
    # ------------------------------------------------------------------ #

    def test_fetch_uses_correct_url_and_params(self, adapter, context, monkeypatch):
        """Verify fetch sends page=1 and size=20."""
        import httpx

        sent_params: dict = {}

        def mock_get(url, **kwargs):
            sent_params["url"] = url
            sent_params["params"] = kwargs.get("params")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"total_count": 0, "has_next_page": False, "items": []},
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        adapter.fetch(context)

        assert sent_params["params"] == {"page": 1, "size": 20}
        assert "api/default/jobs" in sent_params["url"]

    # ------------------------------------------------------------------ #
    # Collect — pagination & dedup
    # ------------------------------------------------------------------ #

    def test_collect_paginates_with_has_next_page(self, adapter, context, monkeypatch):
        """Collect paginates correctly and stops when has_next_page is false."""
        import httpx

        fixture = _load_fixture("chaitin.json")
        items = fixture["data"]["items"]
        requested_pages: list[int] = []

        def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            page = params.get("page", 1)
            requested_pages.append(page)

            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "total_count": 5,
                            "has_next_page": True,
                            "items": [items[0], items[1]],
                        },
                    },
                )
            elif page == 2:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "total_count": 5,
                            "has_next_page": True,
                            "items": [items[2], items[3]],
                        },
                    },
                )
            elif page == 3:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "total_count": 5,
                            "has_next_page": False,
                            "items": [items[4]],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"total_count": 5, "has_next_page": False, "items": []},
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        assert requested_pages == [1, 2, 3]
        assert len(jobs) == 5

    def test_collect_stops_when_no_items(self, adapter, context, monkeypatch):
        """Collect stops when a page returns no items."""
        import httpx

        fixture = _load_fixture("chaitin.json")
        items = fixture["data"]["items"]
        requested_pages: list[int] = []

        def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            page = params.get("page", 1)
            requested_pages.append(page)

            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "total_count": 5,
                            "has_next_page": True,
                            "items": [items[0], items[1]],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"total_count": 5, "has_next_page": False, "items": []},
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        assert len(requested_pages) == 2
        assert len(jobs) == 2

    def test_collect_deduplicates_by_job_id(self, adapter, context, monkeypatch):
        """Items with duplicate job_id are only included once."""
        import httpx

        fixture = _load_fixture("chaitin.json")
        items = fixture["data"]["items"]

        def mock_get(url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "total_count": 6,
                        "has_next_page": False,
                        "items": items + [items[0]],
                    },
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("chaitin-ai-security-001") == 1
        assert len(jobs) == 5

    def test_collect_deduplicates_fallback_title_location(
        self, adapter, context, monkeypatch
    ):
        """Items without job_id deduplicate by title+location."""
        import httpx

        def mock_get(url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "total_count": 2,
                        "has_next_page": False,
                        "items": [
                            {
                                "title": "测试岗位",
                                "location": "北京",
                                "category": "技术",
                                "work_type": "full_time",
                                "description": "测试",
                                "career_site_published_at": 1780272000,
                            },
                            {
                                "title": "测试岗位",
                                "location": "北京",
                                "category": "技术",
                                "work_type": "full_time",
                                "description": "测试",
                                "career_site_published_at": 1780272000,
                            },
                        ],
                    },
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        assert len(jobs) == 1

    def test_collect_uses_total_count_check(self, adapter, context, monkeypatch):
        """Collect stops when accumulated items reach total_count."""
        import httpx

        fixture = _load_fixture("chaitin.json")
        items = fixture["data"]["items"]

        def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            page = params.get("page", 1)

            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "total_count": 3,
                            "has_next_page": True,
                            "items": [items[0], items[1]],
                        },
                    },
                )
            # Page 2 has 2 items but total_count=3 so after collecting
            # 3 items we should stop (the check compares all_items length
            # against total = 3).
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "total_count": 3,
                        "has_next_page": True,
                        "items": [items[2], items[3]],
                    },
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        # After page 2 we have 4 items which >= 3 total, so we stop.
        # But only 3 are unique (items[3] is still included since it's
        # a new id). The dedup runs within the page loop.
        assert len(jobs) == 4

    # ------------------------------------------------------------------ #
    # Error handling
    # ------------------------------------------------------------------ #

    def test_parse_raises_on_nonzero_code(self, adapter, context):
        """parse raises ValueError when API code != 0."""
        with pytest.raises(ValueError, match="Chaitin API returned error code"):
            adapter.parse({"code": 1, "message": "bad request"}, context)

    def test_fetch_retries_on_transport_error(self, adapter, context, monkeypatch):
        """fetch retries on transport errors before giving up."""
        import httpx

        monkeypatch.setattr(time, "sleep", lambda s: None)

        attempts: list[int] = []

        def mock_get(url, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"total_count": 0, "has_next_page": False, "items": []},
                },
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        result = adapter.fetch(context)

        assert len(attempts) == 3
        assert result["code"] == 0

    def test_fetch_raises_after_exhausted_retries(self, adapter, context, monkeypatch):
        """fetch raises when all retry attempts fail on transport error."""
        import httpx

        monkeypatch.setattr(time, "sleep", lambda s: None)

        attempts: list[int] = []

        def mock_get(url, **kwargs):
            attempts.append(1)
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", mock_get)
        with pytest.raises(httpx.ConnectError):
            adapter.fetch(context)

        assert len(attempts) == 3  # exactly _MAX_RETRIES attempts
