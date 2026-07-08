"""Kuaishou official recruitment adapter."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_PAGE_SIZE = 10
_MAX_PAGES = 50
_SECRET = "652f962a-0575-4575-98d2-f04e2291bee2"
_LIST_URL = (
    "https://zhaopin.kuaishou.cn/recruit/e/api/v1/open/positions/simple"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://zhaopin.kuaishou.cn/recruit/e/#/official/social/",
}


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _build_params(page_no: int, *, keyword: str | None = None) -> dict[str, Any]:
    return {
        "name": keyword or TARGET_KEYWORDS[0],
        "pageNum": page_no,
        "pageSize": _PAGE_SIZE,
        "positionNatureCode": "C001",
    }


def _canonical_query(params: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if value is None or value == "":
            continue
        parts.append(f"{key}={quote(str(value), safe='')}")
    return "&".join(parts)


def _signature_headers(params: dict[str, Any]) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    payload = timestamp + _canonical_query(params) + _SECRET
    digest = hmac.new(
        _SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"sign": digest, "signTimestamp": timestamp}


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result = raw.get("result") or {}
    values = result.get("list") or raw.get("list") or []
    return [item for item in values if isinstance(item, dict)]


def _total(raw: dict[str, Any]) -> int:
    try:
        return int((raw.get("result") or {}).get("total") or 0)
    except (TypeError, ValueError):
        return 0


def _location(item: dict[str, Any]) -> str:
    locations = item.get("workLocationsCode") or []
    if isinstance(locations, list) and locations:
        return "、".join(_str(value) for value in locations if value)
    return _str(item.get("workLocationCode"))


def _description(item: dict[str, Any]) -> str:
    responsibility = _str(item.get("description"))
    requirement = _str(item.get("positionDemand"))
    parts: list[str] = []
    if responsibility:
        parts.append("职责:\n" + responsibility)
    if requirement:
        parts.append("要求:\n" + requirement)
    return "\n\n".join(parts)


class KuaishouOfficialAdapter(BaseAdapter):
    """Adapter for Kuaishou's signed official recruitment API."""

    def _fetch_page(
        self, context: AdapterContext, page_no: int, *, keyword: str | None = None
    ) -> dict[str, Any]:
        import httpx

        url = context.fetch_url or _LIST_URL
        params = _build_params(page_no, keyword=keyword)
        headers = {**_HEADERS, **_signature_headers(params)}
        response = httpx.get(url, params=params, headers=headers, timeout=30)
        if response.status_code >= 400:
            response.raise_for_status()
        raw = response.json()
        if raw.get("code") not in (0, "0"):
            raise ValueError(
                "Kuaishou API returned failure: "
                f"{raw.get('message') or raw.get('code')}"
            )
        return raw

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        return self._fetch_page(context, page_no=1)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            keyword_items: list[dict[str, Any]] = []
            total = 0
            for page_no in range(1, _MAX_PAGES + 1):
                raw = self._fetch_page(context, page_no=page_no, keyword=keyword)
                page_items = _items(raw)
                if total == 0:
                    total = _total(raw)
                if not page_items:
                    break
                keyword_items.extend(page_items)
                if total and len(keyword_items) >= total:
                    break

            for item in keyword_items:
                item_id = _str(item.get("id"))
                title = _str(item.get("name"))
                location = _location(item)
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

        return self.parse({"code": 0, "result": {"list": all_items}}, context)

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        jobs: list[CollectedJob] = []
        for item in _items(raw):
            external_id = _str(item.get("id"))
            title = _str(item.get("name"))
            description = _description(item)
            url = _str(item.get("url"))
            if not url and external_id:
                url = (
                    "https://zhaopin.kuaishou.cn/recruit/e/#/official/social/"
                    f"job-info/{external_id}"
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
                    location=_location(item),
                    job_type=_str(item.get("positionCategoryCode")),
                    published_at=_parse_datetime(_str(item.get("updateTime"))),
                    matched_tags=classify_job(
                        title,
                        description,
                        _str(item.get("positionCategoryCode")),
                    ),
                )
            )
        return jobs


register("kuaishou_official", KuaishouOfficialAdapter())
