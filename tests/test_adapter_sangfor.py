"""Tests for SangforOfficialAdapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "adapters"


def _load_fixture(name: str):
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _context(company: str, source: str, base_url: str = "", fetch_url: str = ""):
    from findjobs.adapters import AdapterContext

    return AdapterContext(
        company_slug=company,
        source_slug=source,
        base_url=base_url,
        fetch_url=fetch_url,
    )


_MOCK_PAGE = {
    "code": 0,
    "message": "success",
    "data": {
        "count": 3,
        "listData": [
            {
                "positionId": pid,
                "title": title,
                "positionState": 1,
                "description": "<p>test</p>",
                "minSalary": ms,
                "maxSalary": mas,
                "commitment": "全职",
                "openedAt": "2026-01-01",
            }
            for pid, title, ms, mas in [
                (2001, "安全工程师", 0, 0),
                (2002, "安全研究员", 0, 0),
                (2003, "安全专家", 0, 0),
            ]
        ],
    },
}

_EMPTY_PAGE = {
    "code": 0,
    "message": "success",
    "data": {"count": 0, "listData": []},
}


class TestSangforOfficialAdapter:
    """Tests for SangforOfficialAdapter parsing and collection."""

    @pytest.fixture
    def adapter(self):
        import findjobs.adapters.sangfor  # noqa: F401
        from findjobs.adapters import get_adapter

        return get_adapter("sangfor_official")

    @pytest.fixture
    def context(self):
        return _context(
            "sangfor",
            "sangfor-careers",
            base_url="https://hr.sangfor.com/Sociology",
            fetch_url="https://hr.sangfor.com/webapi/api/Jobs",
        )

    # ------------------------------------------------------------------
    # Parse field coverage
    # ------------------------------------------------------------------

    def test_parses_all_jobs(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        assert len(jobs) == 6

    def test_parses_external_id_and_url(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.external_id == "1001"
        assert job.url == "https://hr.sangfor.com/Delivery/1001"

    def test_parses_title_description_and_type(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.title == "AI安全平台工程师"
        assert "岗位职责" in job.description
        assert "任职要求" in job.description
        # job_type built from functionName / departmentName / commitment
        assert job.job_type == "研发 / AI安全平台部 / 全职"

    def test_parses_salary_disclosed(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.salary_disclosed is True
        assert job.salary_min == 30000.0
        assert job.salary_max == 60000.0
        assert job.salary_text == "30000-60000"

    def test_parses_salary_undisclosed_when_zero(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1002")
        assert job.salary_disclosed is False
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.salary_text == ""

    def test_parses_salary_undisclosed_when_one_bound_zero(self, adapter, context):
        """salary_disclosed=False when only one bound is positive."""
        raw = {
            "code": 0,
            "message": "success",
            "data": {
                "count": 1,
                "listData": [
                    {
                        "positionId": 3001,
                        "title": "Partial Salary",
                        "minSalary": 30000,
                        "maxSalary": 0,
                        "commitment": "全职",
                        "openedAt": "2026-01-01",
                    }
                ],
            },
        }
        jobs = adapter.parse(raw, context)
        assert jobs[0].salary_disclosed is False
        assert jobs[0].salary_min is None
        assert jobs[0].salary_max is None
        assert jobs[0].salary_text == ""

    def test_parses_multi_location(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1002")
        assert "、" in job.location
        assert "北京" in job.location
        assert "深圳" in job.location

    def test_parses_published_at(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.published_at is not None
        assert job.published_at.year == 2026
        assert job.published_at.month == 3
        assert job.published_at.day == 15

    def test_parses_empty_description_gracefully(self, adapter, context):
        jobs = adapter.parse(
            {
                "code": 0,
                "message": "success",
                "data": {
                    "count": 1,
                    "listData": [
                        {
                            "positionId": 2001,
                            "title": "Empty Description Job",
                            "openedAt": "2026-01-01",
                        }
                    ],
                },
            },
            context,
        )
        assert len(jobs) == 1
        assert jobs[0].description == ""

    def test_salary_period_empty(self, adapter, context):
        """salary_period is empty string (no hardcoded monthly)."""
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        for job in jobs:
            assert job.salary_period == ""

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        algo = next(j for j in jobs if j.external_id == "1003")
        assert algo.matched_tags == []

    def test_functional_risk_strategy_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        risk = next(j for j in jobs if j.external_id == "1004")
        assert risk.matched_tags == []

    def test_ai_security_platform_engineering_gets_ai_tags(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert "AI" in job.matched_tags or "AI Security" in job.matched_tags
        assert "Security" in job.matched_tags or "AI Security" in job.matched_tags

    def test_security_engineer_gets_security_tags(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1002")
        assert "Security" in job.matched_tags or "AI Security" in job.matched_tags

    def test_security_operations_gets_security_tags(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1005")
        assert "Security" in job.matched_tags or "AI Security" in job.matched_tags

    def test_ai_research_engineer_gets_ai_tags(self, adapter, context):
        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1006")
        assert "AI" in job.matched_tags or "AI Security" in job.matched_tags

    # ------------------------------------------------------------------
    # Central normalization — job_type maps functionName/category
    # ------------------------------------------------------------------

    def test_job_type_normalized_via_normalize_collected_job(self, adapter, context):
        """Raw job_type from functionName/departmentName normalizes via
        central normalize_collected_job.

        ``研发`` → ``技术``,  ``AI安全平台部`` → ``AI工程``,  ``全职`` preserved.
        Sorted: AI工程、技术、全职.
        """
        from findjobs.collection import normalize_collected_job

        jobs = adapter.parse(_load_fixture("sangfor.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        # Raw value before normalization.
        assert "研发" in job.job_type
        assert "AI安全平台部" in job.job_type
        assert "全职" in job.job_type

        normalized = normalize_collected_job(job)
        assert "AI工程" in normalized.job_type
        assert "技术" in normalized.job_type
        assert "全职" in normalized.job_type

    # ------------------------------------------------------------------
    # Code validation
    # ------------------------------------------------------------------

    def test_fetch_page_raises_on_nonzero_code(self, adapter, context, monkeypatch):
        """_fetch_page raises ValueError when API code != 0."""
        import findjobs.adapters.sangfor as sf

        monkeypatch.setattr(
            sf.SangforOfficialAdapter, "_fetch_token", lambda self: "test-token"
        )

        def mock_request(*args, **kwargs):
            resp = pytest.importorskip("httpx").Response(
                200,
                json={"code": 1, "message": "bad request", "data": {"listData": []}},
            )
            return resp

        monkeypatch.setattr(sf, "_request_with_retry", mock_request)

        with pytest.raises(ValueError, match="Sangfor API returned error code 1"):
            adapter.fetch(context)

    # ------------------------------------------------------------------
    # Collect: pagination and dedup
    # ------------------------------------------------------------------

    def test_collect_stops_on_short_page(self, adapter, context, monkeypatch):
        import findjobs.adapters.sangfor as sf

        monkeypatch.setattr(
            sf.SangforOfficialAdapter, "_fetch_token", lambda self: "test-token"
        )

        call_pages: list[int] = []

        def mock_fetch_page(self, *, token, page, keyword, **kwargs):
            call_pages.append(page)
            if page == 1:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "count": 3,
                        "listData": [
                            {
                                "positionId": p,
                                "title": f"工程师{p}",
                                "workPlaceText": "深圳",
                                "functionName": "技术",
                                "departmentName": "研发部",
                                "commitment": "全职",
                                "description": "<p>研发</p>",
                                "minSalary": 0,
                                "maxSalary": 0,
                                "openedAt": "2026-01-01",
                            }
                            for p in range(1, 3)
                        ],
                    },
                }
            return _EMPTY_PAGE

        monkeypatch.setattr(
            sf.SangforOfficialAdapter, "_fetch_page", mock_fetch_page
        )

        jobs = adapter.collect(context)
        assert len(jobs) >= 1
        # Verify that no keyword ever progressed beyond page 1
        # (short-page stop triggers within each keyword)
        assert all(p == 1 for p in call_pages)

    def test_collect_deduplicates_by_position_id(self, adapter, context, monkeypatch):
        import findjobs.adapters.sangfor as sf

        monkeypatch.setattr(
            sf.SangforOfficialAdapter, "_fetch_token", lambda self: "test-token"
        )

        call_count = [0]

        def mock_fetch_page(self, *, token, page, keyword, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "count": 3,
                        "listData": [
                            {
                                "positionId": 9999,
                                "title": "重复岗位",
                                "workPlaceText": "北京",
                                "functionName": "技术",
                                "departmentName": "研发部",
                                "commitment": "全职",
                                "description": "<p>重复</p>",
                                "minSalary": 0,
                                "maxSalary": 0,
                                "openedAt": "2026-01-01",
                            },
                            {
                                "positionId": 10001,
                                "title": "唯一岗位A",
                                "workPlaceText": "杭州",
                                "functionName": "研发",
                                "departmentName": "产品部",
                                "commitment": "全职",
                                "description": "<p>唯一A</p>",
                                "minSalary": 0,
                                "maxSalary": 0,
                                "openedAt": "2026-01-01",
                            },
                        ],
                    },
                }
            return {
                "code": 0,
                "message": "success",
                "data": {
                    "count": 2,
                    "listData": [
                        {
                            "positionId": 9999,
                            "title": "重复岗位",
                            "workPlaceText": "北京",
                            "functionName": "技术",
                            "departmentName": "研发部",
                            "commitment": "全职",
                            "description": "<p>重复</p>",
                            "minSalary": 0,
                            "maxSalary": 0,
                            "openedAt": "2026-01-01",
                        },
                        {
                            "positionId": 10002,
                            "title": "唯一岗位B",
                            "workPlaceText": "上海",
                            "functionName": "研发",
                            "departmentName": "产品部",
                            "commitment": "全职",
                            "description": "<p>唯一B</p>",
                            "minSalary": 0,
                            "maxSalary": 0,
                            "openedAt": "2026-01-01",
                        },
                    ],
                },
            }

        monkeypatch.setattr(
            sf.SangforOfficialAdapter, "_fetch_page", mock_fetch_page
        )

        jobs = adapter.collect(context)
        ext_ids = [j.external_id for j in jobs]
        assert ext_ids.count("9999") == 1
        assert "10001" in ext_ids
        assert "10002" in ext_ids

    def test_collect_acquires_token_once(self, adapter, context, monkeypatch):
        import findjobs.adapters.sangfor as sf

        token_calls = [0]

        def mock_token(self):
            token_calls[0] += 1
            return "test-token"

        monkeypatch.setattr(sf.SangforOfficialAdapter, "_fetch_token", mock_token)

        def mock_fetch_page(self, *, token, page, keyword, **kwargs):
            return _EMPTY_PAGE

        monkeypatch.setattr(
            sf.SangforOfficialAdapter, "_fetch_page", mock_fetch_page
        )

        adapter.collect(context)
        assert token_calls[0] == 1

    # ------------------------------------------------------------------
    # Request payload
    # ------------------------------------------------------------------

    def test_build_payload_structure(self):
        from findjobs.adapters.sangfor import _build_payload

        payload = _build_payload(page=2, keyword="安全")
        assert payload["page"] == 2
        assert payload["kw"] == "安全"
        assert payload["pageSize"] == 100
        assert payload["channelId"] == 110
        assert set(payload.keys()) == {
            "channelId",
            "page",
            "pageSize",
            "departmentId",
            "functionId",
            "kw",
            "locationId",
            "workPlaceId",
        }
