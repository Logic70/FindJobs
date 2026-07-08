"""Baidu official talent-page adapter."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://talent.baidu.com/jobs/social-list",
    "Accept": "application/json, text/plain, */*",
}
_PAGE_SIZE = 20
_MAX_PAGES = 50


def _str(val: Any) -> str:
    return str(val) if val is not None else ""


def _try_parse_date(val: str) -> datetime | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _build_description(item: dict[str, Any]) -> str:
    responsibility = _str(item.get("workContent") or item.get("description") or "")
    requirement = _str(item.get("serviceCondition") or item.get("requirement") or "")

    parts: list[str] = []
    if responsibility:
        parts.append("职责:\n" + responsibility)
    if requirement:
        parts.append("要求:\n" + requirement)
    return "\n\n".join(parts)


def _post_with_retry(
    url: str, *, data: dict[str, str], headers: dict[str, str], max_retries: int = 2
):
    import httpx

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(url, data=data, headers=headers, timeout=30)
            if resp.status_code >= 400:
                resp.raise_for_status()
            return resp
        except httpx.TransportError:
            if attempt == max_retries:
                raise
            time.sleep((attempt + 1) * 1.0)


class BaiduOfficialAdapter(BaseAdapter):
    """Adapter for Baidu's official talent API."""

    def _fetch_page(
        self, context: AdapterContext, page_no: int, *, keyword: str | None = None
    ) -> dict[str, Any]:
        """Fetch one page for the given keyword (defaults to first target keyword)."""
        url = context.fetch_url or context.base_url
        payload = {
            "recruitType": "SOCIAL",
            "keyWord": keyword or TARGET_KEYWORDS[0],
            "pageSize": str(_PAGE_SIZE),
            "curPage": str(page_no),
        }
        resp = _post_with_retry(url, data=payload, headers=_HEADERS)
        raw = resp.json()
        if raw.get("status") not in (None, "ok"):
            raise ValueError(
                "Baidu Talent API returned failure: "
                f"{raw.get('message') or raw.get('status')}"
            )
        return raw

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch first page of the first target keyword (backward-compatible)."""
        return self._fetch_page(context, page_no=1)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Collect across all target keywords, paginating and deduplicating."""
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            keyword_items: list[dict[str, Any]] = []
            keyword_total: int | None = None

            for page_no in range(1, _MAX_PAGES + 1):
                raw = self._fetch_page(context, page_no=page_no, keyword=keyword)
                data = raw.get("data") or {}
                items = data.get("list") or []

                if keyword_total is None:
                    try:
                        keyword_total = int(data.get("total") or 0)
                    except (TypeError, ValueError):
                        keyword_total = 0

                if not items:
                    break

                keyword_items.extend(items)
                if keyword_total and len(keyword_items) >= keyword_total:
                    break

            # Deduplicate this keyword's items against all collected items.
            for item in keyword_items:
                item_id = _str(item.get("postId") or item.get("jobId") or "")
                title = _str(item.get("name") or item.get("title") or "")
                location = _str(item.get("workPlace") or item.get("location") or "")
                key_tuple = (title, location)

                if item_id:
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                else:
                    if key_tuple in seen_key_tuples:
                        continue
                    seen_key_tuples.add(key_tuple)

                all_items.append(item)

        return self.parse({"status": "ok", "data": {"list": all_items}}, context)

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        data = raw.get("data") or {}
        items = data.get("list") or raw.get("list") or []

        results: list[CollectedJob] = []
        for item in items:
            external_id = _str(item.get("postId") or item.get("jobId") or "")
            title = _str(item.get("name") or item.get("title") or "")
            description = _build_description(item)
            location = _str(item.get("workPlace") or item.get("location") or "")
            job_type = _str(item.get("postType") or item.get("jobType") or "")
            published = _try_parse_date(
                _str(item.get("publishDate") or item.get("updateDate") or "")
            )

            url = _str(item.get("url") or "")
            if not url and external_id:
                url = (
                    "https://talent.baidu.com/jobs/social-detail"
                    f"?postId={external_id}"
                )

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


register("baidu_official", BaiduOfficialAdapter())
