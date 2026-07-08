"""Phase 3 tests: adapter registry, official-source parsers, fixture parsing.

All tests are deterministic and offline unless explicitly guarded by
``FINDJOBS_LIVE_TEST=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "adapters"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture from the adapters fixtures directory."""
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """Registry resolves known adapters and rejects unknown names."""

    def test_resolves_known_adapters(self):
        from findjobs.adapters import get_adapter

        for name in (
            "generic_official",
            "tencent_official",
            "alibaba_official",
            "baidu_official",
            "bytedance_official",
            "deepseek_moka",
            "jd_official",
            "kuaishou_official",
            "meituan_official",
            "netease_official",
            "iflytek_official",
        ):
            adapter = get_adapter(name)
            assert adapter is not None

    def test_rejects_unknown_adapter(self):
        from findjobs.adapters import get_adapter

        with pytest.raises(ValueError, match="Unknown adapter"):
            get_adapter("nonexistent_adapter_xyz")


# ---------------------------------------------------------------------------
# Tencent adapter
# ---------------------------------------------------------------------------


class TestTencentAdapter:
    """Tencent adapter correctly parses its fixture JSON."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("tencent_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("tencent.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="tencent", source_slug="tencent-careers"
        )

    def test_parses_three_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 3

    def test_ai_security_engineer_has_ai_and_security_tags(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "T1001")
        assert ai_job.title == "AI Security Engineer"
        assert "AI" in ai_job.matched_tags
        assert "Security" in ai_job.matched_tags
        assert ai_job.salary_disclosed is False
        assert ai_job.url.startswith("https://careers.tencent.com")

    def test_pure_algorithm_engineer_no_ai_tag(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        algo_job = next(j for j in jobs if j.external_id == "T1002")
        assert algo_job.title == "算法工程师"
        assert "AI" not in algo_job.matched_tags

    def test_software_engineer_no_tags(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        swe_job = next(j for j in jobs if j.external_id == "T1003")
        assert swe_job.matched_tags == []


class TestTencentVerifiedApiAdapter:
    """Tencent adapter parses the verified Query API response shape correctly."""

    VERIFIED_FIXTURE = FIXTURES_DIR / "tencent_verified_api.json"

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("tencent_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("tencent_verified_api.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="tencent", source_slug="tencent-careers"
        )

    def test_parses_two_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 2

    def test_uses_verified_api_keys(self, adapter, raw, context):
        """Verify keys PostId, RecruitPostName, LocationName, etc. are read."""
        jobs = adapter.parse(raw, context)
        assert jobs[0].external_id == "T2001"
        assert jobs[0].title == "AI Security Engineer"
        assert jobs[0].location == "Shenzhen"
        assert jobs[0].job_type == "技术类"
        assert jobs[0].url.startswith("https://careers.tencent.com/en-us/job/T2001")

    def test_parses_chinese_date_format(self, adapter, raw, context):
        """LastUpdateTime like '2026年06月18日' should parse correctly."""
        jobs = adapter.parse(raw, context)
        assert jobs[0].published_at is not None
        assert jobs[0].published_at.year == 2026
        assert jobs[0].published_at.month == 6
        assert jobs[0].published_at.day == 18

    def test_algorithm_engineer_no_ai_tag(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        algo_job = next(j for j in jobs if j.external_id == "T2002")
        assert "AI" not in algo_job.matched_tags

    def test_salary_not_disclosed(self, adapter, raw, context):
        """Tencent API does not return salary, so all jobs have undisclosed salary."""
        jobs = adapter.parse(raw, context)
        for j in jobs:
            assert j.salary_disclosed is False


class TestTencentParseWithRequirement:
    """Tencent parse includes Requirement when present."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("tencent_official")

    @pytest.fixture
    def raw(self):
        """Tencent Query API shape *without* Requirement in list items."""
        return {
            "Code": 200,
            "Data": {
                "Posts": [
                    {
                        "PostId": "T3001",
                        "RecruitPostName": "AI Security Lead",
                        "LocationName": "Shenzhen",
                        "CategoryName": "技术类",
                        "Responsibility": "Lead AI security research and team",
                        "Requirement": "10+ years experience in security",
                        "PostURL": "https://careers.tencent.com/en-us/job/T3001",
                        "LastUpdateTime": "2026年06月20日",
                    }
                ]
            },
        }

    @pytest.fixture
    def raw_no_requirement(self):
        """ListItem with Responsibility only (no Requirement)."""
        return {
            "Code": 200,
            "Data": {
                "Posts": [
                    {
                        "PostId": "T3002",
                        "RecruitPostName": "Security Engineer",
                        "LocationName": "Beijing",
                        "CategoryName": "技术类",
                        "Responsibility": "Security operations and incident response",
                        "PostURL": "https://careers.tencent.com/en-us/job/T3002",
                        "LastUpdateTime": "2026年06月19日",
                    }
                ]
            },
        }

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(company_slug="tencent", source_slug="tencent-careers")

    def test_both_sections_appear_in_description(self, adapter, raw, context):
        """When both Responsibility and Requirement exist, both sections are present."""
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 1
        desc = jobs[0].description
        assert "职责:" in desc
        assert "要求:" in desc
        assert "Lead AI security research" in desc
        assert "10+ years experience" in desc

    def test_only_responsibility_when_no_requirement(
        self, adapter, raw_no_requirement, context
    ):
        """When only Responsibility exists, only that section is present."""
        jobs = adapter.parse(raw_no_requirement, context)
        desc = jobs[0].description
        assert "职责:" in desc
        assert "Security operations" in desc
        assert "要求:" not in desc

    def test_classify_uses_combined_description(self, adapter, raw, context):
        """classify_job sees both Responsibility and Requirement for tag signal."""
        jobs = adapter.parse(raw, context)
        assert "Security" in jobs[0].matched_tags
        assert "AI" in jobs[0].matched_tags


class TestTencentCollectDetail:
    """Tencent collect() enriches list items with detail API data."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("tencent_official")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="tencent",
            source_slug="tencent-careers",
            fetch_url="https://careers.tencent.com/tencentcareer/api/post/Query?timestamp=1&pageIndex=1&pageSize=50&language=zh-cn&area=cn",
        )

    def test_detail_api_requirement_included_when_list_lacks_it(
        self, adapter, context, monkeypatch
    ):
        """collect() should include Requirement from detail API when list lacks it.

        Verifies that multi-keyword list fetches are made and dedup works —
        only one detail fetch for the unique PostId.
        """
        import httpx

        list_response = {
            "Code": 200,
            "Data": {
                "Posts": [
                    {
                        "PostId": "T4001",
                        "RecruitPostName": "AI Security Architect",
                        "LocationName": "Shenzhen",
                        "CategoryName": "技术类",
                        "Responsibility": "Design security architecture for AI systems",
                        "PostURL": "https://careers.tencent.com/en-us/job/T4001",
                        "LastUpdateTime": "2026年06月18日",
                    }
                ]
            },
        }

        detail_response = {
            "Code": 200,
            "Data": {
                "PostId": "T4001",
                "RecruitPostName": "AI Security Architect",
                "LocationName": "Shenzhen",
                "CategoryName": "技术类",
                "Responsibility": "Design security architecture for AI systems",
                "Requirement": "MS/PhD in CS; 5+ years security architecture; AI/ML knowledge",
                "PostURL": "https://careers.tencent.com/en-us/job/T4001",
                "LastUpdateTime": "2026年06月18日",
            },
        }

        list_fetch_count = 0
        detail_fetch_count = 0

        def mock_get(url, **kwargs):
            nonlocal list_fetch_count, detail_fetch_count
            if "ByPostId" in url:
                detail_fetch_count += 1
                return httpx.Response(200, json=detail_response)
            list_fetch_count += 1
            return httpx.Response(200, json=list_response)

        monkeypatch.setattr(httpx, "get", mock_get)

        jobs = adapter.collect(context)
        assert len(jobs) == 1
        desc = jobs[0].description
        assert "职责:" in desc
        assert "要求:" in desc
        assert "5+ years security architecture" in desc
        # Multi-keyword collect makes multiple list fetches.
        assert list_fetch_count >= 2
        # Only one detail fetch — dedup ensures unique PostId.
        assert detail_fetch_count == 1

    def test_detail_fetch_failure_falls_back_to_list_item(
        self, adapter, context, monkeypatch
    ):
        """When detail API fails, the adapter falls back to list data."""
        import httpx

        list_response = {
            "Code": 200,
            "Data": {
                "Posts": [
                    {
                        "PostId": "T4002",
                        "RecruitPostName": "Security Analyst",
                        "LocationName": "Beijing",
                        "CategoryName": "技术类",
                        "Responsibility": "Monitor and respond to security incidents",
                        "PostURL": "https://careers.tencent.com/en-us/job/T4002",
                        "LastUpdateTime": "2026年06月17日",
                    }
                ]
            },
        }

        detail_attempts = 0
        list_fetch_count = 0

        def mock_get(url, **kwargs):
            nonlocal detail_attempts, list_fetch_count
            if "ByPostId" in url:
                detail_attempts += 1
                raise httpx.RequestError("Connection failed")
            list_fetch_count += 1
            return httpx.Response(200, json=list_response)

        monkeypatch.setattr(httpx, "get", mock_get)

        jobs = adapter.collect(context)
        assert len(jobs) == 1
        desc = jobs[0].description
        assert "职责:" in desc  # falls back to list Responsibility
        assert "要求:" not in desc  # no Requirement in list data
        assert "Monitor and respond" in desc
        # Multi-keyword: multiple list fetches.
        assert list_fetch_count >= 2
        # At least one detail attempt (for the unique PostId).
        assert detail_attempts >= 1


class TestTencentCollectMultiKeyword:
    """Tencent collect() uses multiple keywords and deduplicates."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("tencent_official")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="tencent",
            source_slug="tencent-careers",
            fetch_url=(
                "https://careers.tencent.com/tencentcareer/api/post/Query"
                "?timestamp=1&pageIndex=1&pageSize=50&language=zh-cn&area=cn"
            ),
        )

    def test_collect_uses_multiple_keywords(
        self, adapter, context, monkeypatch
    ):
        """Tencent collect() queries more than one target keyword."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        seen_keywords: set[str] = set()

        def mock_get(url, **kwargs):
            nonlocal seen_keywords
            if "ByPostId" in url:
                return httpx.Response(
                    200,
                    json={"Code": 200, "Data": {"PostId": "none"}},
                )
            # Extract keyword from URL.
            import urllib.parse

            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            kw = params.get("keyword", [""])[0]
            seen_keywords.add(kw)
            return httpx.Response(
                200,
                json={"Code": 200, "Data": {"Posts": []}},
            )

        monkeypatch.setattr(httpx, "get", mock_get)

        adapter.collect(context)

        assert len(seen_keywords) >= 2
        assert TARGET_KEYWORDS[0] in seen_keywords

    def test_collect_deduplicates_duplicate_ids(
        self, adapter, context, monkeypatch
    ):
        """Tencent collect() deduplicates items with the same PostId across keywords."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        def mock_get(url, **kwargs):
            if "ByPostId" in url:
                # Echo the requested PostId so the merge preserves it.
                import re

                match = re.search(r"postId=([^&]+)", url)
                pid = match.group(1) if match else "unknown"
                return httpx.Response(
                    200,
                    json={
                        "Code": 200,
                        "Data": {
                            "PostId": pid,
                            "RecruitPostName": "Common Role",
                            "Responsibility": "Work",
                            "Requirement": "Skills",
                        },
                    },
                )
            import urllib.parse

            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            kw = params.get("keyword", [""])[0]

            if kw == TARGET_KEYWORDS[0]:
                return httpx.Response(
                    200,
                    json={
                        "Code": 200,
                        "Data": {
                            "Posts": [
                                {
                                    "PostId": "T5001",
                                    "RecruitPostName": "唯一岗位A",
                                    "LocationName": "北京",
                                },
                            ]
                        },
                    },
                )
            if kw == TARGET_KEYWORDS[1]:
                return httpx.Response(
                    200,
                    json={
                        "Code": 200,
                        "Data": {
                            "Posts": [
                                {
                                    "PostId": "T5001",
                                    "RecruitPostName": "唯一岗位A",
                                    "LocationName": "北京",
                                },
                                {
                                    "PostId": "T5002",
                                    "RecruitPostName": "唯一岗位B",
                                    "LocationName": "上海",
                                },
                            ]
                        },
                    },
                )
            return httpx.Response(
                200,
                json={"Code": 200, "Data": {"Posts": []}},
            )

        monkeypatch.setattr(httpx, "get", mock_get)

        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("T5001") == 1
        assert "T5002" in ids
        assert len(jobs) == 2

    def test_collect_stops_at_max_pages_when_total_missing(
        self, adapter, context, monkeypatch
    ):
        """Tencent keyword pagination is bounded even if total is unavailable."""
        import httpx
        import urllib.parse

        from findjobs.adapters import tencent as tencent_module
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        monkeypatch.setattr(tencent_module, "_MAX_QUERY_PAGES_PER_KEYWORD", 2)
        list_calls: list[tuple[str, int]] = []

        def mock_get(url, **kwargs):
            if "ByPostId" in url:
                return httpx.Response(200, json={"Code": 200, "Data": {}})

            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            keyword = params.get("keyword", [""])[0]
            page_index = int(params.get("pageIndex", ["0"])[0])
            list_calls.append((keyword, page_index))

            if keyword == TARGET_KEYWORDS[0]:
                posts = [
                    {
                        "RecruitPostName": f"Security Role {page_index}-{i}",
                        "LocationName": "北京",
                        "CategoryName": "技术类",
                        "Responsibility": "安全研发",
                    }
                    for i in range(tencent_module._QUERY_PAGE_SIZE)
                ]
                return httpx.Response(200, json={"Code": 200, "Data": {"Posts": posts}})

            return httpx.Response(200, json={"Code": 200, "Data": {"Posts": []}})

        monkeypatch.setattr(httpx, "get", mock_get)
        jobs = adapter.collect(context)

        first_keyword_pages = [
            page for keyword, page in list_calls if keyword == TARGET_KEYWORDS[0]
        ]
        assert first_keyword_pages == [1, 2]
        assert len(jobs) == tencent_module._QUERY_PAGE_SIZE * 2


# ---------------------------------------------------------------------------
# Alibaba adapter
# ---------------------------------------------------------------------------


class TestAlibabaAdapter:
    """Alibaba adapter correctly parses its fixture JSON."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("alibaba_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("alibaba.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="alibaba", source_slug="alibaba-talent"
        )

    def test_parses_three_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 3

    def test_ai_application_engineer_has_ai_tag_and_disclosed_salary(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "A2001")
        assert ai_job.title == "AI Application Engineer"
        assert "AI" in ai_job.matched_tags
        assert ai_job.salary_disclosed is True
        assert ai_job.salary_min == 30000.0
        assert ai_job.salary_max == 50000.0

    def test_security_engineer_has_security_tag_negotiable_salary(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        sec_job = next(j for j in jobs if j.external_id == "A2002")
        assert "Security" in sec_job.matched_tags
        assert sec_job.salary_disclosed is False
        assert sec_job.salary_text == "面议"

    def test_pure_recommendation_algorithm_no_ai_tag(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        algo_job = next(j for j in jobs if j.external_id == "A2003")
        assert algo_job.title == "推荐算法工程师"
        assert "AI" not in algo_job.matched_tags
        # But salary with yearly format should be parsed
        assert algo_job.salary_disclosed is True
        assert algo_job.salary_min == 400000.0
        assert algo_job.salary_max == 600000.0
        assert algo_job.salary_period == "yearly"


# ---------------------------------------------------------------------------
# Baidu adapter
# ---------------------------------------------------------------------------


class TestBaiduAdapter:
    """Baidu adapter correctly parses its official API fixture JSON."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("baidu_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("baidu.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="baidu", source_slug="baidu-talent"
        )

    def test_parses_two_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 2

    def test_security_product_has_sections_url_but_no_security_tag(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id.startswith("3e4b"))
        assert job.title == "安全风控产品专家（J85186）"
        assert job.location == "北京市"
        assert job.job_type == "产品"
        assert "职责:" in job.description
        assert "要求:" in job.description
        assert "Security" not in job.matched_tags
        assert job.url.startswith("https://talent.baidu.com/jobs/social-detail")
        assert job.salary_disclosed is False

    def test_algorithm_title_is_excluded(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id.startswith("7f1a"))
        assert job.matched_tags == []

    def test_fetch_uses_first_target_keyword(
        self, adapter, monkeypatch
    ):
        """Baidu fetch() must POST form data with first target keyword (backward compat)."""
        import httpx
        from findjobs.adapters import AdapterContext
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        sent = {}

        def mock_post(url, **kwargs):
            sent["url"] = url
            sent["data"] = kwargs.get("data", {})
            sent["headers"] = kwargs.get("headers", {})
            return httpx.Response(200, json={"status": "ok", "data": {"list": []}})

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.fetch(
            AdapterContext(
                company_slug="baidu",
                source_slug="baidu-talent",
                fetch_url="https://talent.baidu.com/httservice/getPostListNew",
            )
        )

        assert sent["url"].endswith("/httservice/getPostListNew")
        assert sent["data"]["recruitType"] == "SOCIAL"
        assert sent["data"]["keyWord"] == TARGET_KEYWORDS[0]
        assert sent["data"]["pageSize"] == "20"
        assert sent["data"]["curPage"] == "1"
        assert "pageNo" not in sent["data"]
        assert "talent.baidu.com" in sent["headers"]["Referer"]

    def test_fetch_fails_on_baidu_error_status(
        self, adapter, monkeypatch
    ):
        """Baidu API failures should not be treated as empty success."""
        import httpx
        from findjobs.adapters import AdapterContext

        def mock_post(url, **kwargs):
            return httpx.Response(
                200,
                json={"status": "fail", "message": "Illegal argument : pageSize"},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        with pytest.raises(ValueError, match="Illegal argument"):
            adapter.fetch(
                AdapterContext(
                    company_slug="baidu",
                    source_slug="baidu-talent",
                    fetch_url="https://talent.baidu.com/httservice/getPostListNew",
                )
            )

    def test_collect_paginates_until_total(self, adapter, monkeypatch):
        """collect() should paginate the first target keyword until total is reached."""
        import httpx
        from findjobs.adapters import AdapterContext
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        fixture = _load_fixture("baidu.json")
        first = fixture["data"]["list"][0]
        second = fixture["data"]["list"][1]
        requested_calls: list[dict[str, str]] = []

        def mock_post(url, **kwargs):
            data = kwargs.get("data", {})
            kw = data.get("keyWord")
            page_no = data.get("curPage")
            requested_calls.append({"keyword": kw, "page": page_no})

            # Return fixture data only for the first target keyword.
            if kw == TARGET_KEYWORDS[0]:
                if page_no == "1":
                    payload = {
                        "status": "ok",
                        "data": {"total": "2", "list": [first]},
                    }
                elif page_no == "2":
                    payload = {
                        "status": "ok",
                        "data": {"total": "2", "list": [second]},
                    }
                else:
                    payload = {
                        "status": "ok",
                        "data": {"total": "2", "list": []},
                    }
            else:
                payload = {"status": "ok", "data": {"total": "0", "list": []}}
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(httpx, "post", mock_post)

        jobs = adapter.collect(
            AdapterContext(
                company_slug="baidu",
                source_slug="baidu-talent",
                fetch_url="https://talent.baidu.com/httservice/getPostListNew",
            )
        )

        # First keyword should paginate pages 1 and 2.
        first_kw_calls = [c for c in requested_calls if c["keyword"] == TARGET_KEYWORDS[0]]
        assert first_kw_calls[0]["page"] == "1"
        assert first_kw_calls[1]["page"] == "2"
        # Other keywords should also be queried (at least keyword 1).
        other_kw_calls = [c for c in requested_calls if c["keyword"] != TARGET_KEYWORDS[0]]
        assert len(other_kw_calls) >= 1
        assert len(jobs) == 2

    def test_collect_uses_multiple_keywords(
        self, adapter, monkeypatch
    ):
        """Baidu collect() iterates over more than one target keyword."""
        import httpx
        from findjobs.adapters import AdapterContext
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        seen_keywords: set[str] = set()

        def mock_post(url, **kwargs):
            kw = kwargs.get("data", {}).get("keyWord", "")
            seen_keywords.add(kw)
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": {"total": "0", "list": []},
                },
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.collect(
            AdapterContext(
                company_slug="baidu",
                source_slug="baidu-talent",
                fetch_url="https://talent.baidu.com/httservice/getPostListNew",
            )
        )

        assert len(seen_keywords) >= 2
        assert TARGET_KEYWORDS[0] in seen_keywords
        assert TARGET_KEYWORDS[1] in seen_keywords

    def test_collect_deduplicates_duplicate_ids(
        self, adapter, monkeypatch
    ):
        """Baidu collect() deduplicates items with the same postId across keywords."""
        import httpx
        from findjobs.adapters import AdapterContext
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        call_index = 0

        def mock_post(url, **kwargs):
            nonlocal call_index
            kw = kwargs.get("data", {}).get("keyWord")
            call_index += 1
            # First keyword returns a job with postId "dup001".
            if kw == TARGET_KEYWORDS[0]:
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {
                            "total": "2",
                            "list": [
                                {"postId": "dup001", "name": "重复岗位", "workPlace": "北京"},
                                {"postId": "baidu-a", "name": "唯一岗位A", "workPlace": "上海"},
                            ],
                        },
                    },
                )
            # Second keyword also includes dup001 (should be deduped).
            if kw == TARGET_KEYWORDS[1]:
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {
                            "total": "2",
                            "list": [
                                {"postId": "dup001", "name": "重复岗位", "workPlace": "北京"},
                                {"postId": "baidu-b", "name": "唯一岗位B", "workPlace": "杭州"},
                            ],
                        },
                    },
                )
            return httpx.Response(
                200,
                json={"status": "ok", "data": {"total": "0", "list": []}},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        jobs = adapter.collect(
            AdapterContext(
                company_slug="baidu",
                source_slug="baidu-talent",
                fetch_url="https://talent.baidu.com/httservice/getPostListNew",
            )
        )

        ids = [j.external_id for j in jobs]
        assert ids.count("dup001") == 1
        assert "baidu-a" in ids
        assert "baidu-b" in ids
        assert len(jobs) == 3


# ---------------------------------------------------------------------------
# ByteDance adapter
# ---------------------------------------------------------------------------


class TestByteDanceAdapter:
    """ByteDance adapter correctly parses its fixture JSON."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("bytedance_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("bytedance.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="bytedance", source_slug="bytedance-careers"
        )

    def test_parses_three_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 3

    def test_ai_security_engineer_compound_tags_and_bonus_salary(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "B3001")
        assert ai_job.title == "AI Security Engineer"
        assert "AI" in ai_job.matched_tags
        assert "Security" in ai_job.matched_tags
        assert "AI Security" in ai_job.matched_tags
        assert ai_job.salary_disclosed is True
        # ByteDance uses "30k-50k·15薪" format
        assert ai_job.salary_text == "30k-50k·15薪"
        assert ai_job.salary_min == 30000.0
        assert ai_job.salary_max == 50000.0
        assert ai_job.salary_period == "monthly"

    def test_backend_engineer_no_salary_no_tags(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        be_job = next(j for j in jobs if j.external_id == "B3002")
        assert be_job.matched_tags == []
        assert be_job.salary_disclosed is False
        assert be_job.salary_text == ""

    def test_pure_search_algorithm_no_ai_tag(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        algo_job = next(j for j in jobs if j.external_id == "B3003")
        assert "AI" not in algo_job.matched_tags
        assert algo_job.salary_disclosed is True
        assert algo_job.salary_min == 40000.0
        assert algo_job.salary_max == 60000.0

    def test_collect_delegates_to_feishu_official_adapter(
        self, adapter, context, monkeypatch
    ):
        from findjobs.collection import CollectedJob

        called = {}

        def fake_collect(ctx):
            called["context"] = ctx
            return [
                CollectedJob(
                    external_id="bd-feishu-001",
                    title="AI Agent安全工程师",
                    description="职责: AI Agent security engineering",
                    matched_tags=["AI", "Security", "AI Security"],
                )
            ]

        monkeypatch.setattr(adapter._feishu, "collect", fake_collect)
        jobs = adapter.collect(context)

        assert called["context"] is context
        assert jobs[0].external_id == "bd-feishu-001"


# ---------------------------------------------------------------------------
# Generic Official adapter
# ---------------------------------------------------------------------------


class TestGenericOfficialAdapter:
    """Generic adapter flexibly parses common JSON shapes."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("generic_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("generic_official.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="example", source_slug="example-careers"
        )

    def test_parses_three_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 3

    def test_ai_engineer_has_ai_tag_and_salary(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "G1001")
        assert ai_job.title == "AI Engineer"
        assert "AI" in ai_job.matched_tags
        assert ai_job.salary_disclosed is True

    def test_security_analyst_has_security_tag_negotiable(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        sec_job = next(j for j in jobs if j.external_id == "G1002")
        assert "Security" in sec_job.matched_tags
        assert sec_job.salary_disclosed is False
        assert sec_job.salary_text == "面议"

    def test_pure_algorithm_no_ai_tag(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        algo_job = next(j for j in jobs if j.external_id == "G1003")
        assert algo_job.title == "算法工程师"
        assert "AI" not in algo_job.matched_tags
        assert algo_job.salary_disclosed is False


# ---------------------------------------------------------------------------
# NetEase adapter
# ---------------------------------------------------------------------------


class TestNetEaseAdapter:
    """NetEase adapter correctly parses its fixture JSON."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("netease_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("netease.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="netease",
            source_slug="netease-hr",
            base_url="https://hr.163.com",
        )

    def test_parses_two_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 2

    def test_security_engineer_has_security_tag_and_undisclosed_salary(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        sec_job = next(j for j in jobs if j.external_id == "100001")
        assert sec_job.title == "安全工程师"
        assert "Security" in sec_job.matched_tags
        assert "AI" not in sec_job.matched_tags
        assert sec_job.salary_disclosed is False

    def test_ai_security_engineer_has_compound_tags_and_combined_description(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "100002")
        assert ai_job.title == "AI安全研发工程师"
        assert "AI" in ai_job.matched_tags
        assert "Security" in ai_job.matched_tags
        assert ai_job.salary_disclosed is False

    def test_location_joined_from_list(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "100002")
        assert "杭州" in ai_job.location
        assert "北京" in ai_job.location

    def test_parses_timestamp_milliseconds(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert jobs[0].published_at is not None
        assert jobs[0].published_at.year == 2026
        assert jobs[0].published_at.month == 6

    def test_url_constructed_from_base_url_and_id(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        sec_job = next(j for j in jobs if j.external_id == "100001")
        assert sec_job.url == "https://hr.163.com/job/100001"

    def test_description_combines_requirement_and_description(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        ai_job = next(j for j in jobs if j.external_id == "100002")
        assert "大模型安全评估与加固" in ai_job.description
        assert "AIGC内容安全技术" in ai_job.description


class TestNetEaseFetch:
    """NetEase adapter fetch() sends the correct request body."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("netease_official")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="netease",
            source_slug="netease-hr",
            fetch_url="https://hr.163.com/api/hr163/position/queryPage",
        )

    def test_fetch_sends_first_target_keyword(
        self, adapter, context, monkeypatch
    ):
        """fetch() POST body must use TARGET_KEYWORDS[0], not 安全 hardcoded."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        sent_body = None

        def mock_post(url, **kwargs):
            nonlocal sent_body
            sent_body = kwargs.get("json", {})
            return httpx.Response(200, json={"code": 200, "data": {"list": []}})

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.fetch(context)
        assert sent_body is not None
        assert "keyword" in sent_body
        assert sent_body["keyword"] == TARGET_KEYWORDS[0]

    def test_fetch_headers_include_user_agent_and_referer(
        self, adapter, context, monkeypatch
    ):
        """fetch() POST must include User-Agent and Referer headers."""
        import httpx

        sent_headers = None

        def mock_post(url, **kwargs):
            nonlocal sent_headers
            sent_headers = kwargs.get("headers", {})
            return httpx.Response(200, json={"code": 200, "data": {"list": []}})

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.fetch(context)
        assert sent_headers is not None
        assert "User-Agent" in sent_headers
        assert "Referer" in sent_headers
        assert "Mozilla" in sent_headers["User-Agent"]
        assert "hr.163.com" in sent_headers["Referer"]


class TestNetEaseCollect:
    """NetEase collect() paginates multiple keywords and deduplicates."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("netease_official")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="netease",
            source_slug="netease-hr",
            base_url="https://hr.163.com",
            fetch_url="https://hr.163.com/api/hr163/position/queryPage",
        )

    def test_collect_uses_multiple_keywords(
        self, adapter, context, monkeypatch
    ):
        """NetEase collect() iterates over more than one target keyword."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        seen_keywords: list[str] = []

        def mock_post(url, **kwargs):
            kw = kwargs.get("json", {}).get("keyword", "")
            seen_keywords.append(kw)
            return httpx.Response(
                200,
                json={"code": 200, "data": {"list": []}},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.collect(context)

        unique_kw = set(seen_keywords)
        assert len(unique_kw) >= 2
        assert TARGET_KEYWORDS[0] in unique_kw
        assert TARGET_KEYWORDS[1] in unique_kw

    def test_collect_deduplicates_duplicate_ids(
        self, adapter, context, monkeypatch
    ):
        """NetEase collect() deduplicates items with the same id across keywords."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        def mock_post(url, **kwargs):
            kw = kwargs.get("json", {}).get("keyword")
            if kw == TARGET_KEYWORDS[0]:
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "data": {
                            "list": [
                                {
                                    "id": 1001,
                                    "name": "重复岗位",
                                    "workPlaceNameList": ["北京"],
                                    "firstPostTypeName": "技术类",
                                    "updateTime": 1700000000000,
                                },
                                {
                                    "id": 1002,
                                    "name": "唯一A",
                                    "workPlaceNameList": ["上海"],
                                    "firstPostTypeName": "技术类",
                                    "updateTime": 1700000000000,
                                },
                            ]
                        },
                    },
                )
            if kw == TARGET_KEYWORDS[1]:
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "data": {
                            "list": [
                                {
                                    "id": 1001,
                                    "name": "重复岗位",
                                    "workPlaceNameList": ["北京"],
                                    "firstPostTypeName": "技术类",
                                    "updateTime": 1700000000000,
                                },
                                {
                                    "id": 1003,
                                    "name": "唯一B",
                                    "workPlaceNameList": ["杭州"],
                                    "firstPostTypeName": "技术类",
                                    "updateTime": 1700000000000,
                                },
                            ]
                        },
                    },
                )
            return httpx.Response(
                200,
                json={"code": 200, "data": {"list": []}},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("1001") == 1
        assert "1002" in ids
        assert "1003" in ids
        assert len(jobs) == 3

    def test_collect_stops_on_short_page(
        self, adapter, context, monkeypatch
    ):
        """NetEase collect() stops paginating a keyword when a short page is returned."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        page_count = 0

        def mock_post(url, **kwargs):
            nonlocal page_count
            kw = kwargs.get("json", {}).get("keyword")
            page_no = kwargs.get("json", {}).get("currentPage")
            if kw == TARGET_KEYWORDS[0]:
                page_count += 1
                # Full page for page 1, short for page 2.
                if page_no == 1:
                    items = [
                        {"id": i, "name": f"Job {i}"}
                        for i in range(50)
                    ]
                else:
                    items = [{"id": 999, "name": "Last Job"}]
                return httpx.Response(
                    200,
                    json={"code": 200, "data": {"list": items}},
                )
            return httpx.Response(
                200,
                json={"code": 200, "data": {"list": []}},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.collect(context)

        # First keyword should see page 1 (full 50 items) and page 2 (short/1 item).
        assert page_count == 2


# ---------------------------------------------------------------------------
# iFlytek adapter (newly added)
# ---------------------------------------------------------------------------


class TestNewOfficialAdaptersRegistered:
    """Ensure newly added official adapters are registered and active."""

    def test_iflytek_official_registered(self):
        from findjobs.adapters import get_adapter

        adapter = get_adapter("iflytek_official")
        assert adapter is not None

    def test_iflytek_not_generic(self):
        from findjobs.adapters import get_adapter

        adapter = get_adapter("iflytek_official")
        assert type(adapter).__name__ != "GenericOfficialAdapter"

    def test_iflytek_source_is_active(self):
        from findjobs.config import load_sources

        config = load_sources()
        iflytek = next(s for s in config.sources if s.slug == "iflytek-careers")
        assert iflytek.is_active is True
        assert iflytek.adapter == "iflytek_official"


class TestIFlyTekAdapter:
    """iFlytek adapter correctly parses its BeiSen API fixture JSON."""

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("iflytek_official")

    @pytest.fixture
    def raw(self):
        return _load_fixture("iflytek.json")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="iflytek",
            source_slug="iflytek-careers",
            base_url="https://iflytek.zhiye.com",
        )

    def test_parses_three_jobs(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 3

    def test_target_keyword_set_matches_adapter_checklist(self):
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        assert TARGET_KEYWORDS == [
            "AI",
            "大模型",
            "智能体",
            "Agent",
            "LLM",
            "MLOps",
            "推理",
            "模型部署",
            "安全",
            "AI安全",
            "风控",
            "反作弊",
            "隐私",
            "数据安全",
            "云安全",
            "漏洞",
            "渗透",
            "攻防",
            "红队",
        ]
        assert "算法" not in TARGET_KEYWORDS
        assert "平台" not in TARGET_KEYWORDS

    def test_security_solution_has_duty_require_and_undisclosed_salary(
        self, adapter, raw, context
    ):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "IF1001")
        assert job.title == "安全解决方案工程师"
        assert "职责:" in job.description
        assert "要求:" in job.description
        assert "网络安全架构评估" in job.description
        assert "CISSP" in job.description
        assert job.salary_disclosed is False

    def test_algorithm_role_no_tags(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "IF1002")
        assert job.title == "AI安全算法工程师"
        assert job.matched_tags == []

    def test_non_target_role_no_tags(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "IF1003")
        assert job.matched_tags == []

    def test_multi_location_preserved(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "IF1001")
        assert "、" in job.location
        assert "合肥" in job.location
        assert "北京" in job.location

    def test_url_stable(self, adapter, raw, context):
        jobs = adapter.parse(raw, context)
        job = next(j for j in jobs if j.external_id == "IF1001")
        assert (
            job.url
            == "https://iflytek.zhiye.com/social/detail?jobAdId=IF1001"
        )

    def test_data_url_fallback_without_jobadid(self, adapter, context):
        """When JobAdId is missing, use base_url as the URL."""
        raw = {
            "Code": 200,
            "Count": 1,
            "Data": [
                {
                    "Id": "IF2001",
                    "JobAdName": "Test Role",
                    "Duty": "Test duty",
                }
            ],
            "Total": 0,
        }
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 1
        assert jobs[0].external_id == "IF2001"
        assert (
            jobs[0].url
            == "https://iflytek.zhiye.com/social/detail?jobAdId=IF2001"
        )

    def test_salary_non_empty_is_parsed(self, adapter, context):
        """When Salary is a non-empty string, parse_salary should run."""
        raw = {
            "Code": 200,
            "Count": 1,
            "Data": [
                {
                    "JobAdId": "IF2002",
                    "JobAdName": "Senior Engineer",
                    "Duty": "Work",
                    "Salary": "30k-50k",
                }
            ],
            "Total": 0,
        }
        jobs = adapter.parse(raw, context)
        assert len(jobs) == 1
        assert jobs[0].salary_disclosed is True
        assert jobs[0].salary_min == 30000.0

    def test_collect_payload_structure(self, adapter, context, monkeypatch):
        """collect() POSTs correct BeiSen payload and headers."""
        import httpx
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        sent_calls: list[dict] = []

        def mock_post(url, **kwargs):
            sent_calls.append(
                {
                    "url": url,
                    "json": kwargs.get("json", {}),
                    "headers": kwargs.get("headers", {}),
                }
            )
            return httpx.Response(
                200,
                json={"Code": 200, "Count": 0, "Data": [], "Total": 0},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        adapter.collect(context)

        assert len(sent_calls) >= 1
        call = sent_calls[0]
        assert "api/Jobad/GetJobAdPageList" in call["url"]
        assert call["json"]["PageSize"] == 100
        assert call["json"]["PageIndex"] == 0
        assert call["json"]["Category"] == ["1"]
        assert call["json"]["KeyWords"] == TARGET_KEYWORDS[0]
        assert call["json"]["SpecialType"] == 0
        assert call["json"]["PortalId"] == ""
        assert "DisplayFields" in call["json"]
        # Headers
        h = call["headers"]
        assert "XMLHttpRequest" in h.get("X-Requested-With", "")
        assert "zh_CN" in h.get("langType", "")
        assert "Mozilla" in h.get("User-Agent", "")
        assert "iflytek.zhiye.com" in h.get("Referer", "")

    def test_collect_paginates_and_deduplicates(
        self, adapter, context, monkeypatch
    ):
        """collect() returns parsed jobs from fixture and stops on empty."""
        import httpx

        fixture = _load_fixture("iflytek.json")
        call_index = [0]

        def mock_post(url, **kwargs):
            idx = call_index[0]
            call_index[0] += 1
            if idx == 0:
                return httpx.Response(200, json=fixture)
            return httpx.Response(
                200,
                json={"Code": 200, "Count": 0, "Data": [], "Total": 0},
            )

        monkeypatch.setattr(httpx, "post", mock_post)

        jobs = adapter.collect(context)
        assert len(jobs) >= 1  # at least fixture jobs
        assert any(j.external_id == "IF1001" for j in jobs)
        assert any(j.external_id == "IF1002" for j in jobs)
        assert any(j.external_id == "IF1003" for j in jobs)

    def test_iflytek_uses_shared_target_keywords(self):
        from findjobs.adapters import iflytek
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        assert not hasattr(iflytek, "_KEYWORDS")
        payload = iflytek._build_payload(TARGET_KEYWORDS[1], 0)
        assert payload["KeyWords"] == TARGET_KEYWORDS[1]


# ---------------------------------------------------------------------------
# Sources config validation
# ---------------------------------------------------------------------------


class TestSourcesConfig:
    """config/sources.yaml meets Phase 3 requirements."""

    def test_has_at_least_ten_sources(self):
        from findjobs.config import load_sources

        config = load_sources()
        assert len(config.sources) >= 10

    def test_no_huawei_entries(self):
        """Verify the YAML file itself contains no Huawei reference."""
        from findjobs.paths import get_config_dir

        path = get_config_dir() / "sources.yaml"
        text = path.read_text(encoding="utf-8").lower()
        assert "huawei" not in text

    def test_all_adapter_names_resolve(self):
        """Every source's adapter field must resolve in the registry."""
        from findjobs.config import load_sources
        from findjobs.adapters import get_adapter

        config = load_sources()
        for source in config.sources:
            adapter = get_adapter(source.adapter)
            assert adapter is not None

    def test_key_sources_have_dedicated_adapters(self):
        """Key live/known sources use non-generic adapters."""
        from findjobs.config import load_sources

        config = load_sources()
        sources_by_slug = {s.slug: s for s in config.sources}

        tc = sources_by_slug.get("tencent-careers")
        assert tc is not None, "tencent-careers source missing"
        assert tc.adapter == "tencent_official"

        ali = sources_by_slug.get("alibaba-talent")
        assert ali is not None, "alibaba-talent source missing"
        assert ali.adapter == "alibaba_official"

        for slug in {
            "alibaba-aliyun-careers",
            "alibaba-tongyi-careers",
            "alibaba-quark-careers",
            "alibaba-dingtalk-careers",
            "alibaba-holding-careers",
        }:
            source = sources_by_slug.get(slug)
            assert source is not None, f"{slug} source missing"
            assert source.adapter == "alibaba_group_official"

        ant = sources_by_slug.get("antgroup-talent")
        assert ant is not None, "antgroup-talent source missing"
        assert ant.adapter == "antgroup_official"

        baidu = sources_by_slug.get("baidu-talent")
        assert baidu is not None, "baidu-talent source missing"
        assert baidu.adapter == "baidu_official"

        bd = sources_by_slug.get("bytedance-careers")
        assert bd is not None, "bytedance-careers source missing"
        assert bd.adapter == "bytedance_official"

        ne = sources_by_slug.get("netease-hr")
        assert ne is not None, "netease-hr source missing"
        assert ne.adapter == "netease_official"

        ks = sources_by_slug.get("kuaishou-zhaopin")
        assert ks is not None, "kuaishou-zhaopin source missing"
        assert ks.adapter == "kuaishou_official"

        mt = sources_by_slug.get("meituan-zhaopin")
        assert mt is not None, "meituan-zhaopin source missing"
        assert mt.adapter == "meituan_official"

        jd = sources_by_slug.get("jd-zhaopin")
        assert jd is not None, "jd-zhaopin source missing"
        assert jd.adapter == "jd_official"

    def test_at_least_one_active_source(self):
        """At least one source is active so live collection can work."""
        from findjobs.config import load_sources

        config = load_sources()
        active = [s for s in config.sources if s.is_active]
        assert len(active) >= 1

    def test_tencent_is_active_by_default(self):
        """Tencent should be the enabled source in default config."""
        from findjobs.config import load_sources

        config = load_sources()
        tencent = next(
            (s for s in config.sources if s.slug == "tencent-careers"), None
        )
        assert tencent is not None
        assert tencent.is_active is True

    def test_verified_live_sources_active_others_inactive(self):
        """Verified live official sources are active by default."""
        from findjobs.config import load_sources

        config = load_sources()
        active_slugs = {
            "tencent-careers",
            "baidu-talent",
            "bytedance-careers",
            "kuaishou-zhaopin",
            "xiaomi-careers",
            "meituan-zhaopin",
            "antgroup-talent",
            "alibaba-aliyun-careers",
            "alibaba-tongyi-careers",
            "alibaba-quark-careers",
            "alibaba-dingtalk-careers",
            "alibaba-holding-careers",
            "jd-zhaopin",
            "netease-hr",
            "deepseek-careers",
            "zhipu-join-us",
            "moonshot-careers",
            "minimax-careers",
            "01ai-feishu",
            "baichuan-feishu",
            "modelbest-feishu",
            "sensetime-careers",
            "iflytek-careers",
        }
        for s in config.sources:
            if s.slug in active_slugs:
                assert s.is_active is True, f"{s.slug} should be active"
            else:
                assert s.is_active is False, f"{s.slug} should be inactive"

    def test_unverified_ai_company_backlog_sources_are_inactive(self):
        """Unverified AI-company backlog entries must not collect before review."""
        from findjobs.config import load_sources

        config = load_sources()
        sources_by_slug = {s.slug: s for s in config.sources}
        backlog_slugs = set()

        assert backlog_slugs <= set(sources_by_slug)
        for slug in backlog_slugs:
            source = sources_by_slug[slug]
            assert source.adapter == "generic_official"
            assert source.is_active is False

        deepseek = sources_by_slug["deepseek-careers"]
        assert deepseek.adapter == "deepseek_moka"
        assert deepseek.is_active is True

        zhipu = sources_by_slug["zhipu-join-us"]
        assert zhipu.adapter == "feishu_official"
        assert zhipu.is_active is True

        for slug in {
            "moonshot-careers",
            "minimax-careers",
            "01ai-feishu",
            "baichuan-feishu",
            "modelbest-feishu",
        }:
            source = sources_by_slug[slug]
            assert source.adapter in {"feishu_official", "moka_official"}
            assert source.is_active is True


# ---------------------------------------------------------------------------
# Feishu adapter collect strategy (Stage 2)
# ---------------------------------------------------------------------------


class TestFeishuOfficialCollectStrategy:
    """Feishu adapter collect() strategy: small-site blank scan vs large-site keyword mode.

    All tests monkeypatch ``_fetch_page``, ``_fetch_site``, and
    ``_fetch_csrf_token`` so no real network is used.
    """

    @pytest.fixture
    def adapter(self):
        from findjobs.adapters import get_adapter

        return get_adapter("feishu_official")

    @pytest.fixture
    def context(self):
        from findjobs.adapters import AdapterContext

        return AdapterContext(
            company_slug="test",
            source_slug="test-feishu",
            base_url="https://test.jobs.feishu.cn",
        )

    # ------------------------------------------------------------------
    # Small site ─ total within FULL_SCAN_CAP → blank keyword pagination
    # ------------------------------------------------------------------

    def test_small_site_uses_blank_keyword_scan(self, adapter, context, monkeypatch):
        """When total ≤ FULL_SCAN_CAP, collect uses blank keyword pagination."""
        call_log: list[dict] = []
        small_total = 300

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset < small_total:
                items = [
                    {"id": f"job_{offset + i}", "title": f"Job {offset + i}"}
                    for i in range(min(50, small_total - offset))
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": small_total},
                }
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        jobs = adapter.collect(context)

        blank_calls = [c for c in call_log if c["keyword"] == ""]
        keyword_calls = [c for c in call_log if c["keyword"] != ""]
        assert len(blank_calls) == 6  # pages 0, 50, 100, 150, 200, 250
        assert len(keyword_calls) == 0
        assert len(jobs) == small_total

    # ------------------------------------------------------------------
    # Large site ─ total exceeds FULL_SCAN_CAP → target-keyword mode
    # ------------------------------------------------------------------

    def test_large_site_switches_to_keyword_collection(
        self, adapter, context, monkeypatch
    ):
        """When total > FULL_SCAN_CAP, collect switches to keyword pagination."""
        call_log: list[dict] = []

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset == 0:
                items = [
                    {"id": f"init_{i}", "title": f"Init {i}"} for i in range(50)
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": 5000},
                }
            if keyword == "AI":
                items = [
                    {"id": f"AI_{i}", "title": f"AI Job {i}"}
                    for i in range(min(50, max(0, 50 - offset)))
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": 50},
                }
            # All other keywords return empty
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        jobs = adapter.collect(context)

        blank_calls = [c for c in call_log if c["keyword"] == ""]
        keyword_calls = [c for c in call_log if c["keyword"] != ""]
        assert len(blank_calls) == 1  # only page 0 is blank
        assert len(keyword_calls) > 0  # at least one keyword queried
        # initial 50 + AI results = 100
        assert len(jobs) == 100

    def test_large_site_uses_shared_target_keywords(
        self, adapter, context, monkeypatch
    ):
        """Large Feishu sites use the shared keyword contract, not a local copy."""
        from findjobs.adapters import feishu
        from findjobs.adapters.keywords import TARGET_KEYWORDS

        assert not hasattr(feishu, "_KEYWORDS")
        call_log: list[dict] = []

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset == 0:
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [{"id": "init", "title": "Init"}],
                        "count": 5000,
                    },
                }
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        adapter.collect(context)

        keyword_calls = [c["keyword"] for c in call_log if c["keyword"]]
        assert keyword_calls == TARGET_KEYWORDS
        assert "算法" not in keyword_calls
        assert "平台" not in keyword_calls

    # ------------------------------------------------------------------
    # Dedup across keywords
    # ------------------------------------------------------------------

    def test_keyword_collection_deduplicates_by_id(
        self, adapter, context, monkeypatch
    ):
        """Jobs with the same id across keyword queries appear only once."""
        call_log: list[dict] = []

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset == 0:
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [
                            {"id": "shared_by_id", "title": "Shared ID Job"},
                            {"id": "unique_init", "title": "Unique Initial"},
                        ],
                        "count": 10000,
                    },
                }
            if keyword == "AI":
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [
                            {"id": "shared_by_id", "title": "Shared ID Job"},
                            {"id": "unique_ai", "title": "Unique AI Job"},
                        ],
                        "count": 2,
                    },
                }
            if keyword == "MLOps":
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [
                            {"id": "shared_by_id", "title": "Shared ID Job"},
                            {"id": "unique_mlops", "title": "Unique MLOps Job"},
                        ],
                        "count": 2,
                    },
                }
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        jobs = adapter.collect(context)

        ids = [j.external_id for j in jobs]
        assert ids.count("shared_by_id") == 1
        assert "unique_init" in ids
        assert "unique_ai" in ids
        assert "unique_mlops" in ids
        assert len(jobs) == 4

    def test_keyword_collection_deduplicates_by_title_location(
        self, adapter, context, monkeypatch
    ):
        """Jobs without id but same title+location appear only once."""
        call_log: list[dict] = []

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset == 0:
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [
                            {
                                "title": "重复岗位",
                                "city_list": [{"i18n_name": "北京"}],
                            },
                            {
                                "title": "唯一初始",
                                "city_list": [{"i18n_name": "上海"}],
                            },
                        ],
                        "count": 10000,
                    },
                }
            if keyword == "AI":
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [
                            {
                                "title": "重复岗位",
                                "city_list": [{"i18n_name": "北京"}],
                            },
                            {
                                "title": "唯一AI",
                                "city_list": [{"i18n_name": "杭州"}],
                            },
                        ],
                        "count": 2,
                    },
                }
            if keyword == "MLOps":
                return {
                    "code": 0,
                    "data": {
                        "job_post_list": [
                            {
                                "title": "重复岗位",
                                "city_list": [{"i18n_name": "北京"}],
                            },
                            {
                                "title": "唯一MLOps",
                                "city_list": [{"i18n_name": "深圳"}],
                            },
                        ],
                        "count": 2,
                    },
                }
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        jobs = adapter.collect(context)

        titles = [j.title for j in jobs]
        assert titles.count("重复岗位") == 1
        assert "唯一初始" in titles
        assert "唯一AI" in titles
        assert "唯一MLOps" in titles
        assert len(jobs) == 4

    # ------------------------------------------------------------------
    # Keyword pagination beyond _MAX_PAGES (large keyword result sets)
    # ------------------------------------------------------------------

    def test_keyword_pagination_continues_beyond_20_pages(
        self, adapter, context, monkeypatch
    ):
        """Keyword pagination fetches enough pages to cover a keyword total
        that exceeds the old 20-page (1000-item) cap."""
        call_log: list[dict] = []

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset == 0:
                items = [
                    {"id": f"init_{i}", "title": f"Init {i}"} for i in range(50)
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": 5000},
                }
            if keyword == "AI":
                remaining = max(0, 1200 - offset)
                count = min(50, remaining)
                items = [
                    {"id": f"AI_{offset + i}", "title": f"AI Job {offset + i}"}
                    for i in range(count)
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": 1200},
                }
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        jobs = adapter.collect(context)

        ai_calls = [c for c in call_log if c["keyword"] == "AI"]
        # 1200 / 50 = 24 pages — exceeds the old 20-page cap
        assert len(ai_calls) == 24
        # 50 initial + 1200 AI items
        assert len(jobs) == 1250

    def test_keyword_pagination_stops_at_total(
        self, adapter, context, monkeypatch
    ):
        """Keyword pagination stops when the keyword's total is reached,
        even when the last page is full (not short)."""
        call_log: list[dict] = []

        monkeypatch.setattr(
            adapter,
            "_fetch_site",
            lambda client, ctx: (ctx.base_url.rstrip("/"), "test"),
        )
        monkeypatch.setattr(
            adapter,
            "_fetch_csrf_token",
            lambda client, base_url, website_path: "test-token",
        )

        def fake_fetch_page(
            client, *, base_url, website_path, keyword, offset, csrf_token=""
        ):
            call_log.append({"keyword": keyword, "offset": offset})
            if keyword == "" and offset == 0:
                items = [
                    {"id": f"init_{i}", "title": f"Init {i}"} for i in range(50)
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": 5000},
                }
            if keyword == "AI":
                remaining = max(0, 100 - offset)
                count = min(50, remaining)
                items = [
                    {"id": f"AI_{offset + i}", "title": f"AI Job {offset + i}"}
                    for i in range(count)
                ]
                return {
                    "code": 0,
                    "data": {"job_post_list": items, "count": 100},
                }
            return {"code": 0, "data": {"job_post_list": [], "count": 0}}

        monkeypatch.setattr(adapter, "_fetch_page", fake_fetch_page)
        jobs = adapter.collect(context)

        ai_calls = [c for c in call_log if c["keyword"] == "AI"]
        # 100 / 50 = 2 full pages — stops at total, not via short-page
        assert len(ai_calls) == 2
        # Without total-based stop, a third empty page would be fetched
        assert all(c["offset"] < 100 for c in ai_calls)
        assert len(jobs) == 150


# ---------------------------------------------------------------------------
# Live smoke tests (opt-in only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("FINDJOBS_LIVE_TEST") != "1",
    reason="Set FINDJOBS_LIVE_TEST=1 to run live smoke tests",
)
class TestLiveSmoke:
    """Verify that configured career pages are reachable.

    These tests only check HTTP reachability; they do **not** assert job
    counts or parse responses.
    """

    @pytest.fixture
    def http_client(self):
        import httpx

        with httpx.Client(timeout=10) as client:
            yield client

    def test_tencent_careers_reachable(self, http_client):
        resp = http_client.get("https://careers.tencent.com")
        assert resp.status_code < 500

    def test_baidu_talent_reachable(self, http_client):
        resp = http_client.get("https://talent.baidu.com")
        assert resp.status_code < 500

    def test_alibaba_talent_reachable(self, http_client):
        resp = http_client.get("https://talent.alibaba.com")
        assert resp.status_code < 500

    def test_bytedance_careers_reachable(self, http_client):
        resp = http_client.get("https://jobs.bytedance.com")
        assert resp.status_code < 500
