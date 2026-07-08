"""NetEase official career-page adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_QUERY_PAGE_SIZE = 50
"""Number of items per NetEase API page."""

_MAX_PAGES = 20
"""Maximum page count per keyword (NetEase API does not expose a total field)."""

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://hr.163.com/",
}


class NetEaseOfficialAdapter(BaseAdapter):
    """Adapter for NetEase's official HR API."""

    def _fetch_page(
        self, context: AdapterContext, page_no: int, keyword: str
    ) -> dict[str, Any]:
        """Fetch one page for the given keyword and page number."""
        import httpx

        url = context.fetch_url or context.base_url
        resp = httpx.post(
            url,
            json={
                "currentPage": page_no,
                "pageSize": _QUERY_PAGE_SIZE,
                "keyword": keyword,
            },
            headers=_HEADERS,
            timeout=30,
        )
        if resp.status_code >= 400:
            resp.raise_for_status()
        return resp.json()

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch first page of the first target keyword (backward-compatible)."""
        return self._fetch_page(context, page_no=1, keyword=TARGET_KEYWORDS[0])

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Collect across all target keywords, paginating and deduplicating.

        The NetEase API does not expose a total-count field, so pagination
        stops when a page returns fewer items than the page size (short page)
        or when ``_MAX_PAGES`` is reached.
        """
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            for page_no in range(1, _MAX_PAGES + 1):
                raw = self._fetch_page(context, page_no=page_no, keyword=keyword)
                data = raw.get("data") or {}
                items = data.get("list") or []

                if not items:
                    break

                for item in items:
                    job_id = str(item.get("id") or "")
                    title = str(item.get("name") or "").strip()
                    location_names = item.get("workPlaceNameList") or []
                    location = "、".join(str(v) for v in location_names if v)
                    key_tuple = (title, location)

                    if job_id:
                        if job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)
                    else:
                        if key_tuple in seen_key_tuples:
                            continue
                        seen_key_tuples.add(key_tuple)

                    all_items.append(item)

                # Stop on short page (less than page size = last page).
                if len(items) < _QUERY_PAGE_SIZE:
                    break

        return self.parse({"data": {"list": all_items}}, context)

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        """Parse the NetEase HR API response into collected jobs."""
        data = raw.get("data") or {}
        jobs_list = data.get("list") or []
        base_url = (context.base_url or "https://hr.163.com").rstrip("/")

        results: list[CollectedJob] = []
        for item in jobs_list:
            job_id = item.get("id")
            external_id = str(job_id) if job_id is not None else ""
            title = str(item.get("name") or "").strip()
            url = f"{base_url}/job/{external_id}" if external_id else ""

            requirement = str(item.get("requirement") or "").strip()
            description = str(item.get("description") or "").strip()
            description_parts = []
            if requirement:
                description_parts.append("岗位要求:\n" + requirement)
            if description:
                description_parts.append("岗位描述:\n" + description)
            combined_description = "\n\n".join(description_parts)

            location_names = item.get("workPlaceNameList") or []
            location = "、".join(str(v) for v in location_names if v)
            job_type = str(item.get("firstPostTypeName") or "")

            published = None
            update_time_ms = item.get("updateTime")
            if update_time_ms is not None:
                try:
                    published = datetime.fromtimestamp(
                        float(update_time_ms) / 1000.0, tz=timezone.utc
                    ).replace(tzinfo=None)
                except (TypeError, ValueError, OSError, OverflowError):
                    published = None

            salary = parse_salary(None)
            tags = classify_job(title, combined_description, job_type)

            results.append(
                CollectedJob(
                    external_id=external_id,
                    title=title,
                    url=url,
                    description=combined_description,
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


register("netease_official", NetEaseOfficialAdapter())
