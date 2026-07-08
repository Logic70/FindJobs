"""Alibaba business-unit official recruitment adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_PAGE_SIZE = 50
_MAX_PAGES = 25
_MAX_KEYWORD_PAGES = 25
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
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


def _parse_publish_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).replace(
                tzinfo=None
            )
        except (OSError, OverflowError, ValueError):
            return None
    text = _str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _base_url(context: AdapterContext) -> str:
    return (context.base_url or "https://talent.alibaba.com").rstrip("/")


def _list_url(context: AdapterContext) -> str:
    return urljoin(_base_url(context) + "/", "off-campus/position-list?lang=zh")


def _search_url(context: AdapterContext, csrf_token: str) -> str:
    if context.fetch_url:
        base = context.fetch_url
    else:
        base = urljoin(_base_url(context) + "/", "position/search")
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}_csrf={quote(csrf_token)}"


def _build_payload(
    page_no: int, page_size: int = _PAGE_SIZE, keyword: str = ""
) -> dict[str, Any]:
    return {
        "channel": "group_official_site",
        "language": "zh",
        "batchId": "",
        "categories": "",
        "deptCodes": [],
        "key": keyword,
        "pageIndex": page_no,
        "pageSize": page_size,
        "regions": "",
        "subCategories": "",
        "shareType": "",
        "shareId": "",
        "myReferralShareCode": "",
    }


def _items_from_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    content = raw.get("content")
    if isinstance(content, dict):
        items = content.get("datas") or content.get("list") or content.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
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
    base_url = _base_url(context)
    position_url = _str(item.get("positionUrl"))
    if position_url:
        return urljoin(base_url + "/", position_url)
    job_id = _str(item.get("id"))
    if job_id:
        return urljoin(base_url + "/", f"off-campus/position-detail?positionId={job_id}")
    return base_url


def _identity(item: dict[str, Any]) -> tuple[str, str]:
    external_id = _str(item.get("id"))
    if external_id:
        return ("id", external_id)
    return (
        "title_location",
        f"{_str(item.get('name'))}\0{_list_text(item.get('workLocations'))}",
    )


class AlibabaGroupOfficialAdapter(BaseAdapter):
    """Adapter for Alibaba's official business-unit careers portals."""

    def _fetch_csrf_token(self, client: Any, context: AdapterContext) -> str:
        response = client.get(
            _list_url(context),
            headers={**_HEADERS, "Accept": "text/html,*/*"},
        )
        response.raise_for_status()
        token = client.cookies.get("XSRF-TOKEN")
        if not token:
            token = response.cookies.get("XSRF-TOKEN")
        if not token:
            raise ValueError("Alibaba careers XSRF-TOKEN cookie not found")
        return _str(token)

    def _fetch_page(
        self,
        client: Any,
        *,
        context: AdapterContext,
        csrf_token: str,
        page_no: int,
        keyword: str = "",
    ) -> dict[str, Any]:
        base_url = _base_url(context)
        response = client.post(
            _search_url(context, csrf_token),
            json=_build_payload(page_no, keyword=keyword),
            headers={**_HEADERS, "Origin": base_url, "Referer": _list_url(context)},
        )
        response.raise_for_status()
        raw = response.json()
        if isinstance(raw, dict) and raw.get("success") is False:
            raise ValueError(_str(raw.get("errorMsg")) or "Alibaba API returned failure")
        return raw

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=30, follow_redirects=True, headers=_HEADERS) as client:
            csrf_token = self._fetch_csrf_token(client, context)
            return self._fetch_page(
                client, context=context, csrf_token=csrf_token, page_no=1
            )

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        import httpx

        all_items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        last_total: int | None = None

        def add_items(items: list[dict[str, Any]]) -> None:
            for item in items:
                key = _identity(item)
                if key in seen:
                    continue
                seen.add(key)
                all_items.append(item)

        with httpx.Client(timeout=30, follow_redirects=True, headers=_HEADERS) as client:
            csrf_token = self._fetch_csrf_token(client, context)
            for page_no in range(1, _MAX_PAGES + 1):
                raw = self._fetch_page(
                    client, context=context, csrf_token=csrf_token, page_no=page_no
                )
                items = _items_from_raw(raw)
                if not items:
                    break
                add_items(items)

                total = _total_count(raw)
                last_total = total
                if total is not None and len(all_items) >= total:
                    break
                if len(items) < _PAGE_SIZE:
                    break

            # Some Alibaba sub-sites cap blank search at 500 results even when
            # totalCount is higher.  Supplement with target-keyword scans so
            # AI/Security jobs beyond the blank cap are still discovered.
            if last_total is not None and len(all_items) < last_total:
                for keyword in TARGET_KEYWORDS:
                    for page_no in range(1, _MAX_KEYWORD_PAGES + 1):
                        raw = self._fetch_page(
                            client,
                            context=context,
                            csrf_token=csrf_token,
                            page_no=page_no,
                            keyword=keyword,
                        )
                        items = _items_from_raw(raw)
                        if not items:
                            break
                        add_items(items)

                        keyword_total = _total_count(raw)
                        if keyword_total is not None and page_no * _PAGE_SIZE >= keyword_total:
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
                    published_at=_parse_publish_time(item.get("publishTime")),
                    matched_tags=tags,
                )
            )

        return jobs


register("alibaba_group_official", AlibabaGroupOfficialAdapter())
