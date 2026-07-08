"""iFlytek official careers adapter (BeiSen ATS portal).

iFlytek's official careers portal at https://iflytek.zhiye.com uses the BeiSen
ATS.  The API endpoint is:

    POST https://iflytek.zhiye.com/api/Jobad/GetJobAdPageList

with a JSON payload containing keyword and pagination fields.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary

_PAGE_SIZE = 100
_BASE_URL = "https://iflytek.zhiye.com"
_ENDPOINT = f"{_BASE_URL}/api/Jobad/GetJobAdPageList"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=utf-8",
    "X-Requested-With": "XMLHttpRequest",
    "langType": "zh_CN",
    "Referer": "https://iflytek.zhiye.com/social/jobs",
}

_DISPLAY_FIELDS = [
    "Category",
    "Kind",
    "LocId",
    "PostDate",
    "ClassificationOne",
    "ClassificationTwo",
    "WorkWeChatQrCode",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the job items from a BeiSen API response.

    Supports two response shapes:

    1. Real BeiSen API (verified live):
       ``{"Code": 200, "Count": N, "Data": [{...job...}], "Total": 0}``

    2. Legacy / nested fixture shape (still tolerated for tests):
       ``{"Data": {"Count": N, "Data": [{...job...}]}}``
    """
    data = raw.get("Data")
    if isinstance(data, list):
        # Real shape: top-level Data is the job list directly.
        return [item for item in data if isinstance(item, dict)]
    # Legacy shape: Data is {"Count": ..., "Data": [...]}.
    if isinstance(data, dict):
        items = data.get("Data")
        if not isinstance(items, list):
            items = data.get("list", [])
        return [item for item in items if isinstance(item, dict)]
    return []


def _build_payload(keyword: str, page_index: int) -> dict[str, Any]:
    return {
        "PageIndex": page_index,
        "PageSize": _PAGE_SIZE,
        "Category": ["1"],
        "KeyWords": keyword,
        "SpecialType": 0,
        "PortalId": "",
        "DisplayFields": _DISPLAY_FIELDS,
    }


def _external_id(item: dict[str, Any]) -> str:
    return _str(item.get("JobAdId") or item.get("Id") or "")


def _description(item: dict[str, Any]) -> str:
    """Combine Duty and Require into a structured description."""
    parts: list[str] = []
    duty = _str(item.get("Duty"))
    require = _str(item.get("Require"))
    if duty:
        parts.append("职责:\n" + duty)
    if require:
        parts.append("要求:\n" + require)
    return "\n\n".join(parts)


def _city_names(item: dict[str, Any]) -> str:
    names = item.get("LocNames") or []
    return "、".join(_str(n) for n in names if n)


def _job_type(item: dict[str, Any]) -> str:
    val = (
        item.get("ClassificationOne")
        or item.get("Kind")
        or ""
    )
    if val:
        return _str(val)
    category = item.get("Category")
    if isinstance(category, list):
        return "、".join(_str(c) for c in category)
    return _str(category or "")


def _published_at(item: dict[str, Any]) -> datetime | None:
    post_date = item.get("PostDate")
    if post_date:
        try:
            return datetime.fromisoformat(str(post_date))
        except (TypeError, ValueError):
            pass
    post_date_int = item.get("PostDateInt")
    if post_date_int is not None:
        try:
            ts = float(post_date_int)
            if ts > 1e12:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    return None


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _post_with_retry(
    url: str, *, json: dict[str, Any], headers: dict[str, str], max_retries: int = 2
) -> httpx.Response:
    """POST with retry on transient transport errors."""
    for attempt in range(max_retries + 1):
        try:
            response = httpx.post(url, json=json, headers=headers, timeout=30)
            if response.status_code >= 400:
                response.raise_for_status()
            return response
        except httpx.TransportError:
            if attempt == max_retries:
                raise
            time.sleep((attempt + 1) * 1.0)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class IFlyTekOfficialAdapter(BaseAdapter):
    """Adapter for iFlytek's BeiSen-powered official careers API."""

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch one page of the first keyword."""
        url = context.fetch_url or _ENDPOINT
        payload = _build_payload(TARGET_KEYWORDS[0], 0)
        response = _post_with_retry(url, json=payload, headers=_HEADERS)
        return response.json()

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Paginate through all target keywords, deduplicate, and collect."""
        url = context.fetch_url or _ENDPOINT
        seen_ids: set[str] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            page_index = 0
            keyword_item_count = 0

            while True:
                payload = _build_payload(keyword, page_index)

                try:
                    response = _post_with_retry(url, json=payload, headers=_HEADERS)
                    raw = response.json()
                except httpx.HTTPError:
                    break

                data = raw.get("Data")
                if isinstance(data, list):
                    # Real API shape: top-level Data is the job list,
                    # top-level Count holds the total.
                    items = data
                    count = raw.get("Count", 0)
                elif isinstance(data, dict):
                    # Legacy nested shape.
                    items = data.get("Data")
                    count = data.get("Count", 0)
                else:
                    items = None
                    count = 0

                if not isinstance(items, list) or not items:
                    break

                keyword_item_count += len(items)

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_id = _external_id(item)
                    if item_id and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        all_items.append(item)
                    elif not item_id:
                        # Items without ID are always appended (rare).
                        all_items.append(item)

                # Stop when we have reached the advertised total.
                if count and keyword_item_count >= count:
                    break
                # Stop when the page is not full (last page).
                if len(items) < _PAGE_SIZE:
                    break

                page_index += 1

        return self.parse({"Data": {"Data": all_items}}, context)

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        """Parse a BeiSen API response into :class:`CollectedJob` items."""
        base_url = (context.base_url or _BASE_URL).rstrip("/")
        jobs: list[CollectedJob] = []

        for item in _items(raw):
            eid = _external_id(item)
            title = _str(item.get("JobAdName") or "")
            description = _description(item)
            location = _city_names(item)
            jt = _job_type(item)

            # Stable URL using JobAdId.
            url = ""
            if eid:
                url = f"{base_url}/social/detail?jobAdId={eid}"

            # Salary: only parse when non-empty; null/empty means undisclosed.
            salary_raw = item.get("Salary")
            salary = parse_salary(salary_raw if salary_raw else None)

            published = _published_at(item)
            tags = classify_job(title, description, jt)

            jobs.append(
                CollectedJob(
                    external_id=eid,
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
                    job_type=jt,
                    published_at=published,
                    matched_tags=tags,
                )
            )

        return jobs


register("iflytek_official", IFlyTekOfficialAdapter())
