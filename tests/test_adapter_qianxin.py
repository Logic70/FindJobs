"""Tests for the Qianxin official Hotjob adapter.

Covers parse-field extraction, one-based pagination, bounded retry, API
contract validation, detail enrichment, dedup, list-fact preservation, and
normalize_collected_job round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from findjobs.adapters.keywords import TARGET_KEYWORDS

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "adapters"


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _list_response(
    details: list[dict[str, Any]], row_count: int | None = None
) -> dict[str, Any]:
    return {
        "code": "00",
        "data": {
            "rowCount": row_count if row_count is not None else len(details),
            "details": details,
        },
    }


def _empty_response() -> dict[str, Any]:
    return {"code": "00", "data": {"details": [], "rowCount": 0}}


# ---------------------------------------------------------------------------
# Mock HTTP layer
# ---------------------------------------------------------------------------


class _MockResponse:
    """Minimal httpx-like response for monkey-patching."""

    def __init__(
        self, json_data: dict[str, Any] | None = None, status_code: int = 200
    ) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        if self._json_data is not None:
            return self._json_data
        raise json.JSONDecodeError("No JSON", "", 0)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://mock")
            response = httpx.Response(status_code=self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=response,
            )


class _MockClient:
    """Stateful mock httpx.Client that records calls and returns pre-set responses."""

    def __init__(self, **kwargs: Any) -> None:
        self._get_responses: list[_MockResponse] = []
        self._post_responses: list[_MockResponse] = []
        self._get_index = 0
        self._post_index = 0
        self.calls: list[dict[str, Any]] = []

    def add_get(self, resp: _MockResponse) -> None:
        self._get_responses.append(resp)

    def add_post(self, resp: _MockResponse) -> None:
        self._post_responses.append(resp)

    def request(self, method: str, url: str, **kwargs: Any) -> _MockResponse:
        data = kwargs.get("data")
        self.calls.append({"method": method, "url": url, "data": data})
        if method == "GET":
            idx = self._get_index
            self._get_index += 1
            if idx < len(self._get_responses):
                return self._get_responses[idx]
        elif method == "POST":
            idx = self._post_index
            self._post_index += 1
            if idx < len(self._post_responses):
                return self._post_responses[idx]
        return _MockResponse({"code": "00", "data": {}})

    def __enter__(self) -> _MockClient:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _pad_post_responses(client: _MockClient, count: int | None = None) -> None:
    """Pad the mock client's post responses with empty pages."""
    remaining = count if count is not None else len(TARGET_KEYWORDS) - 1
    for _ in range(remaining):
        client.add_post(_MockResponse(_empty_response()))


# ===================================================================
# Parse tests (offline fixture)
# ===================================================================


class TestQianxinOfficialParse:
    """Deterministic offline parsing from the enriched fixture."""

    @pytest.fixture
    def adapter(self) -> Any:
        from findjobs.adapters import get_adapter

        return get_adapter("qianxin_official")

    @pytest.fixture
    def raw(self) -> dict[str, Any]:
        return _load_fixture("qianxin.json")

    @pytest.fixture
    def context(self) -> Any:
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="qianxin",
            source_slug="qianxin-careers",
            base_url="https://www.hotjob.cn/wt/qianxin/web/index",
        )

    def test_parses_all_five_jobs(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 5

    def test_mobile_security_engineer_fields(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        assert job.title == "移动安全工程师"
        assert job.location == "北京"
        assert job.job_type == "安全技术"
        assert job.salary_disclosed is True
        assert job.salary_text == "10-15k/月"
        assert job.salary_min == 10000.0
        assert job.salary_max == 15000.0
        assert "Security" in job.matched_tags

    def test_multi_city_location_parsed(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1002")
        assert job.title == "安全开发工程师"
        assert "北京" in job.location
        assert "上海" in job.location

    def test_salary_k_format(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1002")
        assert job.salary_disclosed is True
        assert job.salary_text == "20k-35k"
        assert job.salary_min == 20000.0
        assert job.salary_max == 35000.0

    def test_salary_with_bonus(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1003")
        assert job.salary_disclosed is True
        assert "·15薪" in job.salary_text
        assert job.salary_min == 30000.0
        assert job.salary_max == 50000.0

    def test_salary_empty_undisclosed(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1004")
        assert job.salary_disclosed is False
        assert job.salary_text == ""
        assert job.salary_min is None
        assert job.salary_max is None

    def test_external_ids_unique_and_non_empty(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        ids = [j.external_id for j in jobs]
        assert all(ids)
        assert len(ids) == len(set(ids))

    def test_stable_hash_url(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        assert job.url.startswith(
            "https://www.hotjob.cn/wt/qianxin/web/index#/positionDetail"
        )
        assert "PostId=QN1001" in job.url
        assert "RecruitType=2" in job.url

    def test_requirements_and_responsibilities_in_description(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        desc = job.description
        assert "移动安全漏洞挖掘" in desc
        assert "安全防护技术" in desc
        assert "移动安全攻防" in desc
        assert "Android/iOS逆向分析" in desc
        assert "ARM汇编" in desc

    def test_description_has_section_headers(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        assert "职责:" in job.description
        assert "要求:" in job.description

    def test_classify_ai_security_engineer(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1003")
        assert "AI" in job.matched_tags
        assert "Security" in job.matched_tags
        assert "AI Security" in job.matched_tags

    def test_classify_mobile_security(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        assert "Security" in job.matched_tags
        assert "AI" not in job.matched_tags

    def test_algorithm_role_has_no_tags(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1004")
        assert job.matched_tags == []

    def test_functional_admin_role_has_no_tags(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1005")
        assert job.matched_tags == []

    def test_published_at_parsed(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        assert job.published_at is not None
        assert job.published_at.year == 2026
        assert job.published_at.month == 7
        assert job.published_at.day == 1

    def test_normalize_collected_job_round_trip(
        self, adapter: Any, raw: dict[str, Any], context: Any
    ) -> None:
        """Verify requirements/responsibilities through normalize_collected_job."""
        from findjobs.collection import normalize_collected_job

        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "QN1001")
        normalized = normalize_collected_job(job)
        assert "移动安全漏洞挖掘" in normalized.responsibilities
        assert "移动安全攻防" in normalized.requirements
        assert normalized.detail_completeness == "full"


# ===================================================================
# Collect tests (mocked HTTP layer)
# ===================================================================


class TestQianxinOfficialCollect:
    """Live-collect logic tested through mocked HTTP responses."""

    @pytest.fixture
    def adapter(self) -> Any:
        from findjobs.adapters import get_adapter

        return get_adapter("qianxin_official")

    @pytest.fixture
    def context(self) -> Any:
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="qianxin",
            source_slug="qianxin-careers",
            base_url="https://www.hotjob.cn/wt/qianxin/web/index",
        )

    # -- One-based pagination --------------------------------------------------

    def test_one_based_row_index(self, adapter: Any, context: Any) -> None:
        """RowIndex must start at 1 and increment by 1 per page (page number, not item offset)."""
        # First page: full PAGE_SIZE items → triggers second page fetch.
        page0 = [
            {"PostId": f"P{i:04d}", "PostName": f"E{i}", "WorkPlace": "北京"}
            for i in range(100)
        ]
        # Second page: fewer than PAGE_SIZE → stops pagination.
        page1 = [
            {"PostId": f"P{i:04d}", "PostName": f"E{i}", "WorkPlace": "北京"}
            for i in range(100, 150)
        ]

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        client.add_post(_MockResponse(_list_response(page0, row_count=150)))
        client.add_post(_MockResponse(_list_response(page1, row_count=150)))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 150

        # Verify the rowIndex values in POST data for the first keyword.
        post_calls = [c for c in client.calls if c["method"] == "POST"]
        assert len(post_calls) >= 2
        first_keyword_posts = [
            c
            for c in post_calls
            if c["data"] and c["data"].get("postName") == TARGET_KEYWORDS[0]
        ]
        assert len(first_keyword_posts) >= 2
        assert first_keyword_posts[0]["data"]["rowIndex"] == "1"
        assert first_keyword_posts[1]["data"]["rowIndex"] == "2"

    def test_fetch_uses_one_based_row_index(self, adapter: Any, context: Any) -> None:
        """Fetch must send rowIndex=1."""
        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        client.add_post(
            _MockResponse(
                _list_response(
                    [{"PostId": "P1", "PostName": "E1", "WorkPlace": "北京"}]
                )
            )
        )

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            result = adapter.fetch(context)

        assert result["code"] == "00"
        post_calls = [c for c in client.calls if c["method"] == "POST"]
        assert len(post_calls) == 1
        assert post_calls[0]["data"]["rowIndex"] == "1"

    # -- Retry tests -----------------------------------------------------------

    @patch("findjobs.adapters.qianxin.time.sleep")
    def test_retry_success_on_bootstrap(
        self, mock_sleep: Any, adapter: Any, context: Any
    ) -> None:
        """Bootstrap GET fails with 5xx once, succeeds on retry."""
        client = _MockClient()
        # Bootstrap GET: first attempt fails, second succeeds.
        client.add_get(_MockResponse({"code": "00", "data": {}}, status_code=500))
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        # List POST for keyword 0 page 0: empty.
        client.add_post(_MockResponse(_empty_response()))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert jobs == []
        # Bootstrap GET should have been called twice (initial + retry).
        get_calls = [c for c in client.calls if c["method"] == "GET"]
        assert len(get_calls) == 2

    @patch("findjobs.adapters.qianxin.time.sleep")
    def test_retry_exhaustion_raises(
        self, mock_sleep: Any, adapter: Any, context: Any
    ) -> None:
        """Bootstrap GET fails with 5xx on all attempts."""
        client = _MockClient()
        # All three GET attempts return 500.
        client.add_get(_MockResponse({"code": "00", "data": {}}, status_code=500))
        client.add_get(_MockResponse({"code": "00", "data": {}}, status_code=500))
        client.add_get(_MockResponse({"code": "00", "data": {}}, status_code=500))

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            with pytest.raises(httpx.HTTPStatusError):
                adapter.collect(context)

        get_calls = [c for c in client.calls if c["method"] == "GET"]
        assert len(get_calls) == 3

    @patch("findjobs.adapters.qianxin.time.sleep")
    def test_fourxx_not_retried(
        self, mock_sleep: Any, adapter: Any, context: Any
    ) -> None:
        """A 403 response must not be retried."""
        client = _MockClient()
        # Bootstrap GET returns 403.
        client.add_get(_MockResponse({"code": "00", "data": {}}, status_code=403))

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            with pytest.raises(httpx.HTTPStatusError):
                adapter.collect(context)

        # Only one GET attempt — no retry.
        get_calls = [c for c in client.calls if c["method"] == "GET"]
        assert len(get_calls) == 1

    # -- API contract error ----------------------------------------------------

    def test_list_contract_error_raises(self, adapter: Any, context: Any) -> None:
        """Non-00 code from list API must raise ValueError."""
        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        # List POST returns error code.
        client.add_post(_MockResponse({"code": "01", "message": "error"}))

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            with pytest.raises(ValueError, match="Qianxin list API returned code"):
                adapter.collect(context)

    # -- Dedup tests -----------------------------------------------------------

    def test_deduplicates_by_post_id(self, adapter: Any, context: Any) -> None:
        """Same PostId across keywords must appear only once."""
        post_a = {
            "PostId": "QN2001",
            "PostName": "安全工程师",
            "WorkPlace": "北京",
            "Salary": "20k-30k",
        }
        post_b = {
            "PostId": "QN2002",
            "PostName": "渗透测试工程师",
            "WorkPlace": "上海",
            "Salary": "25k-40k",
        }

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        client.add_post(_MockResponse(_list_response([post_a, post_b])))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 2
        ids = {j.external_id for j in jobs}
        assert ids == {"QN2001", "QN2002"}

    def test_deduplicates_same_post_across_keywords(
        self, adapter: Any, context: Any
    ) -> None:
        """Same job from two different keywords must not duplicate."""
        post = {
            "PostId": "QN3001",
            "PostName": "安全工程师",
            "WorkPlace": "北京",
            "Salary": "20k-30k",
        }

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        client.add_post(_MockResponse(_list_response([post])))
        client.add_post(_MockResponse(_empty_response()))  # keyword 0 page 1
        client.add_post(_MockResponse(_list_response([post])))  # keyword 1
        _pad_post_responses(client, count=len(TARGET_KEYWORDS) - 2)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 1
        assert jobs[0].external_id == "QN3001"

    # -- Pagination tests ------------------------------------------------------

    def test_stops_on_short_page(self, adapter: Any, context: Any) -> None:
        """A page with fewer items than PAGE_SIZE must stop pagination."""
        post = {
            "PostId": "QN4001",
            "PostName": "安全工程师",
            "WorkPlace": "北京",
            "Salary": "20k-30k",
        }

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        client.add_post(_MockResponse(_list_response([post], row_count=1)))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 1
        assert jobs[0].external_id == "QN4001"

    def test_stops_when_total_reached(self, adapter: Any, context: Any) -> None:
        """When keyword_item_count reaches rowCount, pagination stops."""
        page0 = [
            {"PostId": f"P{i:04d}", "PostName": f"E{i}", "WorkPlace": "北京"}
            for i in range(50)
        ]
        page1 = [
            {"PostId": f"P{i:04d}", "PostName": f"E{i}", "WorkPlace": "北京"}
            for i in range(50, 80)
        ]

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))
        client.add_post(_MockResponse(_list_response(page0, row_count=80)))
        client.add_post(_MockResponse(_list_response(page1, row_count=80)))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 80

    # -- Enrichment tests ------------------------------------------------------

    def test_detail_enrichment_adds_requirements(
        self, adapter: Any, context: Any
    ) -> None:
        """Collect enriches list items via detail API, filling requirements."""
        list_post = {
            "PostId": "QN5001",
            "PostName": "安全研究员",
            "WorkPlace": "北京",
            "Salary": "30k-50k",
        }
        detail_data = {
            "name": "安全研究员",
            "workPlace": "北京",
            "Salary": "30k-50k",
            "serviceCondition": "<p>要求：<br>安全研究经验</p>",
            "workConcet": "<p>职责：<br>漏洞研究</p>",
        }

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))  # bootstrap
        client.add_get(_MockResponse({"code": "00", "data": detail_data}))  # detail
        client.add_post(_MockResponse(_list_response([list_post])))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 1
        job = jobs[0]
        assert "漏洞研究" in job.description
        assert "安全研究经验" in job.description
        assert job.external_id == "QN5001"

    def test_detail_failure_preserves_list_facts(
        self, adapter: Any, context: Any
    ) -> None:
        """When detail enrichment fails, list-level fields are not discarded."""
        list_post = {
            "PostId": "QN6001",
            "PostName": "安全工程师（列表）",
            "WorkPlace": "深圳",
            "Salary": "15k-25k",
        }

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))  # bootstrap
        client.add_get(_MockResponse(None, status_code=500))  # detail fails with 5xx
        client.add_post(_MockResponse(_list_response([list_post])))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "安全工程师（列表）"
        assert job.location == "深圳"
        assert job.salary_disclosed is True
        assert job.external_id == "QN6001"

    def test_detail_skipped_when_already_enriched(
        self, adapter: Any, context: Any
    ) -> None:
        """Items with pre-existing detail fields skip the detail fetch."""
        enriched_post = {
            "PostId": "QN7001",
            "PostName": "安全专家",
            "WorkPlace": "北京",
            "Salary": "40k-60k",
            "serviceCondition": "<p>要求丰富经验</p>",
            "workConcet": "<p>领导安全团队</p>",
        }

        client = _MockClient()
        client.add_get(_MockResponse({"code": "00", "data": {}}))  # bootstrap
        client.add_post(_MockResponse(_list_response([enriched_post])))
        _pad_post_responses(client)

        with patch("findjobs.adapters.qianxin.httpx.Client", return_value=client):
            jobs = adapter.collect(context)

        assert len(jobs) == 1
        job = jobs[0]
        assert "领导安全团队" in job.description
        assert "要求丰富经验" in job.description
        assert job.external_id == "QN7001"
