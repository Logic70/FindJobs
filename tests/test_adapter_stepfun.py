"""Tests for StepFunMokaAdapter.

Covers: init-data/payload, real parsing, detail split, multi-location,
stable URL/identity, blank pagination/dedup, status filtering, salary
boundary, algorithm exclusion, system role inclusion, API validation,
retry transport/5xx and no-retry on 4xx.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "adapters"


def _load_fixture(name: str):
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _context(
    company: str = "stepfun",
    source: str = "stepfun_moka",
    base_url: str = "https://app.mokahr.com/campus-recruitment/step/141903",
    fetch_url: str = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
):
    from findjobs.adapters import AdapterContext

    return AdapterContext(
        company_slug=company,
        source_slug=source,
        base_url=base_url,
        fetch_url=fetch_url,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class TestStepFunMokaAdapter:
    """Tests for StepFunMokaAdapter parsing and collection."""

    @pytest.fixture
    def adapter(self):
        import findjobs.adapters.stepfun  # noqa: F401
        from findjobs.adapters import get_adapter

        return get_adapter("stepfun_moka")

    @pytest.fixture
    def context(self):
        return _context()

    @pytest.fixture
    def mock_bootstrap(self, monkeypatch):
        """Mock _request to prevent real HTTP during collect tests."""
        import findjobs.adapters.stepfun as sf
        import httpx
        from unittest.mock import MagicMock

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.text = "<html />"

        def mock_request(client, method, url, **kw):
            if method.upper() != "GET":
                raise RuntimeError(f"Unexpected HTTP request: {method} {url}")
            return mock_resp

        monkeypatch.setattr(sf, "_request", mock_request)

    # ------------------------------------------------------------------
    # Parse field coverage -- real shape
    # ------------------------------------------------------------------

    def test_parses_all_open_jobs(self, adapter, context):
        """7 open jobs, 1 closed job -> 7 parsed results."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        assert len(jobs) == 7

    def test_parses_external_id_and_url(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert job.external_id == "step-sys-01"
        assert job.url == (
            "https://app.mokahr.com/campus-recruitment/step/141903"
            "#/job/step-sys-01"
        )

    def test_parses_title(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert job.title == "高性能网络系统工程师"

    # ------------------------------------------------------------------
    # Detail: Chinese-bracket section conversion
    # ------------------------------------------------------------------

    def test_description_converts_gewuzhize_to_zhize(self, adapter, context):
        """【工作职责】is converted to 职责: after HTML stripping."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-02")
        assert "职责:" in job.description

    def test_description_converts_renyao_to_yaoqiu(self, adapter, context):
        """【任职要求】is converted to 要求: after HTML stripping."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-02")
        assert "要求:" in job.description

    def test_description_converts_gangweimiaoshu_to_zhize(self, adapter, context):
        """【岗位描述】is converted to 职责: after HTML stripping."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert "职责:" in job.description

    def test_description_converts_gangweiyaoqiu_to_yaoqiu(self, adapter, context):
        """【岗位要求】is converted to 要求: after HTML stripping."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert "要求:" in job.description

    def test_no_invented_sections(self, adapter, context):
        """Descriptions keep only the sections present in the source."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-03")
        # Has both 职责 and 要求 sections.
        assert "职责:" in job.description
        assert "要求:" in job.description
        # No spurious markers.
        assert "【" not in job.description

    # ------------------------------------------------------------------
    # Job type: commitment only, never zhineng
    # ------------------------------------------------------------------

    def test_job_type_is_commitment_only(self, adapter, context):
        """Canonical job_type uses the factual commitment field, not zhineng."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        # zhineng is "算法研究" but job_type must NOT contain "算法".
        assert job.job_type == "全职"
        assert "算法" not in job.job_type

    def test_job_type_all_six_open_jobs(self, adapter, context):
        """Every open job has commitment as job_type."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        for j in jobs:
            assert j.job_type in ("全职", "实习")

    # ------------------------------------------------------------------
    # Multi-location
    # ------------------------------------------------------------------

    def test_multi_location(self, adapter, context):
        """Locations are joined with the Moka formatter."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-multi-01")
        assert "北京市 海淀区" in job.location
        assert "浙江省 杭州市" in job.location
        assert " / " in job.location

    def test_single_location_no_delimiter(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert " / " not in job.location
        assert job.location == "北京市 海淀区"

    # ------------------------------------------------------------------
    # Salary boundary
    # ------------------------------------------------------------------

    def test_parses_salary_disclosed(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-02")
        assert job.salary_disclosed is True
        assert job.salary_min == 40000.0
        assert job.salary_max == 60000.0
        assert "40k-60k" in job.salary_text

    def test_parses_salary_undisclosed_when_null(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert job.salary_disclosed is False
        assert job.salary_min is None
        assert job.salary_max is None
        assert job.salary_text == ""

    def test_parses_salary_disclosed_when_provided(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-algo-01")
        assert job.salary_disclosed is True
        assert job.salary_min == 30000.0
        assert job.salary_max == 50000.0

    def test_parses_salary_from_salary_range(self, adapter, context):
        """Fallback to salaryRange when salaryText is null."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-slrng-01")
        assert job.salary_disclosed is True
        assert job.salary_min == 50000.0
        assert job.salary_max == 80000.0
        assert "50k-80k" in job.salary_text

    # ------------------------------------------------------------------
    # Published at
    # ------------------------------------------------------------------

    def test_parses_published_at_from_opened_at(self, adapter, context):
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert job.published_at is not None
        assert job.published_at.year == 2026
        assert job.published_at.month == 7
        assert job.published_at.day == 2

    # ------------------------------------------------------------------
    # Status filtering
    # ------------------------------------------------------------------

    def test_skips_closed_jobs(self, adapter, context):
        """Non-open jobs are excluded from parsed results."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        ext_ids = [j.external_id for j in jobs]
        assert "step-closed-01" not in ext_ids

    def test_keeps_open_jobs(self, adapter, context):
        """Open jobs are not discarded."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        ext_ids = [j.external_id for j in jobs]
        assert "step-algo-01" in ext_ids  # algorithm excluded by classifier, not adapter

    # ------------------------------------------------------------------
    # Algorithm exclusion / system role inclusion
    # ------------------------------------------------------------------

    def test_algorithm_title_is_excluded(self, adapter, context):
        """Jobs with '算法' in the title get empty matched_tags."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-algo-01")
        assert job.matched_tags == []

    def test_system_role_with_zhineng_suanfa_not_excluded(self, adapter, context):
        """System roles under zhineng='算法研究' appear and are not excluded.

        Because `job_type` is only the commitment value ('全职'), the
        classifier never sees '算法' in the surface, so these jobs are
        eligible for AI/Security tagging based on title + description.
        """
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        # All four verified system roles are present.
        ext_ids = [j.external_id for j in jobs]
        for sys_id in ("step-sys-01", "step-sys-02", "step-sys-03", "step-sys-04"):
            assert sys_id in ext_ids, f"{sys_id} should not be excluded"

    def test_system_role_networking_gets_ai_tag_from_description(self, adapter, context):
        """高性能网络系统工程师 gets AI tag from description signals."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert job.matched_tags == ["AI"]

    def test_system_role_training_framework_gets_ai_tag(self, adapter, context):
        """大模型训练框架系统工程师 gets AI tag from title signals."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-02")
        assert "AI" in job.matched_tags

    def test_system_role_inference_optimization_gets_ai_tag(self, adapter, context):
        """大模型推理优化系统工程师 gets AI tag from title signals."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-03")
        assert "AI" in job.matched_tags

    def test_system_role_post_training_gets_ai_tag(self, adapter, context):
        """大模型后训练系统工程师 gets AI tag from title signals."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-sys-04")
        assert "AI" in job.matched_tags

    def test_security_engineer_gets_security_tags(self, adapter, context):
        """安全研发工程师 gets Security tags."""
        jobs = adapter.parse(_load_fixture("stepfun.json"), context)
        job = next(j for j in jobs if j.external_id == "step-multi-01")
        assert "Security" in job.matched_tags

    # ------------------------------------------------------------------
    # API validation
    # ------------------------------------------------------------------

    def test_parse_raises_on_success_false(self, adapter, context):
        raw = {"code": 0, "success": False, "data": {"jobs": []}}
        import findjobs.adapters.stepfun as sf

        # parse() calls _items_from_raw → _validate_response
        with pytest.raises(ValueError, match="success=false"):
            adapter.parse(raw, context)

    def test_parse_raises_on_nonzero_code(self, adapter, context):
        raw = {"code": 1, "success": True, "data": {"jobs": []}}
        with pytest.raises(ValueError, match="non-zero code 1"):
            adapter.parse(raw, context)

    # ------------------------------------------------------------------
    # Request payload
    # ------------------------------------------------------------------

    def test_request_payload_uses_campus_site(self):
        import findjobs.adapters.stepfun as sf

        payload = sf._request_payload(
            org_id="step", site_id="141903", offset=0
        )
        assert payload["site"] == "campus"
        assert payload["orgId"] == "step"
        assert payload["siteId"] == "141903"
        assert "keyword" not in payload

    def test_request_payload_has_correct_offset(self):
        import findjobs.adapters.stepfun as sf

        payload = sf._request_payload(
            org_id="step", site_id="141903", offset=50
        )
        assert payload["offset"] == 50

    # ------------------------------------------------------------------
    # Init-data extraction
    # ------------------------------------------------------------------

    def test_extract_init_data(self):
        import findjobs.adapters.stepfun as sf

        # The regex in _extract_init_data uses `["\']` which would stop at
        # a bare `"` inside the value, so use &quot; HTML entities inside
        # and wrap the JSON in { }.
        html = (
            '<input id="init-data" type="hidden" value="'
            '{&quot;aesIv&quot;:&quot;testiv&quot;,'
            '&quot;org&quot;:{&quot;id&quot;:&quot;step&quot;,'
            '&quot;name&quot;:&quot;阶跃StepFun&quot;},'
            '&quot;siteId&quot;:&quot;141903&quot;}'
            '" />'
        )
        init = sf._extract_init_data(html)
        assert init["aesIv"] == "testiv"
        assert init["org"]["id"] == "step"
        assert init["siteId"] == "141903"

    # ------------------------------------------------------------------
    # Collect: pagination and dedup
    # ------------------------------------------------------------------

    def test_collect_paginates_using_total(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        monkeypatch.setattr(sf, "_PAGE_SIZE", 2)

        call_offsets: list[int] = []

        def mock_fetch_page(*, client, context, aes_iv, org_id, site_id, offset):
            call_offsets.append(offset)
            if offset == 0:
                return {
                    "code": 0,
                    "success": True,
                    "data": {
                        "jobStats": {"total": 4},
                        "jobs": [
                            {
                                "id": "p1-a",
                                "title": "岗位A",
                                "status": "open",
                                "commitment": "全职",
                                "locations": [{"provinceName": "北京市", "cityName": "海淀区"}],
                                "salaryText": None,
                                "jobDescription": "",
                                "openedAt": "2026-07-01T00:00",
                            },
                            {
                                "id": "p1-b",
                                "title": "岗位B",
                                "status": "open",
                                "commitment": "全职",
                                "locations": [{"provinceName": "上海市", "cityName": "浦东新区"}],
                                "salaryText": None,
                                "jobDescription": "",
                                "openedAt": "2026-07-01T00:00",
                            },
                        ],
                    },
                }
            return {
                "code": 0,
                "success": True,
                "data": {
                    "jobStats": {"total": 4},
                    "jobs": [
                        {
                            "id": "p2-c",
                            "title": "岗位C",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "广东省", "cityName": "广州市"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                        {
                            "id": "p2-d",
                            "title": "岗位D",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "广东省", "cityName": "深圳市"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                    ],
                },
            }

        monkeypatch.setattr(sf, "_fetch_page", mock_fetch_page)
        # Stub the bootstrap GET to return valid init-data HTML.
        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {
                "aesIv": "testiv",
                "org": {"id": "step", "name": "阶跃StepFun"},
                "siteId": "141903",
            },
        )

        jobs = adapter.collect(context)
        # PAGE_SIZE is 2, so offsets are 0, then 2.
        assert call_offsets == [0, 2]
        assert len(jobs) == 4
        ext_ids = [j.external_id for j in jobs]
        assert ext_ids == ["p1-a", "p1-b", "p2-c", "p2-d"]

    def test_collect_stops_on_short_page(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        call_count = [0]

        def mock_fetch_page(*, client, context, aes_iv, org_id, site_id, offset):
            call_count[0] += 1
            return {
                "code": 0,
                "success": True,
                "data": {
                    "jobStats": {"total": 1},
                    "jobs": [
                        {
                            "id": "only-1",
                            "title": "单一岗位",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "北京市", "cityName": "海淀区"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                    ],
                },
            }

        monkeypatch.setattr(sf, "_fetch_page", mock_fetch_page)
        monkeypatch.setattr(sf, "_PAGE_SIZE", 50)  # default > 1 item
        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {
                "aesIv": "testiv",
                "org": {"id": "step"},
                "siteId": "141903",
            },
        )

        adapter.collect(context)
        # Short page (1 < 50) should stop after first page.
        assert call_count[0] == 1

    def test_collect_deduplicates_by_id(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        def mock_fetch_page(*, client, context, aes_iv, org_id, site_id, offset):
            return {
                "code": 0,
                "success": True,
                "data": {
                    "jobStats": {"total": 3},
                    "jobs": [
                        {
                            "id": "dup-1",
                            "title": "重复岗位",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "北京市", "cityName": "海淀区"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                        {
                            "id": "unique-2",
                            "title": "唯一岗位A",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "北京市", "cityName": "海淀区"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                        {
                            "id": "dup-1",
                            "title": "重复岗位",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "北京市", "cityName": "海淀区"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                    ],
                },
            }

        monkeypatch.setattr(sf, "_fetch_page", mock_fetch_page)
        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {
                "aesIv": "testiv",
                "org": {"id": "step"},
                "siteId": "141903",
            },
        )

        jobs = adapter.collect(context)
        ext_ids = [j.external_id for j in jobs]
        assert ext_ids.count("dup-1") == 1
        assert "unique-2" in ext_ids

    def test_collect_deduplicates_by_title_location_fallback(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        def mock_fetch_page(*, client, context, aes_iv, org_id, site_id, offset):
            return {
                "code": 0,
                "success": True,
                "data": {
                    "jobStats": {"total": 2},
                    "jobs": [
                        {
                            "id": "",
                            "title": "通用岗位",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "上海市", "cityName": "浦东新区"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                        {
                            "id": "",
                            "title": "通用岗位",
                            "status": "open",
                            "commitment": "全职",
                            "locations": [{"provinceName": "上海市", "cityName": "浦东新区"}],
                            "salaryText": None,
                            "jobDescription": "",
                            "openedAt": "2026-07-01T00:00",
                        },
                    ],
                },
            }

        monkeypatch.setattr(sf, "_fetch_page", mock_fetch_page)
        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {
                "aesIv": "testiv",
                "org": {"id": "step"},
                "siteId": "141903",
            },
        )

        jobs = adapter.collect(context)
        assert len(jobs) == 1  # deduplicated by (title, location)

    # ------------------------------------------------------------------
    # fetch_url and base_url propagation
    # ------------------------------------------------------------------

    def test_fetch_url_used_in_collect(self, adapter, context, monkeypatch, mock_bootstrap):
        """Collect passes context.fetch_url to _fetch_page via payload URL."""
        import findjobs.adapters.stepfun as sf

        captured_urls: list[str] = []

        def mock_fetch_page(*, client, context, aes_iv, org_id, site_id, offset):
            captured_urls.append(context.fetch_url)
            return {
                "code": 0,
                "success": True,
                "data": {"jobStats": {"total": 0}, "jobs": []},
            }

        monkeypatch.setattr(sf, "_fetch_page", mock_fetch_page)
        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {
                "aesIv": "testiv",
                "org": {"id": "step"},
                "siteId": "141903",
            },
        )

        adapter.collect(context)
        assert len(captured_urls) >= 1
        assert captured_urls[0] == context.fetch_url

    # ------------------------------------------------------------------
    # Init-data validation
    # ------------------------------------------------------------------

    def test_collect_raises_on_missing_aes_iv(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {"aesIv": "", "org": {"id": "step"}, "siteId": "141903"},
        )

        with pytest.raises(ValueError, match="missing aesIv/orgId/siteId"):
            adapter.collect(context)

    def test_collect_raises_on_missing_org_id(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {"aesIv": "testiv", "org": {"id": ""}, "siteId": "141903"},
        )

        with pytest.raises(ValueError, match="missing aesIv/orgId/siteId"):
            adapter.collect(context)

    def test_collect_raises_on_missing_site_id(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {"aesIv": "testiv", "org": {"id": "step"}, "siteId": ""},
        )

        with pytest.raises(ValueError, match="missing aesIv/orgId/siteId"):
            adapter.collect(context)

    # ------------------------------------------------------------------
    # Code validation in collect
    # ------------------------------------------------------------------

    def test_collect_raises_on_nonzero_code(self, adapter, context, monkeypatch, mock_bootstrap):
        import findjobs.adapters.stepfun as sf

        def mock_fetch_page(*, client, context, aes_iv, org_id, site_id, offset):
            raise ValueError("StepFun Moka API returned non-zero code 500: ")

        monkeypatch.setattr(sf, "_fetch_page", mock_fetch_page)
        monkeypatch.setattr(
            sf,
            "_extract_init_data",
            lambda _: {
                "aesIv": "testiv",
                "org": {"id": "step"},
                "siteId": "141903",
            },
        )

        with pytest.raises(ValueError, match="non-zero code 500"):
            adapter.collect(context)

    # ------------------------------------------------------------------
    # Retry: _request retries transport/5xx, not 4xx
    # ------------------------------------------------------------------

    def test_request_post_retries_on_transport_error(self, monkeypatch):
        import findjobs.adapters.stepfun as sf
        import httpx
        import time
        from unittest.mock import MagicMock

        monkeypatch.setattr(time, "sleep", lambda s: None)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.TransportError("connection failed")

        with pytest.raises(httpx.TransportError):
            sf._request(mock_client, "POST", "http://fake", json_data={})

        assert mock_client.post.call_count == 3

    def test_request_post_retries_on_500(self, monkeypatch):
        import findjobs.adapters.stepfun as sf
        import httpx
        import time
        from unittest.mock import MagicMock

        monkeypatch.setattr(time, "sleep", lambda s: None)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = httpx.Response(
            500, request=httpx.Request("POST", "http://fake")
        )

        with pytest.raises(httpx.HTTPStatusError):
            sf._request(mock_client, "POST", "http://fake", json_data={})

        assert mock_client.post.call_count == 3

    def test_request_post_no_retry_on_400(self, monkeypatch):
        import findjobs.adapters.stepfun as sf
        import httpx
        import time
        from unittest.mock import MagicMock

        monkeypatch.setattr(time, "sleep", lambda s: None)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = httpx.Response(
            400, request=httpx.Request("POST", "http://fake")
        )

        with pytest.raises(httpx.HTTPStatusError):
            sf._request(mock_client, "POST", "http://fake", json_data={})

        assert mock_client.post.call_count == 1

    def test_request_get_retries_on_transport_error(self, monkeypatch):
        import findjobs.adapters.stepfun as sf
        import httpx
        import time
        from unittest.mock import MagicMock

        monkeypatch.setattr(time, "sleep", lambda s: None)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = httpx.TransportError("connection failed")

        with pytest.raises(httpx.TransportError):
            sf._request(mock_client, "GET", "http://fake")

        assert mock_client.get.call_count == 3

    def test_request_get_retries_on_500(self, monkeypatch):
        import findjobs.adapters.stepfun as sf
        import httpx
        import time
        from unittest.mock import MagicMock

        monkeypatch.setattr(time, "sleep", lambda s: None)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = httpx.Response(
            500, request=httpx.Request("GET", "http://fake")
        )

        with pytest.raises(httpx.HTTPStatusError):
            sf._request(mock_client, "GET", "http://fake")

        assert mock_client.get.call_count == 3

    def test_request_get_no_retry_on_400(self, monkeypatch):
        import findjobs.adapters.stepfun as sf
        import httpx
        import time
        from unittest.mock import MagicMock

        monkeypatch.setattr(time, "sleep", lambda s: None)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = httpx.Response(
            400, request=httpx.Request("GET", "http://fake")
        )

        with pytest.raises(httpx.HTTPStatusError):
            sf._request(mock_client, "GET", "http://fake")

        assert mock_client.get.call_count == 1

    def test_guard_unexpected_http_raises(self):
        """Guard test: _request raises when given a guarded client."""
        import findjobs.adapters.stepfun as sf
        import httpx
        from unittest.mock import MagicMock

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = RuntimeError("guard: HTTP blocked")

        with pytest.raises(RuntimeError, match="guard"):
            sf._request(mock_client, "GET", "http://blocked.example")

    # ------------------------------------------------------------------
    # _total_count through deepseek reuse
    # ------------------------------------------------------------------

    def test_job_total_extraction(self):
        import findjobs.adapters.deepseek as dsk

        raw = {
            "code": 0,
            "success": True,
            "data": {
                "jobStats": {"total": 19},
                "jobs": [],
            },
        }
        assert dsk._job_total(raw) == 19

    def test_job_total_none_when_missing(self):
        import findjobs.adapters.deepseek as dsk

        raw = {"code": 0, "success": True, "data": {}}
        assert dsk._job_total(raw) is None

    # ------------------------------------------------------------------
    # Base-url origin for detail URL
    # ------------------------------------------------------------------

    def test_parse_uses_base_url_for_detail_url(self, adapter):
        """Detail URL is built from context.base_url using the Moka hash pattern."""
        ctx = _context(
            base_url="https://custom.example.com/campus-recruitment/step/141903",
        )
        jobs = adapter.parse(_load_fixture("stepfun.json"), ctx)
        job = next(j for j in jobs if j.external_id == "step-sys-01")
        assert job.url == (
            "https://custom.example.com/campus-recruitment/step/141903"
            "#/job/step-sys-01"
        )

    # ------------------------------------------------------------------
    # Normalize sections edge cases
    # ------------------------------------------------------------------

    def test_normalize_sections_only_zhize(self):
        import findjobs.adapters.stepfun as sf

        text = "【岗位描述】\n工作内容\n【岗位要求】\n资格要求"
        result = sf._normalize_sections(text)
        assert "职责:" in result
        assert "要求:" in result

    def test_normalize_sections_no_markers(self):
        import findjobs.adapters.stepfun as sf

        text = "plain text without markers"
        result = sf._normalize_sections(text)
        assert result == text
