"""Phase 3 tests for newly verified official source adapters."""

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


class TestNewOfficialAdaptersRegistered:
    def test_resolves_new_dedicated_adapters(self):
        from findjobs.adapters import get_adapter

        for name in (
            "feishu_official",
            "jd_official",
            "meituan_official",
            "kuaishou_official",
            "deepseek_moka",
            "moka_official",
            "antgroup_official",
            "alibaba_group_official",
        ):
            assert get_adapter(name) is not None


class TestAntGroupOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("antgroup_official")

    @pytest.fixture
    def context(self):
        return _context(
            "antgroup",
            "antgroup-talent",
            base_url="https://talent.antgroup.com",
            fetch_url="https://hrcareersweb.antgroup.com/api/social/position/search",
        )

    def test_parses_jobs_with_requirements(self, adapter, context):
        jobs = adapter.parse(_load_fixture("antgroup.json"), context)

        assert len(jobs) == 3
        job = jobs[0]
        assert job.external_id == "25052604900001"
        assert job.title == "AI Security Platform Engineer"
        assert job.location == "Beijing\u3001Hangzhou"
        assert job.job_type == "R&D-Security"
        assert "\u804c\u8d23:" in job.description
        assert "\u8981\u6c42:" in job.description
        assert job.url.endswith("off-campus-position?positionId=25052604900001")
        assert job.salary_disclosed is False
        assert job.published_at.year == 2026
        assert job.matched_tags == ["AI", "Security", "AI Security"]

    def test_security_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("antgroup.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "25052604900002")

        assert algorithm_job.title == "\u5b89\u5168\u7b97\u6cd5\u4e13\u5bb6"
        assert algorithm_job.matched_tags == []

    def test_functional_risk_strategy_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("antgroup.json"), context)
        risk_job = next(j for j in jobs if j.external_id == "25052604900003")

        assert risk_job.title == "\u98ce\u63a7\u7b56\u7565\u4e13\u5bb6"
        assert risk_job.matched_tags == []

    def test_fetch_uses_verified_payload(self, adapter, context, monkeypatch):
        import httpx

        sent: dict[str, object] = {}

        def mock_post(url, **kwargs):
            sent["url"] = url
            sent["json"] = kwargs.get("json")
            sent["headers"] = kwargs.get("headers")
            return httpx.Response(
                200,
                json={"success": True, "content": [], "totalCount": 0},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        adapter.fetch(context)

        payload = sent["json"]
        assert str(sent["url"]).startswith(context.fetch_url)
        assert "ctoken=" in str(sent["url"])
        assert payload["channel"] == "group_official_site"
        assert payload["language"] == "zh"
        assert payload["pageIndex"] == 1
        assert payload["pageSize"] == 20
        assert payload["key"] == ""

    def test_collect_paginates_and_deduplicates(self, adapter, context, monkeypatch):
        from findjobs.adapters import antgroup as antgroup_module

        fixture = _load_fixture("antgroup.json")
        first = fixture["content"][0]
        second = fixture["content"][1]
        page_calls: list[int] = []

        def fake_fetch_page(page_no, ctx):
            page_calls.append(page_no)
            if page_no == 1:
                return {"success": True, "content": [first], "totalCount": 2}
            if page_no == 2:
                return {"success": True, "content": [first, second], "totalCount": 2}
            return {"success": True, "content": [], "totalCount": 2}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        monkeypatch.setattr(antgroup_module, "_PAGE_SIZE", 1)

        jobs = adapter.collect(context)

        assert page_calls == [1, 2]
        ids = [job.external_id for job in jobs]
        assert ids == ["25052604900001", "25052604900002"]


class TestAlibabaGroupOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("alibaba_group_official")

    @pytest.fixture
    def context(self):
        return _context(
            "alibaba",
            "alibaba-aliyun-careers",
            base_url="https://careers.aliyun.com",
            fetch_url="https://careers.aliyun.com/position/search",
        )

    def test_parses_jobs_with_requirements(self, adapter, context):
        jobs = adapter.parse(_load_fixture("alibaba_group.json"), context)

        assert len(jobs) == 4
        job = jobs[0]
        assert job.external_id == "100015383016"
        assert job.title == "\u963f\u91cc\u4e91\u667a\u80fd-AI Agent\u5e73\u53f0\u5de5\u7a0b\u5e08-\u5317\u4eac"
        assert job.location == "\u5317\u4eac"
        assert job.job_type == "\u7814\u53d1"
        assert "\u804c\u8d23:" in job.description
        assert "\u8981\u6c42:" in job.description
        assert job.url.startswith("https://careers.aliyun.com/off-campus/position-detail")
        assert job.salary_disclosed is False
        assert job.published_at.year == 2026
        assert "AI" in job.matched_tags

    def test_cloud_security_role_keeps_security(self, adapter, context):
        jobs = adapter.parse(_load_fixture("alibaba_group.json"), context)
        security_job = next(j for j in jobs if j.external_id == "100015383017")

        assert security_job.location == "Hangzhou\u3001Shanghai"
        assert "Security" in security_job.matched_tags

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("alibaba_group.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "100015383018")

        assert algorithm_job.title.startswith("\u901a\u4e49-\u7b97\u6cd5")
        assert algorithm_job.matched_tags == []

    def test_functional_asset_risk_role_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("alibaba_group.json"), context)
        risk_job = next(j for j in jobs if j.external_id == "100015383019")

        assert "\u98ce\u63a7\u7ba1\u7406" in risk_job.title
        assert risk_job.matched_tags == []

    def test_fetch_uses_xsrf_cookie_from_list_page(self, adapter, context, monkeypatch):
        import httpx

        calls: list[tuple[str, str]] = []

        class FakeCookies(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.cookies = FakeCookies()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get(self, url, **kwargs):
                calls.append(("GET", url))
                self.cookies["XSRF-TOKEN"] = "csrf-token"
                return httpx.Response(
                    200,
                    text="<html></html>",
                    request=httpx.Request("GET", url),
                )

            def post(self, url, **kwargs):
                calls.append(("POST", url))
                payload = kwargs.get("json")
                assert payload["channel"] == "group_official_site"
                assert payload["language"] == "zh"
                assert payload["pageIndex"] == 1
                return httpx.Response(
                    200,
                    json={"success": True, "content": {"datas": [], "totalCount": 0}},
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "Client", FakeClient)

        adapter.fetch(context)

        assert calls[0] == (
            "GET",
            "https://careers.aliyun.com/off-campus/position-list?lang=zh",
        )
        assert calls[1][0] == "POST"
        assert calls[1][1].endswith("/position/search?_csrf=csrf-token")

    def test_collect_paginates_and_deduplicates(self, adapter, context, monkeypatch):
        from findjobs.adapters import alibaba_group as alibaba_group_module

        fixture = _load_fixture("alibaba_group.json")
        first = fixture["content"]["datas"][0]
        second = fixture["content"]["datas"][1]
        page_calls: list[int] = []

        def fake_csrf(client, ctx):
            return "csrf-token"

        def fake_fetch_page(client, *, context, csrf_token, page_no, keyword=""):
            page_calls.append(page_no)
            if page_no == 1:
                return {
                    "success": True,
                    "content": {"datas": [first], "totalCount": 2},
                }
            if page_no == 2:
                return {
                    "success": True,
                    "content": {"datas": [first, second], "totalCount": 2},
                }
            return {"success": True, "content": {"datas": [], "totalCount": 2}}

        monkeypatch.setattr(adapter, "_fetch_csrf_token", fake_csrf)
        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        monkeypatch.setattr(alibaba_group_module, "_PAGE_SIZE", 1)

        jobs = adapter.collect(context)

        assert page_calls == [1, 2]
        ids = [job.external_id for job in jobs]
        assert ids == ["100015383016", "100015383017"]

    def test_collect_supplements_blank_cap_with_target_keywords(
        self, adapter, context, monkeypatch
    ):
        from findjobs.adapters import alibaba_group as alibaba_group_module

        fixture = _load_fixture("alibaba_group.json")
        first = fixture["content"]["datas"][0]
        second = fixture["content"]["datas"][1]
        third = fixture["content"]["datas"][2]
        page_calls: list[tuple[str, int]] = []

        def fake_csrf(client, ctx):
            return "csrf-token"

        def fake_fetch_page(client, *, context, csrf_token, page_no, keyword=""):
            page_calls.append((keyword, page_no))
            if keyword == "" and page_no == 1:
                return {
                    "success": True,
                    "content": {"datas": [first], "totalCount": 3},
                }
            if keyword == "":
                return {
                    "success": True,
                    "content": {"datas": [], "totalCount": 3},
                }
            if keyword == "AI" and page_no == 1:
                return {
                    "success": True,
                    "content": {"datas": [first, second], "totalCount": 2},
                }
            if keyword == "\u5b89\u5168" and page_no == 1:
                return {
                    "success": True,
                    "content": {"datas": [third], "totalCount": 1},
                }
            return {
                "success": True,
                "content": {"datas": [], "totalCount": 0},
            }

        monkeypatch.setattr(adapter, "_fetch_csrf_token", fake_csrf)
        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        monkeypatch.setattr(alibaba_group_module, "_PAGE_SIZE", 1)

        jobs = adapter.collect(context)

        ids = [job.external_id for job in jobs]
        assert ids == ["100015383016", "100015383017", "100015383018"]
        assert ("", 1) in page_calls
        assert ("AI", 1) in page_calls
        assert ("\u5b89\u5168", 1) in page_calls


class TestFeishuOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("feishu_official")

    @pytest.fixture
    def context(self):
        return _context(
            "01ai",
            "01ai-feishu",
            base_url="https://01ai.jobs.feishu.cn",
        )

    def test_parses_list_response_with_requirements(self, adapter, context):
        jobs = adapter.parse(_load_fixture("feishu.json"), context)
        assert len(jobs) == 2
        assert jobs[0].external_id == "7650000000000000001"
        assert jobs[0].title == "AI\u5168\u6808\u5f00\u53d1\u5de5\u7a0b\u5e08"
        assert jobs[0].location == "\u5317\u4eac\u3001\u676d\u5dde"
        assert jobs[0].job_type == "\u7814\u53d1"
        assert "\u804c\u8d23:" in jobs[0].description
        assert "\u8981\u6c42:" in jobs[0].description
        assert jobs[0].url.endswith(
            "/index/position/7650000000000000001/detail"
        )
        assert jobs[0].salary_disclosed is False
        assert "AI" in jobs[0].matched_tags

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("feishu.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "7650000000000000002")
        assert algorithm_job.matched_tags == []

    def test_website_info_extracts_path(self):
        from findjobs.adapters.feishu import _website_info, _website_path

        html = (
            '<script id="js-websiteInfo" type="text/json">'
            '{"website_info":{"path":"career"}}</script>'
        )
        assert _website_path(_website_info(html)) == "career"

    def test_fetch_page_signs_request_and_retries_after_csrf(
        self, adapter, context, monkeypatch
    ):
        import httpx
        from findjobs.adapters import feishu

        signed_requests: list[dict[str, object]] = []
        calls: list[tuple[str, str, str]] = []

        def fake_sign_many(base_url, requests):
            signed_requests.extend(requests)
            return ["signed-token"]

        class FakeClient:
            def post(self, url, **kwargs):
                headers = kwargs.get("headers", {})
                calls.append(("POST", url, headers.get("x-csrf-token", "")))
                request = httpx.Request("POST", url)
                if url.endswith("/api/v1/csrf/token"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={
                            "code": 0,
                            "data": {"token": "csrf-token"},
                            "message": "ok",
                        },
                    )
                if not headers.get("x-csrf-token"):
                    return httpx.Response(405, request=request)
                return httpx.Response(
                    200,
                    request=request,
                    json={"code": 0, "data": {"job_post_list": [], "count": 0}},
                )

        monkeypatch.setattr(feishu, "_sign_many", fake_sign_many)
        raw = adapter._fetch_page(
            FakeClient(),
            base_url=context.base_url,
            website_path="index",
            keyword="",
            offset=0,
        )

        assert raw["code"] == 0
        assert signed_requests[0]["url"].startswith(
            "/api/v1/search/job/posts?keyword=&limit="
        )
        assert "_signature=signed-token" in calls[0][1]
        assert calls[-1][2] == "csrf-token"

    def test_collect_paginates_until_total(self, adapter, context, monkeypatch):
        pages: list[int] = []
        fixture = _load_fixture("feishu.json")

        def fake_fetch_site(client, context):
            return context.base_url, "index"

        def fake_fetch_csrf(client, base_url, website_path):
            return "csrf-token"

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            pages.append(offset)
            return fixture if offset == 0 else {
                "code": 0,
                "data": {"job_post_list": [], "count": 2},
            }

        monkeypatch.setattr(adapter, "_fetch_site", fake_fetch_site)
        monkeypatch.setattr(adapter, "_fetch_csrf_token", fake_fetch_csrf)
        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)

        jobs = adapter.collect(context)

        assert pages == [0]
        assert len(jobs) == 2


class TestJDOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("jd_official")

    @pytest.fixture
    def context(self):
        return _context(
            "jd",
            "jd-zhaopin",
            base_url="https://zhaopin.jd.com",
            fetch_url="https://zhaopin.jd.com/web/job/job_list",
        )

    def test_parses_list_response_with_requirements(self, adapter, context):
        jobs = adapter.parse(_load_fixture("jd.json"), context)
        assert len(jobs) == 2
        assert jobs[0].external_id == "219422"
        assert jobs[0].title == "\u5b89\u5168\u8fd0\u8425\u4e13\u5bb6"
        assert jobs[0].location == "\u5317\u4eac\u5e02"
        assert jobs[0].job_type == "\u7814\u53d1\u7c7b"
        assert "\u804c\u8d23:" in jobs[0].description
        assert "\u8981\u6c42:" in jobs[0].description
        assert jobs[0].url.endswith("requementId=219422")
        assert jobs[0].salary_disclosed is False
        assert jobs[0].published_at.year == 2026

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("jd.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "219423")
        assert algorithm_job.matched_tags == []

    def test_fetch_uses_verified_form_payload(self, adapter, context, monkeypatch):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        sent: dict[str, object] = {}

        def mock_post(url, **kwargs):
            sent["url"] = url
            sent["data"] = kwargs.get("data")
            sent["headers"] = kwargs.get("headers")
            return httpx.Response(200, json=[])

        monkeypatch.setattr(httpx, "post", mock_post)
        adapter.fetch(context)

        data = sent["data"]
        headers = sent["headers"]
        assert sent["url"] == "https://zhaopin.jd.com/web/job/job_list"
        assert data["pageIndex"] == "1"
        assert data["pageSize"] == "10"
        assert data["jobSearch"] == TARGET_KEYWORDS[0]
        assert headers["X-Requested-With"] == "XMLHttpRequest"

    def test_collect_uses_count_endpoint_and_stops_at_total(
        self, adapter, context, monkeypatch
    ):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        fixture = _load_fixture("jd.json")
        count_calls: dict[str, int] = {}
        page_calls: list[dict] = []

        def mock_post(url, **kwargs):
            data = kwargs.get("data", {})
            kw = data.get("jobSearch", "")

            if url.endswith("job_count"):
                count_calls[kw] = count_calls.get(kw, 0) + 1
                if kw == TARGET_KEYWORDS[0]:
                    return httpx.Response(200, json=1)
                return httpx.Response(200, json=0)

            page = data.get("pageIndex")
            page_calls.append({"page": page, "keyword": kw})

            if kw == TARGET_KEYWORDS[0] and page == "1":
                return httpx.Response(200, json=[fixture[0]])
            return httpx.Response(200, json=[])

        monkeypatch.setattr(httpx, "post", mock_post)
        jobs = adapter.collect(context)

        assert TARGET_KEYWORDS[0] in count_calls
        assert len(jobs) == 1


    def test_collect_uses_multiple_keywords(
        self, adapter, context, monkeypatch
    ):
        """JD collect() iterates over more than one target keyword."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        seen_keywords: set[str] = set()

        def mock_post(url, **kwargs):
            kw = kwargs.get("data", {}).get("jobSearch", "")
            if url.endswith("job_count"):
                return httpx.Response(200, json=0)
            seen_keywords.add(kw)
            return httpx.Response(200, json=[])

        monkeypatch.setattr(httpx, "post", mock_post)
        adapter.collect(context)

        assert len(seen_keywords) >= 2
        assert TARGET_KEYWORDS[0] in seen_keywords
        assert TARGET_KEYWORDS[1] in seen_keywords

    def test_collect_deduplicates_duplicate_ids(
        self, adapter, context, monkeypatch
    ):
        """JD collect() deduplicates items with the same external_id across keywords."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        def mock_post(url, **kwargs):
            data = kwargs.get("data", {})
            kw = data.get("jobSearch", "")

            if url.endswith("job_count"):
                return httpx.Response(200, json=2)

            page = data.get("pageIndex")
            if kw == TARGET_KEYWORDS[0] and page == "1":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "requirementId": "dup001",
                            "positionNameOpen": "重复岗位",
                            "workCity": "北京",
                        },
                        {
                            "requirementId": "jd-a",
                            "positionNameOpen": "唯一岗位A",
                            "workCity": "上海",
                        },
                    ],
                )
            if kw == TARGET_KEYWORDS[1] and page == "1":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "requirementId": "dup001",
                            "positionNameOpen": "重复岗位",
                            "workCity": "北京",
                        },
                        {
                            "requirementId": "jd-b",
                            "positionNameOpen": "唯一岗位B",
                            "workCity": "杭州",
                        },
                    ],
                )
            return httpx.Response(200, json=[])

        monkeypatch.setattr(httpx, "post", mock_post)
        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("dup001") == 1
        assert "jd-a" in ids
        assert "jd-b" in ids
        assert len(jobs) == 3


class TestMeituanOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("meituan_official")

    @pytest.fixture
    def context(self):
        return _context(
            "meituan",
            "meituan-zhaopin",
            base_url="https://zhaopin.meituan.com",
            fetch_url="https://zhaopin.meituan.com/api/official/job/getJobList",
        )

    def test_parses_job_list_with_requirements(self, adapter, context):
        jobs = adapter.parse(_load_fixture("meituan.json"), context)
        assert len(jobs) == 2
        assert jobs[0].external_id == "4158839535"
        assert jobs[0].title == "\u9a91\u624b\u5b89\u5168\u57f9\u8bad\u4e13\u5bb6"
        assert jobs[0].location == "\u5317\u4eac\u5e02"
        assert jobs[0].job_type == "\u516c\u53f8\u4e8b\u52a1/\u804c\u80fd\u7c7b"
        assert "\u804c\u8d23:" in jobs[0].description
        assert "\u8981\u6c42:" in jobs[0].description
        assert "\u4eae\u70b9:" in jobs[0].description
        assert jobs[0].salary_disclosed is False

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("meituan.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "4158839536")
        assert algorithm_job.matched_tags == []

    def test_fetch_uses_verified_json_payload(self, adapter, context, monkeypatch):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        sent: dict[str, object] = {}

        def mock_post(url, **kwargs):
            sent["url"] = url
            sent["json"] = kwargs.get("json")
            sent["headers"] = kwargs.get("headers")
            return httpx.Response(
                200,
                json={"status": 1, "data": {"list": [], "page": {"totalPage": 0}}},
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        adapter.fetch(context)

        payload = sent["json"]
        headers = sent["headers"]
        assert payload["keywords"] == TARGET_KEYWORDS[0]
        assert payload["page"]["pageNo"] == 1
        assert payload["page"]["pageSize"] == 50
        assert "zhaopin.meituan.com" in headers["Referer"]

    def test_collect_paginates_until_total_page(self, adapter, context, monkeypatch):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        fixture = _load_fixture("meituan.json")
        first = fixture["data"]["list"][0]
        second = fixture["data"]["list"][1]
        requested_pages: list[tuple[int, str]] = []

        def mock_post(url, **kwargs):
            if url.endswith("getJobDetail"):
                job_union_id = kwargs.get("json", {}).get("jobUnionId")
                detail = dict(first if job_union_id == first["jobUnionId"] else second)
                return httpx.Response(200, json={"status": 1, "data": detail})
            json_payload = kwargs.get("json", {})
            kw = json_payload.get("keywords", "")
            page_no = json_payload.get("page", {}).get("pageNo")
            requested_pages.append((page_no, kw))

            if kw != TARGET_KEYWORDS[0]:
                return httpx.Response(
                    200,
                    json={
                        "status": 1,
                        "data": {"list": [], "page": {"totalPage": 0}},
                    },
                )

            if page_no == 1:
                payload = fixture
                payload["data"]["page"]["totalPage"] = 2
                payload["data"]["list"] = [first]
            elif page_no == 2:
                payload = {
                    "status": 1,
                    "data": {
                        "list": [second],
                        "page": {"pageNo": 2, "pageSize": 50, "totalPage": 2},
                    },
                }
            else:
                payload = {"status": 1, "data": {"list": []}}
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(httpx, "post", mock_post)
        jobs = adapter.collect(context)

        kw0_pages = [p for p, kw in requested_pages if kw == TARGET_KEYWORDS[0]]
        assert kw0_pages == [1, 2]
        other_kw_calls = [(p, kw) for p, kw in requested_pages if kw != TARGET_KEYWORDS[0]]
        assert len(other_kw_calls) >= 1
        assert len(jobs) == 2

    def test_collect_fetches_detail_to_fill_requirements(
        self, adapter, context, monkeypatch
    ):
        import httpx

        list_item = {
            "jobUnionId": "detail-001",
            "name": "安全大模型技术专家",
            "jobFamily": "研发类",
            "cityList": [{"name": "北京市"}],
            "jobDuty": "负责安全大模型平台建设。",
            "jobRequirement": None,
            "highLight": "AI安全场景",
        }
        detail_item = {
            **list_item,
            "jobRequirement": "具备安全工程、大模型应用或MLOps经验。",
        }

        def mock_post(url, **kwargs):
            if url.endswith("getJobDetail"):
                assert kwargs.get("json") == {"jobUnionId": "detail-001"}
                return httpx.Response(200, json={"status": 1, "data": detail_item})
            return httpx.Response(
                200,
                json={
                    "status": 1,
                    "data": {
                        "list": [list_item],
                        "page": {"pageNo": 1, "pageSize": 50, "totalPage": 1},
                    },
                },
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        jobs = adapter.collect(context)

        assert len(jobs) == 1
        assert "职责:" in jobs[0].description
        assert "要求:" in jobs[0].description
        assert "MLOps" in jobs[0].description

    def test_detail_retries_on_transport_error_then_succeeds(
        self, adapter, context, monkeypatch
    ):
        import httpx

        from findjobs.adapters import meituan as meituan_module

        call_count = 0

        monkeypatch.setattr(meituan_module.time, "sleep", lambda _: None)

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if url.endswith("getJobDetail"):
                if call_count == 1:
                    raise httpx.TransportError(
                        "SSL: UNEXPECTED_EOF_WHILE_READING"
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": 1,
                        "data": {
                            "jobUnionId": "retry-001",
                            "jobDuty": "负责安全工作",
                            "jobRequirement": "After retry requirement.",
                        },
                    },
                )
            # List page calls always succeed
            return httpx.Response(
                200,
                json={
                    "status": 1,
                    "data": {"list": [], "page": {"totalPage": 0}},
                },
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        result = adapter._fetch_detail(context, "retry-001")

        assert result is not None
        assert result.get("jobRequirement") == "After retry requirement."
        assert call_count == 2  # first fails, second succeeds

    def test_detail_returns_none_after_all_transport_retries_exhausted(
        self, adapter, context, monkeypatch
    ):
        import httpx

        from findjobs.adapters import meituan as meituan_module

        call_count = 0

        monkeypatch.setattr(meituan_module.time, "sleep", lambda _: None)

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TransportError("SSL: UNEXPECTED_EOF_WHILE_READING")

        monkeypatch.setattr(httpx, "post", mock_post)
        result = adapter._fetch_detail(context, "fail-001")

        assert result is None
        assert call_count == 3  # initial + 2 retries

    def test_collect_keeps_list_item_when_detail_retries_exhausted(
        self, adapter, context, monkeypatch
    ):
        import httpx

        from findjobs.adapters import meituan as meituan_module

        detail_calls = 0
        list_item = {
            "jobUnionId": "fallback-001",
            "name": "AI Security Platform Engineer",
            "jobFamily": "R&D",
            "cityList": [{"name": "Beijing"}],
            "jobDuty": "Build AI security platform and LLM risk controls.",
            "jobRequirement": None,
            "firstPostTime": 1781971200000,
        }

        monkeypatch.setattr(meituan_module.time, "sleep", lambda _: None)

        def mock_post(url, **kwargs):
            nonlocal detail_calls
            if url.endswith("getJobDetail"):
                detail_calls += 1
                raise httpx.TransportError("SSL: UNEXPECTED_EOF_WHILE_READING")
            return httpx.Response(
                200,
                json={
                    "status": 1,
                    "data": {
                        "list": [list_item],
                        "page": {"pageNo": 1, "pageSize": 50, "totalPage": 1},
                    },
                },
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        jobs = adapter.collect(context)

        assert len(jobs) == 1
        assert jobs[0].external_id == "fallback-001"
        assert "职责:" in jobs[0].description
        assert "要求:" not in jobs[0].description
        assert detail_calls == 3


    def test_collect_uses_multiple_keywords(
        self, adapter, context, monkeypatch
    ):
        """Meituan collect() iterates over more than one target keyword."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        seen_keywords: set[str] = set()

        def mock_post(url, **kwargs):
            if url.endswith("getJobDetail"):
                return httpx.Response(
                    200,
                    json={"status": 1, "data": {"jobUnionId": "none"}},
                )
            kw = kwargs.get("json", {}).get("keywords", "")
            seen_keywords.add(kw)
            return httpx.Response(
                200,
                json={
                    "status": 1,
                    "data": {"list": [], "page": {"totalPage": 0}},
                },
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        adapter.collect(context)

        assert len(seen_keywords) >= 2
        assert TARGET_KEYWORDS[0] in seen_keywords
        assert TARGET_KEYWORDS[1] in seen_keywords

    def test_collect_deduplicates_duplicate_ids(
        self, adapter, context, monkeypatch
    ):
        """Meituan collect() deduplicates items with the same jobUnionId across keywords."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        def mock_post(url, **kwargs):
            if url.endswith("getJobDetail"):
                job_union_id = kwargs.get("json", {}).get("jobUnionId")
                if job_union_id == "dup001":
                    return httpx.Response(
                        200,
                        json={
                            "status": 1,
                            "data": {
                                "jobUnionId": "dup001",
                                "name": "重复岗位",
                                "jobDuty": "重复岗位职责",
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": 1,
                        "data": {
                            "jobUnionId": job_union_id,
                            "name": f"岗位{job_union_id}",
                            "jobDuty": "岗位职责",
                        },
                    },
                )

            kw = kwargs.get("json", {}).get("keywords", "")
            page_no = kwargs.get("json", {}).get("page", {}).get("pageNo")
            if kw == TARGET_KEYWORDS[0] and page_no == 1:
                return httpx.Response(
                    200,
                    json={
                        "status": 1,
                        "data": {
                            "list": [
                                {
                                    "jobUnionId": "dup001",
                                    "name": "重复岗位",
                                    "cityList": [{"name": "北京"}],
                                },
                                {
                                    "jobUnionId": "mt-a",
                                    "name": "唯一岗位A",
                                    "cityList": [{"name": "上海"}],
                                },
                            ],
                            "page": {"totalPage": 1},
                        },
                    },
                )
            if kw == TARGET_KEYWORDS[1] and page_no == 1:
                return httpx.Response(
                    200,
                    json={
                        "status": 1,
                        "data": {
                            "list": [
                                {
                                    "jobUnionId": "dup001",
                                    "name": "重复岗位",
                                    "cityList": [{"name": "北京"}],
                                },
                                {
                                    "jobUnionId": "mt-b",
                                    "name": "唯一岗位B",
                                    "cityList": [{"name": "杭州"}],
                                },
                            ],
                            "page": {"totalPage": 1},
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": 1,
                    "data": {"list": [], "page": {"totalPage": 0}},
                },
            )

        monkeypatch.setattr(httpx, "post", mock_post)
        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("dup001") == 1
        assert "mt-a" in ids
        assert "mt-b" in ids
        assert len(jobs) == 3


class TestKuaishouOfficialAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("kuaishou_official")

    @pytest.fixture
    def context(self):
        return _context(
            "kuaishou",
            "kuaishou-zhaopin",
            base_url="https://zhaopin.kuaishou.cn",
            fetch_url=(
                "https://zhaopin.kuaishou.cn/recruit/e/api/v1/open/"
                "positions/simple"
            ),
        )

    def test_parses_signed_api_response(self, adapter, context):
        jobs = adapter.parse(_load_fixture("kuaishou.json"), context)
        assert len(jobs) == 2
        assert jobs[0].external_id == "18870"
        assert jobs[0].title.startswith("\u673a\u5668\u5b66\u4e60")
        assert jobs[0].location == "Beijing"
        assert jobs[0].job_type == "J0011"
        assert "\u804c\u8d23:" in jobs[0].description
        assert "\u8981\u6c42:" in jobs[0].description
        assert jobs[0].salary_disclosed is False

    def test_algorithm_title_is_excluded(self, adapter, context):
        jobs = adapter.parse(_load_fixture("kuaishou.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "18870")
        assert algorithm_job.matched_tags == []

    def test_ai_security_platform_gets_compound_tags(self, adapter, context):
        jobs = adapter.parse(_load_fixture("kuaishou.json"), context)
        ai_job = next(j for j in jobs if j.external_id == "18871")
        assert ai_job.matched_tags == ["AI", "Security", "AI Security"]

    def test_fetch_adds_signature_headers(self, adapter, context, monkeypatch):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        sent: dict[str, object] = {}

        def mock_get(url, **kwargs):
            sent["url"] = url
            sent["params"] = kwargs.get("params")
            sent["headers"] = kwargs.get("headers")
            return httpx.Response(
                200,
                json={"code": 0, "result": {"total": 0, "list": []}},
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        adapter.fetch(context)

        params = sent["params"]
        headers = sent["headers"]
        assert params["name"] == TARGET_KEYWORDS[0]
        assert params["positionNatureCode"] == "C001"
        assert "sign" in headers
        assert "signTimestamp" in headers
        assert len(headers["sign"]) == 64

    def test_collect_paginates_until_total(self, adapter, context, monkeypatch):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        fixture = _load_fixture("kuaishou.json")
        requested_pages: list[tuple[int, str]] = []

        def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            page_no = params.get("pageNum")
            kw = params.get("name", "")
            requested_pages.append((page_no, kw))

            if kw != TARGET_KEYWORDS[0]:
                return httpx.Response(
                    200,
                    json={"code": 0, "result": {"total": 0, "list": []}},
                )

            if page_no == 1:
                payload = {
                    "code": 0,
                    "result": {"total": 2, "list": [fixture["result"]["list"][0]]},
                }
            elif page_no == 2:
                payload = {
                    "code": 0,
                    "result": {"total": 2, "list": [fixture["result"]["list"][1]]},
                }
            else:
                payload = {"code": 0, "result": {"total": 2, "list": []}}
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        kw0_pages = [p for p, kw in requested_pages if kw == TARGET_KEYWORDS[0]]
        assert kw0_pages == [1, 2]
        other_kw_calls = [(p, kw) for p, kw in requested_pages if kw != TARGET_KEYWORDS[0]]
        assert len(other_kw_calls) >= 1
        assert len(jobs) == 2


    def test_collect_uses_multiple_keywords(
        self, adapter, context, monkeypatch
    ):
        """Kuaishou collect() iterates over more than one target keyword."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        seen_keywords: set[str] = set()

        def mock_get(url, **kwargs):
            kw = kwargs.get("params", {}).get("name", "")
            seen_keywords.add(kw)
            return httpx.Response(
                200,
                json={"code": 0, "result": {"total": 0, "list": []}},
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        adapter.collect(context)

        assert len(seen_keywords) >= 2
        assert TARGET_KEYWORDS[0] in seen_keywords
        assert TARGET_KEYWORDS[1] in seen_keywords

    def test_collect_deduplicates_duplicate_ids(
        self, adapter, context, monkeypatch
    ):
        """Kuaishou collect() deduplicates items with the same id across keywords."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        def mock_get(url, **kwargs):
            params = kwargs.get("params", {})
            kw = params.get("name", "")
            page_no = params.get("pageNum")

            if kw == TARGET_KEYWORDS[0] and page_no == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "result": {
                            "total": 2,
                            "list": [
                                {
                                    "id": 1001,
                                    "name": "重复岗位",
                                    "workLocationCode": "北京",
                                },
                                {
                                    "id": 2001,
                                    "name": "唯一岗位A",
                                    "workLocationCode": "上海",
                                },
                            ],
                        },
                    },
                )
            if kw == TARGET_KEYWORDS[1] and page_no == 1:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "result": {
                            "total": 2,
                            "list": [
                                {
                                    "id": 1001,
                                    "name": "重复岗位",
                                    "workLocationCode": "北京",
                                },
                                {
                                    "id": 3001,
                                    "name": "唯一岗位B",
                                    "workLocationCode": "杭州",
                                },
                            ],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={"code": 0, "result": {"total": 0, "list": []}},
            )

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("1001") == 1
        assert "2001" in ids
        assert "3001" in ids
        assert len(jobs) == 3


class TestDeepSeekMokaAdapter:
    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("deepseek_moka")

    @pytest.fixture
    def context(self):
        return _context(
            "deepseek",
            "deepseek-careers",
            base_url=(
                "https://app.mokahr.com/social-recruitment/high-flyer/140576"
                "?orgId=high-flyer"
            ),
            fetch_url="https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
        )

    def test_parses_moka_jobs_with_description_and_requirements(
        self, adapter, context
    ):
        jobs = adapter.parse(_load_fixture("deepseek.json"), context)
        assert len(jobs) == 2
        job = jobs[0]
        assert job.external_id == "ds-ai-platform"
        assert job.title == "AI\u5e73\u53f0\u8fd0\u7ef4\u5de5\u7a0b\u5e08"
        assert "\u3010\u5de5\u4f5c\u804c\u8d23\u3011" in job.description
        assert "\u3010\u5c97\u4f4d\u8981\u6c42\u3011" in job.description
        assert "MLOps" in job.description
        assert "\u5317\u4eac\u5e02 \u6d77\u6dc0\u533a" in job.location
        assert "\u6d59\u6c5f \u62f1\u5885\u533a" in job.location
        assert job.job_type == "AI\u5e73\u53f0\u5de5\u7a0b"
        assert job.url.endswith("#/job/ds-ai-platform")
        assert job.salary_disclosed is False
        assert job.published_at.year == 2026

    def test_algorithm_title_keeps_deepseek_job_out_of_ai_tags(
        self, adapter, context
    ):
        jobs = adapter.parse(_load_fixture("deepseek.json"), context)
        algorithm_job = next(j for j in jobs if j.external_id == "ds-ai-search-algo")
        assert "AI" not in algorithm_job.matched_tags
        assert "AI Security" not in algorithm_job.matched_tags

    def test_deepseek_keywords_are_shared_plus_agi_only(self):
        from findjobs.adapters import deepseek
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        assert deepseek._KEYWORDS == TARGET_KEYWORDS + ["AGI"]
        assert "算法" not in deepseek._KEYWORDS
        assert "平台" not in deepseek._KEYWORDS

    def test_collect_merges_keyword_pages_and_deduplicates(
        self, adapter, context, monkeypatch
    ):
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        fixture = _load_fixture("deepseek.json")
        first = fixture["data"]["jobs"][0]
        duplicate = dict(first)
        duplicate["title"] = first["title"]
        post_calls: list[dict] = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get(self, url, **kwargs):
                html = (
                    '<input id="init-data" type="hidden" value="'
                    '{&quot;siteId&quot;:&quot;140576&quot;,'
                    '&quot;aesIv&quot;:&quot;fixture-iv&quot;,'
                    '&quot;org&quot;:{&quot;id&quot;:&quot;high-flyer&quot;}}'
                    '">'
                )
                return httpx.Response(200, text=html, request=httpx.Request("GET", url))

            def post(self, url, **kwargs):
                payload = kwargs.get("json", {})
                post_calls.append(payload)
                keyword = payload.get("keyword")
                if keyword == "AI":
                    body = {
                        "data": {
                            "jobStats": {"total": 1},
                            "jobs": [first],
                        }
                    }
                elif keyword == "AGI":
                    body = {
                        "data": {
                            "jobStats": {"total": 1},
                            "jobs": [duplicate],
                        }
                    }
                else:
                    body = {"data": {"jobStats": {"total": 0}, "jobs": []}}
                return httpx.Response(200, json=body, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "Client", FakeClient)
        jobs = adapter.collect(context)

        assert len(jobs) == 1
        assert jobs[0].external_id == "ds-ai-platform"
        requested_keywords = [payload.get("keyword") for payload in post_calls]
        assert TARGET_KEYWORDS[0] in requested_keywords
        assert TARGET_KEYWORDS[1] in requested_keywords
        assert "AGI" in requested_keywords
        assert "算法" not in requested_keywords
        assert "平台" not in requested_keywords
