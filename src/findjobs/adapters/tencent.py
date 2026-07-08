"""Tencent official career-page adapter.

Supports two JSON response shapes:
- Legacy API returns ``data.posts[*]`` with fields ``id``, ``name``, etc.
- Verified Query API (``/tencentcareer/api/post/Query``) returns ``Data.Posts[*]``
  with capitalised keys ``PostId``, ``RecruitPostName``, ``LocationName``,
  ``CategoryName``, ``Responsibility``, ``LastUpdateTime``, ``PostURL``::

    {
      "Code": 200,
      "Data": {
        "Posts": [
          {
            "PostId": "T2001",
            "RecruitPostName": "AI Security Engineer",
            "LocationName": "Shenzhen",
            "CategoryName": "技术类",
            "Responsibility": "LLM security research ...",
            "PostURL": "https://careers.tencent.com/job/T2001",
            "LastUpdateTime": "2026年06月18日"
          }
        ]
      }
    }

- Detail API (``/tencentcareer/api/post/ByPostId``) returns a single post with
  both ``Responsibility`` and ``Requirement`` fields.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
import urllib.parse

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_DETAIL_BASE = (
    "https://careers.tencent.com/tencentcareer/api/post/ByPostId"
)
_DETAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://careers.tencent.com/",
}

_QUERY_PAGE_SIZE = 50
_MAX_QUERY_PAGES_PER_KEYWORD = 100
_DETAIL_MAX_WORKERS = 8


def _str(val: Any) -> str:
    return str(val) if val is not None else ""


def _try_parse_date(val: str) -> datetime | None:
    if not val:
        return None
    val = val.replace("年", "-").replace("月", "-").replace("日", "")
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _build_description(item: dict[str, Any]) -> str:
    """Build a structured description from Responsibility and Requirement fields.

    Returns a string with clear section headers when both exist, or just the
    available section when only one is present.  Falls back to a plain
    ``description`` / ``description`` field when neither is available (legacy).
    """
    responsibility = _str(
        item.get("Responsibility") or item.get("responsibility") or ""
    )
    requirement = _str(
        item.get("Requirement") or item.get("requirement") or ""
    )

    # Fallback: plain description field (legacy API shape).
    if not responsibility and not requirement:
        return _str(item.get("description") or "")

    parts: list[str] = []
    if responsibility:
        parts.append("职责:\n" + responsibility)
    if requirement:
        parts.append("要求:\n" + requirement)

    return "\n\n".join(parts)


def _posts_from_raw(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the post list from a Tencent API response (legacy or verified)."""
    data_wrapper = raw.get("data") or raw.get("Data") or {}
    return (
        data_wrapper.get("posts")
        or data_wrapper.get("Posts")
        or data_wrapper.get("jobList")
        or raw.get("posts")
        or raw.get("Posts")
        or []
    )


def _keyword_total(raw: dict[str, Any]) -> int | None:
    """Extract total count from a Tencent Query API response, if available."""
    data_wrapper = raw.get("data") or raw.get("Data") or {}
    for key in ("total", "Total", "count", "Count"):
        val = data_wrapper.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return None


def _detail_url(post_id: str) -> str:
    return f"{_DETAIL_BASE}?postId={post_id}&language=zh-cn"


def _fetch_detail(post_id: str) -> dict[str, Any] | None:
    import httpx

    try:
        resp = httpx.get(_detail_url(post_id), headers=_DETAIL_HEADERS, timeout=20)
        if resp.status_code >= 400:
            resp.raise_for_status()
        detail = resp.json().get("Data") or {}
    except Exception:
        return None
    return detail if isinstance(detail, dict) else None


def _should_fetch_detail(item: dict[str, Any]) -> bool:
    title = _str(item.get("RecruitPostName") or item.get("name") or "")
    description = _build_description(item)
    job_type = _str(item.get("CategoryName") or item.get("job_type") or "")
    return bool(classify_job(title, description, job_type))


def _build_query_url(
    context: AdapterContext, keyword: str, page_index: int
) -> str:
    """Build a Tencent Query API URL with the given keyword and page index.

    Preserves other query parameters (language, area, etc.) from the
    configured ``fetch_url``.
    """
    base = context.fetch_url or (
        f"{(context.base_url or 'https://careers.tencent.com').rstrip('/')}"
        "/tencentcareer/api/post/Query"
    )
    parsed = urllib.parse.urlparse(base)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    params["keyword"] = [keyword]
    params["pageIndex"] = [str(page_index)]
    new_query = urllib.parse.urlencode(params, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


class TencentOfficialAdapter(BaseAdapter):
    """Adapter for Tencent's official career page API."""

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch Tencent list JSON without requiring a request on mocked responses."""
        import httpx

        url = context.fetch_url or context.base_url
        resp = httpx.get(url, headers=_DETAIL_HEADERS, timeout=30)
        if resp.status_code >= 400:
            resp.raise_for_status()
        return resp.json()

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        posts = _posts_from_raw(raw)

        results: list[CollectedJob] = []
        for item in posts:
            external_id = _str(item.get("id") or item.get("PostId") or "")
            title = _str(item.get("name") or item.get("RecruitPostName") or "")
            url = _str(item.get("post_url") or item.get("PostURL") or item.get("PostUrl") or "")
            description = _build_description(item)
            location = _str(item.get("location") or item.get("LocationName") or "")
            job_type = _str(item.get("category") or item.get("CategoryName") or "")

            published_str = _str(
                item.get("publish_time")
                or item.get("PublishTime")
                or item.get("LastUpdateTime")
                or item.get("lastUpdateTime")
                or ""
            )
            published = _try_parse_date(published_str)

            salary = parse_salary(None)
            tags = classify_job(title, description, job_type)

            results.append(
                CollectedJob(
                    external_id=external_id,
                    title=title,
                    url=url,
                    description=description,
                    salary_text=salary["salary_text"],
                    salary_min=salary["salary_min"],
                    salary_max=salary["salary_max"],
                    salary_currency=salary["salary_currency"],
                    salary_period=salary["salary_period"],
                    salary_disclosed=salary["salary_disclosed"],
                    location=location,
                    job_type=job_type,
                    published_at=published,
                    matched_tags=tags,
                )
            )

        return results

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Collect across all target keywords, paginating, deduplicating, and enriching.

        For each keyword, attempts to extract a total/count from the response.
        If unavailable, stops on short/empty page.  List items are then enriched
        via the ByPostId detail API (same as before).  Duplicate ``PostId`` /
        ``id`` values across keywords are included only once.
        """
        import httpx

        # ------------------------------------------------------------------
        # Step 1: fetch all keywords, paginate, deduplicate raw items
        # ------------------------------------------------------------------
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            page_index = 1
            kw_total: int | None = None
            kw_item_count = 0

            while page_index <= _MAX_QUERY_PAGES_PER_KEYWORD:
                url = _build_query_url(context, keyword, page_index)
                resp = httpx.get(url, headers=_DETAIL_HEADERS, timeout=30)
                if resp.status_code >= 400:
                    resp.raise_for_status()
                raw = resp.json()

                posts = _posts_from_raw(raw)

                # Extract total for this keyword (may be None).
                if kw_total is None:
                    kw_total = _keyword_total(raw)

                if not posts:
                    break

                for item in posts:
                    item_id = _str(item.get("PostId") or item.get("id") or "")
                    title = _str(
                        item.get("RecruitPostName") or item.get("name") or ""
                    )
                    location = _str(
                        item.get("LocationName") or item.get("location") or ""
                    )

                    if item_id:
                        if item_id in seen_ids:
                            continue
                        seen_ids.add(item_id)
                    else:
                        key_tuple = (title, location)
                        if key_tuple in seen_key_tuples:
                            continue
                        seen_key_tuples.add(key_tuple)

                    all_items.append(item)

                kw_item_count += len(posts)

                # Stop conditions.
                if kw_total is not None and kw_item_count >= kw_total:
                    break
                if len(posts) < _QUERY_PAGE_SIZE:
                    break

                page_index += 1

        # ------------------------------------------------------------------
        # Step 2: enrich likely relevant unique items with detail API.
        # ------------------------------------------------------------------
        enriched = list(all_items)
        detail_targets = [
            (index, _str(item.get("PostId") or item.get("id") or ""))
            for index, item in enumerate(all_items)
            if (item.get("PostId") or item.get("id")) and _should_fetch_detail(item)
        ]

        if detail_targets:
            worker_count = min(_DETAIL_MAX_WORKERS, len(detail_targets))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                details = executor.map(
                    _fetch_detail, [post_id for _, post_id in detail_targets]
                )
                for (index, _), detail in zip(detail_targets, details):
                    if detail:
                        enriched[index] = {**enriched[index], **detail}

        # Rebuild structure for parse().
        raw_enriched = {"Data": {"Posts": enriched}}
        return self.parse(raw_enriched, context)


register("tencent_official", TencentOfficialAdapter())
