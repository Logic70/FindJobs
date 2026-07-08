"""Ant Group official social recruitment adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_FETCH_URL = "https://hrcareersweb.antgroup.com/api/social/position/search"
_DETAIL_BASE_URL = "https://talent.antgroup.com/off-campus-position"
_PAGE_SIZE = 20
_MAX_PAGES = 60
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://talent.antgroup.com",
    "Referer": "https://talent.antgroup.com/off-campus",
}
_RESPONSIBILITY_LABEL = "\u804c\u8d23:\n"
_REQUIREMENT_LABEL = "\u8981\u6c42:\n"
_LIST_JOINER = "\u3001"


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _list_text(value: Any) -> str:
    if isinstance(value, list):
        return _LIST_JOINER.join(_str(item) for item in value if _str(item))
    return _str(value)


def _parse_datetime(value: Any) -> datetime | None:
    text = _str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _items_from_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    content = raw.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if isinstance(content, dict):
        items = content.get("datas") or content.get("list") or content.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    items = raw.get("datas") or raw.get("list") or raw.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _total_count(raw: Any) -> int | None:
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    candidates: list[Any] = [raw.get("totalCount"), raw.get("total")]
    if isinstance(content, dict):
        candidates.extend([content.get("totalCount"), content.get("total")])
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _build_payload(page_no: int, page_size: int = _PAGE_SIZE) -> dict[str, Any]:
    return {
        "key": "",
        "regions": "",
        "categories": "",
        "subCategories": "",
        "bgCode": "",
        "socialQrCode": "",
        "pageIndex": page_no,
        "pageSize": page_size,
        "channel": "group_official_site",
        "language": "zh",
    }


def _build_description(item: dict[str, Any]) -> str:
    responsibility = _str(item.get("description"))
    requirement = _str(item.get("requirement"))
    parts: list[str] = []
    if responsibility:
        parts.append(_RESPONSIBILITY_LABEL + responsibility)
    if requirement:
        parts.append(_REQUIREMENT_LABEL + requirement)
    return "\n\n".join(parts)


def _job_url(item: dict[str, Any], context: AdapterContext) -> str:
    base_url = (context.base_url or "https://talent.antgroup.com").rstrip("/")
    position_url = _str(item.get("positionUrl"))
    if position_url:
        return urljoin(base_url + "/", position_url)
    job_id = _str(item.get("id"))
    return f"{_DETAIL_BASE_URL}?positionId={job_id}" if job_id else base_url


def _identity(item: dict[str, Any]) -> tuple[str, str]:
    external_id = _str(item.get("id"))
    if external_id:
        return ("id", external_id)
    return (
        "title_location",
        f"{_str(item.get('name'))}\0{_list_text(item.get('workLocations'))}",
    )


class AntGroupOfficialAdapter(BaseAdapter):
    """Adapter for Ant Group's official social recruitment API."""

    def _fetch_page(self, page_no: int, context: AdapterContext) -> dict[str, Any]:
        import httpx

        url = context.fetch_url or _FETCH_URL
        separator = "&" if "?" in url else "?"
        if "ctoken=" not in url:
            url = f"{url}{separator}ctoken=bigfish_ctoken_findjobs"
        response = httpx.post(
            url,
            json=_build_payload(page_no),
            headers=_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        raw = response.json()
        if isinstance(raw, dict) and raw.get("success") is False:
            raise ValueError(_str(raw.get("errorMsg")) or "Ant Group API returned failure")
        return raw

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        return self._fetch_page(1, context)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        all_items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for page_no in range(1, _MAX_PAGES + 1):
            raw = self._fetch_page(page_no, context)
            items = _items_from_raw(raw)
            if not items:
                break

            for item in items:
                key = _identity(item)
                if key in seen:
                    continue
                seen.add(key)
                all_items.append(item)

            total = _total_count(raw)
            if total is not None and len(all_items) >= total:
                break
            if len(items) < _PAGE_SIZE:
                break

        return self.parse(all_items, context)

    def parse(
        self, raw: dict[str, Any] | list[dict[str, Any]], context: AdapterContext
    ) -> list[CollectedJob]:
        jobs: list[CollectedJob] = []
        for item in _items_from_raw(raw):
            title = _str(item.get("name"))
            description = _build_description(item)
            job_type = _list_text(item.get("categories"))
            salary = parse_salary(
                _str(item.get("salary") or item.get("salaryText") or item.get("salaryRange"))
            )
            tags = classify_job(title, description, job_type)

            jobs.append(
                CollectedJob(
                    external_id=_str(item.get("id")),
                    title=title,
                    url=_job_url(item, context),
                    description=description,
                    salary_text=salary["salary_text"],
                    salary_min=salary["salary_min"],
                    salary_max=salary["salary_max"],
                    salary_currency=salary["salary_currency"],
                    salary_period=salary["salary_period"],
                    salary_disclosed=salary["salary_disclosed"],
                    location=_list_text(item.get("workLocations")),
                    job_type=job_type,
                    published_at=_parse_datetime(item.get("publishTime")),
                    matched_tags=tags,
                )
            )

        return jobs


register("antgroup_official", AntGroupOfficialAdapter())
