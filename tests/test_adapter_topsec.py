"""Tests for TopSecOfficialAdapter."""

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


_SINGLE_PAGE = {
    "Code": 200,
    "Count": 2,
    "Data": [
        {
            "JobAdId": "2001",
            "JobAdName": "安全工程师",
            "LocNames": ["北京"],
            "Salary": "30k-50k",
            "Duty": "<p>研发工作</p>",
            "Require": "<p>安全背景</p>",
            "ClassificationOne": "技术类",
            "ClassificationTwo": "研发",
            "Kind": "全职",
            "PostDate": "2026-06-01",
            "PostDateInt": 1780444800,
        },
        {
            "JobAdId": "2002",
            "JobAdName": "渗透测试工程师",
            "LocNames": ["深圳"],
            "Salary": None,
            "Duty": "<p>渗透测试</p>",
            "Require": "<p>安全经验</p>",
            "ClassificationOne": "技术类",
            "ClassificationTwo": "安全服务",
            "Kind": "全职",
            "PostDate": "2026-06-15",
            "PostDateInt": 1780531200,
        },
    ],
}

_EMPTY_PAGE = {
    "Code": 200,
    "Count": 0,
    "Data": [],
}


class TestTopSecOfficialAdapter:
    """Tests for TopSecOfficialAdapter parsing and collection."""

    @pytest.fixture
    def adapter(self):
        import findjobs.adapters.topsec  # noqa: F401
        from findjobs.adapters import get_adapter

        return get_adapter("topsec_official")

    @pytest.fixture
    def context(self):
        return _context(
            "topsec",
            "topsec-careers",
            base_url="https://topsec.zhiye.com/social/jobs",
            fetch_url="https://topsec.zhiye.com/api/Jobad/GetJobAdPageList",
        )

    # ------------------------------------------------------------------
    # Parse field coverage -- real shape
    # ------------------------------------------------------------------

    def test_parses_all_jobs(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        assert len(jobs) == 5

    def test_parses_external_id_and_url(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.external_id == "1001"
        assert job.url == "https://topsec.zhiye.com/social/detail?jobAdId=1001"

    def test_parses_title(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.title == "安全研发工程师"

    # ------------------------------------------------------------------
    # Detail: Duty and Require preserved in description
    # ------------------------------------------------------------------

    def test_description_contains_duty_and_require(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert "岗位职责" in job.description or "负责" in job.description
        assert "任职要求" in job.description or "本科" in job.description

    # ------------------------------------------------------------------
    # Job type from ClassificationOne / ClassificationTwo / Kind
    # ------------------------------------------------------------------

    def test_job_type_uses_factual_category_text(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert "技术类" in job.job_type
        assert "研发" in job.job_type
        assert "全职" in job.job_type
        # Avoid a single opaque code as the only type.
        assert job.job_type != "技术类"

    # ------------------------------------------------------------------
    # Multi-location -- preserve every LocNames value
    # ------------------------------------------------------------------

    def test_multi_location_uses_dunhao_delimiter(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1005")
        assert "、" in job.location
        assert "北京" in job.location
        assert "杭州" in job.location
        assert "深圳" in job.location

    def test_single_location_no_delimiter(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert "、" not in job.location
        assert job.location == "北京"

    # ------------------------------------------------------------------
    # Salary boundary -- parse official salary, null -> undisclosed
    # ------------------------------------------------------------------

    def test_parses_salary_disclosed(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.salary_disclosed is True
        assert job.salary_min == 30000.0
        assert job.salary_max == 50000.0
        assert job.salary_text == "30k-50k"

    def test_parses_salary_with_bonus_months(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1003")
        assert job.salary_disclosed is True
        assert job.salary_min == 40000.0
        assert job.salary_max == 60000.0

    def test_parses_salary_undisclosed_when_empty(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1002")
        assert job.salary_disclosed is False
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.salary_text == ""

    def test_parses_salary_undisclosed_when_null(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1005")
        assert job.salary_disclosed is False
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.salary_text == ""

    # ------------------------------------------------------------------
    # Published at: PostDate then PostDateInt
    # ------------------------------------------------------------------

    def test_parses_published_at_from_post_date(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert job.published_at is not None
        assert job.published_at.year == 2026
        assert job.published_at.month == 7
        assert job.published_at.day == 1

    # ------------------------------------------------------------------
    # Classification -- algorithm and functional risk/strategy excluded
    # ------------------------------------------------------------------

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        algo = next(j for j in jobs if j.external_id == "1003")
        assert algo.matched_tags == []

    def test_functional_risk_strategy_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        risk = next(j for j in jobs if j.external_id == "1004")
        assert risk.matched_tags == []

    def test_security_engineer_gets_security_tags(self, adapter, context):
        jobs = adapter.parse(_load_fixture("topsec.json"), context)
        job = next(j for j in jobs if j.external_id == "1001")
        assert "Security" in job.matched_tags or "AI Security" in job.matched_tags

    # ------------------------------------------------------------------
    # Code validation
    # ------------------------------------------------------------------

    def test_parse_raises_on_nonzero_code(self, adapter, context):
        raw = {"Code": 400, "Count": 0, "Data": []}
        with pytest.raises(ValueError, match="TopSec API returned error code 400"):
            adapter.parse(raw, context)

    def test_parse_raises_on_missing_code(self, adapter, context):
        raw = {"Count": 0, "Data": []}
        with pytest.raises(ValueError, match="TopSec API returned error code None"):
            adapter.parse(raw, context)

    # ------------------------------------------------------------------
    # UNIX timestamp parsing (seconds and milliseconds)
    # ------------------------------------------------------------------

    def test_parse_unix_timestamp_seconds(self):
        import findjobs.adapters.topsec as ts

        dt = ts._parse_unix_timestamp(1780272000)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 1
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.second == 0

    def test_parse_unix_timestamp_milliseconds(self):
        import findjobs.adapters.topsec as ts

        dt = ts._parse_unix_timestamp(1780272000000)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 1
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.second == 0

    def test_parse_unix_timestamp_none(self):
        import findjobs.adapters.topsec as ts

        assert ts._parse_unix_timestamp(None) is None

    # ------------------------------------------------------------------
    # _build_payload uses current _PAGE_SIZE
    # ------------------------------------------------------------------

    def test_build_payload_uses_current_page_size(self):
        import findjobs.adapters.topsec as ts

        payload = ts._build_payload(0)
        assert payload["PageSize"] == 100

    # ------------------------------------------------------------------
    # Collect: pagination and dedup
    # ------------------------------------------------------------------

    def test_collect_paginates_using_count(self, adapter, context, monkeypatch):
        import findjobs.adapters.topsec as ts

        monkeypatch.setattr(ts, "_PAGE_SIZE", 2)

        call_pages: list[int] = []

        def mock_fetch_page(url: str, page_index: int) -> dict:
            call_pages.append(page_index)
            if page_index == 0:
                return {
                    "Code": 200,
                    "Count": 4,
                    "Data": [
                        {
                            "JobAdId": "3001",
                            "JobAdName": "岗位A",
                            "LocNames": ["北京"],
                            "Salary": None,
                            "Duty": "",
                            "Require": "",
                            "ClassificationOne": "技术类",
                            "ClassificationTwo": "研发",
                            "Kind": "全职",
                            "PostDate": "2026-06-01",
                            "PostDateInt": 1780444800,
                        },
                        {
                            "JobAdId": "3002",
                            "JobAdName": "岗位B",
                            "LocNames": ["上海"],
                            "Salary": None,
                            "Duty": "",
                            "Require": "",
                            "ClassificationOne": "技术类",
                            "ClassificationTwo": "研发",
                            "Kind": "全职",
                            "PostDate": "2026-06-01",
                            "PostDateInt": 1780444800,
                        },
                    ],
                }
            return {
                "Code": 200,
                "Count": 4,
                "Data": [
                    {
                        "JobAdId": "3003",
                        "JobAdName": "岗位C",
                        "LocNames": ["广州"],
                        "Salary": None,
                        "Duty": "",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                    {
                        "JobAdId": "3004",
                        "JobAdName": "岗位D",
                        "LocNames": ["深圳"],
                        "Salary": None,
                        "Duty": "",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                ],
            }

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        jobs = adapter.collect(context)
        assert call_pages == [0, 1]
        assert len(jobs) == 4
        ext_ids = [j.external_id for j in jobs]
        assert ext_ids == ["3001", "3002", "3003", "3004"]
        # _build_payload respects the monkeypatched _PAGE_SIZE.
        assert ts._build_payload(0)["PageSize"] == 2
        assert ts._build_payload(1)["PageSize"] == 2

    def test_collect_stops_on_short_page(self, adapter, context, monkeypatch):
        import findjobs.adapters.topsec as ts

        call_pages: list[int] = []

        def mock_fetch_page(url: str, page_index: int) -> dict:
            call_pages.append(page_index)
            if page_index == 0:
                return _SINGLE_PAGE
            return _EMPTY_PAGE

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        adapter.collect(context)
        # _SINGLE_PAGE has 2 items (< _PAGE_SIZE 100) -> short-page stop.
        assert call_pages == [0]

    def test_collect_deduplicates_by_job_ad_id(self, adapter, context, monkeypatch):
        """Dedup removes same JobAdId appearing within a single page."""
        import findjobs.adapters.topsec as ts

        def mock_fetch_page(url: str, page_index: int) -> dict:
            # Both items on page 0 include the duplicate 5001.
            return {
                "Code": 200,
                "Count": 3,
                "Data": [
                    {
                        "JobAdId": "5001",
                        "JobAdName": "重复岗位",
                        "LocNames": ["北京"],
                        "Salary": None,
                        "Duty": "<p>重复</p>",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                    {
                        "JobAdId": "5002",
                        "JobAdName": "唯一岗位A",
                        "LocNames": ["杭州"],
                        "Salary": None,
                        "Duty": "<p>唯一A</p>",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                    {
                        "JobAdId": "5001",
                        "JobAdName": "重复岗位",
                        "LocNames": ["北京"],
                        "Salary": None,
                        "Duty": "<p>重复</p>",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                ],
            }

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        jobs = adapter.collect(context)
        ext_ids = [j.external_id for j in jobs]
        assert ext_ids.count("5001") == 1
        assert "5002" in ext_ids

    def test_collect_deduplicates_by_title_location_fallback(self, adapter, context, monkeypatch):
        """Fallback dedup when JobAdId is missing."""
        import findjobs.adapters.topsec as ts

        def mock_fetch_page(url: str, page_index: int) -> dict:
            return {
                "Code": 200,
                "Count": 2,
                "Data": [
                    {
                        "JobAdId": "",
                        "JobAdName": "通用岗位",
                        "LocNames": ["上海"],
                        "Salary": None,
                        "Duty": "<p>工作内容</p>",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                    {
                        "JobAdId": "",
                        "JobAdName": "通用岗位",
                        "LocNames": ["上海"],
                        "Salary": None,
                        "Duty": "<p>工作内容</p>",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "2026-06-01",
                        "PostDateInt": 1780444800,
                    },
                ],
            }

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        jobs = adapter.collect(context)
        assert len(jobs) == 1  # deduplicated by (title, location)

    # ------------------------------------------------------------------
    # fetch_url and base_url propagation
    # ------------------------------------------------------------------

    def test_fetch_url_used_in_collect(self, adapter, context, monkeypatch):
        import findjobs.adapters.topsec as ts

        captured: list[tuple[str, int]] = []

        def mock_fetch_page(url: str, page_index: int) -> dict:
            captured.append((url, page_index))
            return _EMPTY_PAGE

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        adapter.collect(context)
        assert len(captured) >= 1
        assert captured[0][0] == context.fetch_url

    def test_list_url_fallback_when_fetch_url_empty(self, adapter, monkeypatch):
        import findjobs.adapters.topsec as ts

        ctx = _context(
            "topsec", "topsec-careers",
            base_url="https://topsec.zhiye.com/social/jobs",
            fetch_url="",
        )
        captured: list[tuple[str, int]] = []

        def mock_fetch_page(url: str, page_index: int) -> dict:
            captured.append((url, page_index))
            return _EMPTY_PAGE

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        adapter.collect(ctx)
        assert len(captured) >= 1
        # When fetch_url is empty, collect falls back to _LIST_URL (the API POST
        # endpoint), not to base_url (which is a public browser page).
        assert captured[0][0] == ts._LIST_URL

    # ------------------------------------------------------------------
    # Base-url origin for detail URL
    # ------------------------------------------------------------------

    def test_collect_uses_base_url_origin_for_detail_url(self, adapter, monkeypatch):
        import findjobs.adapters.topsec as ts

        ctx = _context(
            "topsec", "topsec-careers",
            base_url="https://custom.topsec.zhiye.com/social/jobs",
            fetch_url="https://topsec.zhiye.com/api/Jobad/GetJobAdPageList",
        )

        def mock_fetch_page(url: str, page_index: int) -> dict:
            return {
                "Code": 200,
                "Count": 1,
                "Data": [
                    {
                        "JobAdId": "9001",
                        "JobAdName": "安全研发工程师",
                        "LocNames": ["北京"],
                        "Salary": "30k-50k",
                        "Duty": "",
                        "Require": "",
                        "ClassificationOne": "技术类",
                        "ClassificationTwo": "研发",
                        "Kind": "全职",
                        "PostDate": "",
                        "PostDateInt": 1780444800,
                    },
                ],
            }

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        jobs = adapter.collect(ctx)
        assert len(jobs) == 1
        assert jobs[0].url == "https://custom.topsec.zhiye.com/social/detail?jobAdId=9001"

    # ------------------------------------------------------------------
    # Code validation in collect
    # ------------------------------------------------------------------

    def test_collect_raises_on_nonzero_code(self, adapter, context, monkeypatch):
        import findjobs.adapters.topsec as ts

        def mock_fetch_page(url: str, page_index: int) -> dict:
            return {"Code": 500, "Count": 0, "Data": []}

        monkeypatch.setattr(ts, "_fetch_page", mock_fetch_page)

        with pytest.raises(ValueError, match="TopSec API returned error code 500"):
            adapter.collect(context)

    # ------------------------------------------------------------------
    # Retry: _fetch_page retries transport/5xx, not 4xx
    # ------------------------------------------------------------------

    def test_fetch_page_retries_on_transport_error(self, monkeypatch):
        import findjobs.adapters.topsec as ts

        attempts = [0]
        orig_client = ts.httpx.Client

        def mock_client(**kw):
            class _MockPost:
                def post(self, url, **kw2):
                    attempts[0] += 1
                    raise ts.httpx.TransportError("connection failed")

            class _MockCtx:
                def __enter__(self):
                    return _MockPost()
                def __exit__(self, *a):
                    pass

            return _MockCtx()

        monkeypatch.setattr(ts.httpx, "Client", mock_client)

        with pytest.raises(ts.httpx.TransportError):
            ts._fetch_page("https://example.com/api", page_index=0)

        assert attempts[0] == 3

    def test_fetch_page_retries_on_500(self, monkeypatch):
        import findjobs.adapters.topsec as ts

        attempts = [0]

        class _MockPost:
            def post(self, url, **kw):
                attempts[0] += 1
                exc = ts.httpx.HTTPStatusError(
                    "500", request=None, response=type("r", (object,), {"status_code": 500})()
                )
                raise exc

        class _MockCtx:
            def __enter__(self):
                return _MockPost()
            def __exit__(self, *a):
                pass

        monkeypatch.setattr(ts.httpx, "Client", lambda **kw: _MockCtx())

        with pytest.raises(ts.httpx.HTTPStatusError):
            ts._fetch_page("https://example.com/api", page_index=0)

        assert attempts[0] == 3

    def test_fetch_page_no_retry_on_400(self, monkeypatch):
        import findjobs.adapters.topsec as ts

        attempts = [0]

        class _MockPost:
            def post(self, url, **kw):
                attempts[0] += 1
                exc = ts.httpx.HTTPStatusError(
                    "400", request=None, response=type("r", (object,), {"status_code": 400})()
                )
                raise exc

        class _MockCtx:
            def __enter__(self):
                return _MockPost()
            def __exit__(self, *a):
                pass

        monkeypatch.setattr(ts.httpx, "Client", lambda **kw: _MockCtx())

        with pytest.raises(ts.httpx.HTTPStatusError):
            ts._fetch_page("https://example.com/api", page_index=0)

        assert attempts[0] == 1
