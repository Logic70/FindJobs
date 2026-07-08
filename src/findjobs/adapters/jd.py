"""JD official recruitment adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_PAGE_SIZE = 10
_MAX_PAGES = 50
_LIST_URL = "https://zhaopin.jd.com/web/job/job_list"
_COUNT_URL = "https://zhaopin.jd.com/web/job/job_count"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://zhaopin.jd.com/web/job/job_info_list/3",
}


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _extract_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    data = raw.get("data") or raw.get("Data") or raw
    items = data.get("list") or data.get("List") or data.get("jobs") or []
    return [item for item in items if isinstance(item, dict)]


def _external_id(item: dict[str, Any]) -> str:
    return _str(
        item.get("requirementId")
        or item.get("positionId")
        or item.get("reqNumber")
        or item.get("id")
        or item.get("positionNameOpen")
        or item.get("positionName")
    )


def _build_payload(page_no: int, *, keyword: str | None = None) -> dict[str, str]:
    return {
        "pageIndex": str(page_no),
        "pageSize": str(_PAGE_SIZE),
        "workCityJson": "[]",
        "jobTypeJson": "[]",
        "jobSearch": keyword or TARGET_KEYWORDS[0],
        "depTypeJson": "[]",
    }


def _build_description(item: dict[str, Any]) -> str:
    responsibility = _str(item.get("workContent") or item.get("description"))
    requirement = _str(item.get("qualification") or item.get("requirement"))

    parts: list[str] = []
    if responsibility:
        parts.append("职责:\n" + responsibility)
    if requirement:
        parts.append("要求:\n" + requirement)
    return "\n\n".join(parts)


class JDOfficialAdapter(BaseAdapter):
    """Adapter for JD's official recruitment API."""

    def _count_url(self, context: AdapterContext) -> str:
        if context.fetch_url and "job_list" in context.fetch_url:
            return context.fetch_url.replace("job_list", "job_count")
        return _COUNT_URL

    def _fetch_count(
        self, context: AdapterContext, *, keyword: str | None = None
    ) -> int | None:
        import httpx

        response = httpx.post(
            self._count_url(context),
            data=_build_payload(1, keyword=keyword),
            headers=_HEADERS,
            timeout=30,
        )
        if response.status_code >= 400:
            response.raise_for_status()
        try:
            raw: Any = response.json()
        except ValueError:
            raw = response.text

        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            raw = raw.strip()
            return int(raw) if raw.isdigit() else None
        if isinstance(raw, dict):
            for key in ("data", "count", "total", "jobCount"):
                try:
                    return int(raw[key])
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _fetch_page(
        self, context: AdapterContext, page_no: int, *, keyword: str | None = None
    ) -> Any:
        import httpx

        url = context.fetch_url or _LIST_URL
        response = httpx.post(
            url,
            data=_build_payload(page_no, keyword=keyword),
            headers=_HEADERS,
            timeout=30,
        )
        if response.status_code >= 400:
            response.raise_for_status()
        return response.json()

    def fetch(self, context: AdapterContext) -> Any:
        return self._fetch_page(context, page_no=1)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            keyword_items: list[dict[str, Any]] = []
            total = self._fetch_count(context, keyword=keyword)

            for page_no in range(1, _MAX_PAGES + 1):
                raw = self._fetch_page(context, page_no=page_no, keyword=keyword)
                items = _extract_items(raw)
                if not items:
                    break

                for item in items:
                    item_id = _external_id(item)
                    title = _str(
                        item.get("positionNameOpen") or item.get("positionName")
                    )
                    location = _str(
                        item.get("workCity") or item.get("workCityName")
                    )
                    key_tuple = (title, location)

                    if item_id:
                        if item_id in seen_ids:
                            continue
                        seen_ids.add(item_id)
                    else:
                        if key_tuple in seen_key_tuples:
                            continue
                        seen_key_tuples.add(key_tuple)

                    keyword_items.append(item)

                if total is not None and len(keyword_items) >= total:
                    break

            all_items.extend(keyword_items)

        return self.parse(all_items, context)

    def parse(
        self, raw: dict[str, Any] | list[dict[str, Any]], context: AdapterContext
    ) -> list[CollectedJob]:
        jobs: list[CollectedJob] = []
        for item in _extract_items(raw):
            external_id = _external_id(item)
            title = _str(item.get("positionNameOpen") or item.get("positionName"))
            description = _build_description(item)
            location = _str(item.get("workCity") or item.get("workCityName"))
            job_type = _str(item.get("jobType") or item.get("jobTypeName"))
            published = _parse_date(_str(item.get("formatPublishTime")))
            url = _str(item.get("url"))
            if not url and external_id:
                url = (
                    "https://zhaopin.jd.com/web/job-info-detail"
                    f"?requementId={external_id}"
                )

            salary = parse_salary(None)
            tags = classify_job(title, description, job_type)

            jobs.append(
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

        return jobs


register("jd_official", JDOfficialAdapter())
