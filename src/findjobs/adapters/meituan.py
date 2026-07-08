"""Meituan official recruitment adapter."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_PAGE_SIZE = 50
_MAX_PAGES = 50
_DETAIL_MAX_WORKERS = 8
_LIST_URL = "https://zhaopin.meituan.com/api/official/job/getJobList"
_DETAIL_URL = "https://zhaopin.meituan.com/api/official/job/getJobDetail"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://zhaopin.meituan.com",
    "Referer": "https://zhaopin.meituan.com/web/social?keyword=%E5%AE%89%E5%85%A8",
}


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _timestamp_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).replace(
            tzinfo=None
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data = raw.get("data") or {}
    values = data.get("list") or raw.get("list") or []
    return [item for item in values if isinstance(item, dict)]


def _total_page(raw: dict[str, Any]) -> int | None:
    page = (raw.get("data") or {}).get("page") or {}
    try:
        return int(page.get("totalPage"))
    except (TypeError, ValueError):
        return None


def _build_payload(page_no: int, *, keyword: str | None = None) -> dict[str, Any]:
    return {
        "page": {"pageNo": page_no, "pageSize": _PAGE_SIZE},
        "keywords": keyword or TARGET_KEYWORDS[0],
    }


def _city_names(item: dict[str, Any]) -> str:
    cities = item.get("cityList") or []
    names = [_str(city.get("name")) for city in cities if isinstance(city, dict)]
    return "、".join(name for name in names if name)


def _job_type(item: dict[str, Any]) -> str:
    family_group = _str(item.get("jobFamilyGroup"))
    family = _str(item.get("jobFamily"))
    if family_group and family:
        return f"{family_group}/{family}"
    return family or family_group or _str(item.get("jobType"))


def _description(item: dict[str, Any]) -> str:
    parts: list[str] = []
    duty = _str(item.get("jobDuty"))
    requirement = _str(item.get("jobRequirement"))
    highlight = _str(item.get("highLight"))
    if duty:
        parts.append("职责:\n" + duty)
    if requirement:
        parts.append("要求:\n" + requirement)
    if highlight:
        parts.append("亮点:\n" + highlight)
    return "\n\n".join(parts)


def _should_fetch_detail(item: dict[str, Any]) -> bool:
    title = _str(item.get("name"))
    description = _description(item)
    job_type = _job_type(item)
    return bool(classify_job(title, description, job_type))


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


class MeituanOfficialAdapter(BaseAdapter):
    """Adapter for Meituan's official recruitment API."""

    def _fetch_page(
        self, context: AdapterContext, page_no: int, *, keyword: str | None = None
    ) -> dict[str, Any]:
        url = context.fetch_url or _LIST_URL
        response = _post_with_retry(
            url, json=_build_payload(page_no, keyword=keyword), headers=_HEADERS
        )
        raw = response.json()
        if raw.get("status") not in (None, 1, "1"):
            raise ValueError(
                "Meituan API returned failure: "
                f"{raw.get('message') or raw.get('status')}"
            )
        return raw

    def _fetch_detail(
        self, context: AdapterContext, job_union_id: str
    ) -> dict[str, Any] | None:
        if not job_union_id:
            return None
        detail_url = _DETAIL_URL
        if context.base_url:
            url = context.base_url.rstrip("/")
            detail_url = f"{url}/api/official/job/getJobDetail"
        headers = {
            **_HEADERS,
            "Referer": (
                "https://zhaopin.meituan.com/web/position/detail"
                f"?jobUnionId={job_union_id}&jobShareType=1&highlightType=social"
            ),
        }
        try:
            response = _post_with_retry(
                detail_url, json={"jobUnionId": job_union_id}, headers=headers
            )
        except httpx.HTTPError:
            return None
        raw = response.json()
        if raw.get("status") not in (None, 1, "1"):
            return None
        data = raw.get("data")
        return data if isinstance(data, dict) else None

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        return self._fetch_page(context, page_no=1)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_unique: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            keyword_items: list[dict[str, Any]] = []
            for page_no in range(1, _MAX_PAGES + 1):
                raw = self._fetch_page(context, page_no=page_no, keyword=keyword)
                page_items = _items(raw)
                if not page_items:
                    break
                keyword_items.extend(page_items)
                total_page = _total_page(raw)
                if total_page is not None and page_no >= total_page:
                    break

            for item in keyword_items:
                item_id = _str(item.get("jobUnionId"))
                title = _str(item.get("name"))
                location = _city_names(item)
                key_tuple = (title, location)

                if item_id:
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                else:
                    if key_tuple in seen_key_tuples:
                        continue
                    seen_key_tuples.add(key_tuple)

                all_unique.append(item)

        # Fetch details only for likely relevant unique jobs after deduplication.
        detailed = list(all_unique)
        detail_targets = [
            (index, _str(item.get("jobUnionId")))
            for index, item in enumerate(all_unique)
            if item.get("jobUnionId") and _should_fetch_detail(item)
        ]
        if detail_targets:
            worker_count = min(_DETAIL_MAX_WORKERS, len(detail_targets))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                details = executor.map(
                    lambda job_id: self._fetch_detail(context, job_id),
                    [job_id for _, job_id in detail_targets],
                )
                for (index, _), detail in zip(detail_targets, details):
                    if detail:
                        detailed[index] = {**detailed[index], **detail}

        return self.parse({"data": {"list": detailed}}, context)

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        jobs: list[CollectedJob] = []
        for item in _items(raw):
            external_id = _str(item.get("jobUnionId"))
            title = _str(item.get("name"))
            description = _description(item)
            url = _str(item.get("url"))
            if not url and external_id:
                url = (
                    "https://zhaopin.meituan.com/web/position/detail"
                    f"?jobUnionId={external_id}&jobShareType=1&highlightType=social"
                )
            salary = parse_salary(None)
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
                    location=_city_names(item),
                    job_type=_job_type(item),
                    published_at=_timestamp_ms(
                        item.get("firstPostTime") or item.get("refreshTime")
                    ),
                    matched_tags=classify_job(title, description, _job_type(item)),
                )
            )
        return jobs


register("meituan_official", MeituanOfficialAdapter())
