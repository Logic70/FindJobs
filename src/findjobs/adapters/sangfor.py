"""Sangfor official recruitment adapter.

https://hr.sangfor.com/Sociology
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob


_TOKEN_URL = "https://hr.sangfor.com/webapi/api/connect/token"
_LIST_URL = "https://hr.sangfor.com/webapi/api/Jobs"
_CAREERS_URL = "https://hr.sangfor.com/Sociology"
_DELIVERY_BASE = "https://hr.sangfor.com/Delivery"
_PAGE_SIZE = 100
_MAX_PAGES_PER_KEYWORD = 10
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://hr.sangfor.com",
    "Referer": _CAREERS_URL,
}


def _request_with_retry(
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Issue an HTTP request with bounded retry for transport / 5xx errors.

    Retries up to ``_MAX_RETRIES`` times on transport errors and server
    (5xx) status codes.  Client errors (4xx) are surfaced immediately.
    Uses exponential backoff (1, 2, 4 seconds).
    """
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.request(method, url, **kwargs)
            # Retry on 5xx server errors (do not raise_for_status yet).
            if resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            resp.raise_for_status()
            return resp
        except httpx.TransportError:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            raise

    raise RuntimeError("Unexpected: retry loop exhausted")


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
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


class _TextExtractor(HTMLParser):
    """Strip HTML preserving section structure for description."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"p", "br", "li", "ol", "ul", "div"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = html.unescape(data).strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        collapsed = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._parts))
        collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
        return collapsed.strip()


def _strip_html(value: str) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()


def _items_from_raw(raw: Any) -> list[dict[str, Any]]:
    """Extract items from Sangfor API response, tolerating common nesting."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    data = raw.get("data") or raw.get("result")
    if isinstance(data, dict):
        items = (
            data.get("listData")
            or data.get("items")
            or data.get("list")
            or data.get("datas")
            or []
        )
        if items:
            return [item for item in items if isinstance(item, dict)]
    items = raw.get("items") or raw.get("list") or raw.get("datas") or []
    return [item for item in items if isinstance(item, dict)]


def _total_count(raw: Any) -> int | None:
    """Extract total count from the response."""
    if not isinstance(raw, dict):
        return None
    candidates: list[Any] = [raw.get("count")]
    data = raw.get("data") or raw.get("result")
    if isinstance(data, dict):
        candidates.extend([data.get("count"), data.get("total")])
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _build_payload(page: int, keyword: str) -> dict[str, Any]:
    return {
        "channelId": 110,
        "page": page,
        "pageSize": _PAGE_SIZE,
        "departmentId": 0,
        "functionId": 0,
        "kw": keyword,
        "locationId": 0,
        "workPlaceId": 0,
    }


def _build_description(item: dict[str, Any]) -> str:
    """Strip HTML description to plain text."""
    description_html = _str(item.get("description"))
    if not description_html:
        return ""
    return _strip_html(description_html)


class SangforOfficialAdapter(BaseAdapter):
    """Adapter for Sangfor's official careers API."""

    def _fetch_token(self) -> str:
        """Acquire a Bearer token from the Sangfor token endpoint."""
        resp = _request_with_retry("GET", _TOKEN_URL, headers=_HEADERS, timeout=30)
        data = resp.json()
        token = _str(data.get("access_token"))
        if not token:
            raise ValueError("Sangfor token endpoint returned no access_token")
        return token

    def _fetch_page(
        self,
        *,
        token: str,
        page: int,
        keyword: str,
        context: AdapterContext | None = None,
    ) -> dict[str, Any]:
        """Fetch one page of jobs for the given keyword.

        Uses *context.fetch_url* when supplied, falling back to ``_LIST_URL``.
        Validates that the API's top-level ``code`` is ``0``.
        """
        headers = dict(_HEADERS)
        headers["Authorization"] = f"Bearer {token}"

        list_url = context.fetch_url if (context and context.fetch_url) else _LIST_URL

        resp = _request_with_retry(
            "POST",
            list_url,
            headers=headers,
            json=_build_payload(page, keyword),
            timeout=30,
        )
        raw = resp.json()
        if raw.get("code") != 0:
            raise ValueError(
                f"Sangfor API returned error code {raw.get('code')}: "
                f"{raw.get('message', '')}"
            )
        return raw

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch first page of first target keyword (backward-compatible)."""
        token = self._fetch_token()
        return self._fetch_page(
            token=token, page=1, keyword=TARGET_KEYWORDS[0], context=context
        )

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Collect across all target keywords, paginating and deduplicating.

        Acquires one token per collect call (not one per page).
        Iterates all TARGET_KEYWORDS, paginating each by total count or
        short-page stop with a defensive max-page guard.

        Deduplicates by positionId with fallback to title+location tuple.
        """
        token = self._fetch_token()
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        all_items: list[dict[str, Any]] = []

        for keyword in TARGET_KEYWORDS:
            keyword_items: list[dict[str, Any]] = []

            for page_no in range(1, _MAX_PAGES_PER_KEYWORD + 1):
                raw = self._fetch_page(
                    token=token, page=page_no, keyword=keyword, context=context
                )
                items = _items_from_raw(raw)

                if not items:
                    break

                keyword_items.extend(items)

                total = _total_count(raw)
                if total is not None and len(keyword_items) >= total:
                    break
                if len(items) < _PAGE_SIZE:
                    break

            # Dedup this keyword's items against all collected items.
            for item in keyword_items:
                item_id = _str(item.get("positionId"))
                title = _str(item.get("title"))
                location = _str(item.get("workPlaceText"))
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

        return self.parse(all_items, context)

    def parse(
        self, raw: dict[str, Any] | list[dict[str, Any]], context: AdapterContext
    ) -> list[CollectedJob]:
        """Parse Sangfor API response into collected jobs.

        Salary is only recorded when *both* minSalary and maxSalary are
        positive (non-zero).  Zero / missing / negative values are all
        treated as undisclosed.

        Job type is built from the real API keys:
        ``functionName`` / ``departmentName`` / ``commitment``.
        """
        items = raw if isinstance(raw, list) else _items_from_raw(raw)

        results: list[CollectedJob] = []
        for item in items:
            external_id = _str(item.get("positionId"))
            title = _str(item.get("title"))
            description = _build_description(item)
            location = _str(item.get("workPlaceText") or "")

            # Job type: combine real Sangfor API fields with slash separators.
            fn = _str(item.get("functionName") or "")
            dn = _str(item.get("departmentName") or "")
            cm = _str(item.get("commitment") or "")
            job_type = " / ".join(p for p in [fn, dn, cm] if p)

            # Official detail URL (verified format).
            url = _CAREERS_URL
            if external_id:
                url = f"{_DELIVERY_BASE}/{external_id}"

            # Salary: only when BOTH bounds are positive.
            # Partial / invalid / zero / negative values → undisclosed
            # with both min and max set to None.
            try:
                raw_min = float(item.get("minSalary") or 0)
                raw_max = float(item.get("maxSalary") or 0)
            except (TypeError, ValueError):
                raw_min = 0.0
                raw_max = 0.0

            salary_disclosed = raw_min > 0 and raw_max > 0
            if salary_disclosed:
                salary_min = raw_min
                salary_max = raw_max
                salary_text = f"{raw_min:.0f}-{raw_max:.0f}"
            else:
                salary_min = None
                salary_max = None
                salary_text = ""

            published_at = _parse_datetime(
                _str(item.get("openedAt") or item.get("appendTime") or "")
            )

            tags = classify_job(title, description, job_type)

            results.append(
                CollectedJob(
                    external_id=external_id,
                    title=title,
                    url=url,
                    description=description,
                    salary_text=salary_text,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency="CNY",
                    salary_period="",
                    salary_disclosed=salary_disclosed,
                    location=location,
                    job_type=job_type,
                    published_at=published_at,
                    matched_tags=tags,
                )
            )

        return results


register("sangfor_official", SangforOfficialAdapter())
