"""Qianxin official social recruitment adapter for its Hotjob-backed careers page.

Official Qianxin campus site footer links social recruitment to
https://www.hotjob.cn/wt/qianxin/web/index#/ .

The list API uses a form-urlencoded POST; bootstrap cookies via a GET to the
base page first, then POST for each keyword/page.  Detail enrichment fetches
each unique posting individually to obtain requirements and responsibilities.
"""

from __future__ import annotations

import html
import json
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
from findjobs.salary import parse_salary

_BASE_URL = "https://www.hotjob.cn/wt/qianxin/web/index"
_LIST_URL = "https://www.hotjob.cn/wt/qianxin/web/mode400/position/list"
_DETAIL_URL = "https://www.hotjob.cn/wt/qianxin/web/mode400/position/detail"
_PAGE_SIZE = 100
_MAX_PAGES_PER_KEYWORD = 50
_MAX_RETRIES = 3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.hotjob.cn",
    "Referer": "https://www.hotjob.cn/wt/qianxin/web/index",
}


def _str(value: Any) -> str:
    """Return a stripped string representation, or empty string on None."""
    return str(value).strip() if value is not None else ""


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text converter that inserts newlines for block elements."""

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
    """Convert HTML to plain text using the _TextExtractor."""
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()


def _items_from_raw(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the details list from a Hotjob API response."""
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    details = data.get("details") or data.get("list") or data.get("items") or []
    return [item for item in details if isinstance(item, dict)]


def _total_count(raw: dict[str, Any]) -> int | None:
    """Extract rowCount from the API response, if available."""
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    try:
        return int(_str(data.get("rowCount")))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: str) -> datetime | None:
    """Parse common date string formats."""
    text = _str(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _build_description(item: dict[str, Any]) -> str:
    """Build a structured description from workConcet and serviceCondition.

    workConcet corresponds to responsibilities, serviceCondition to requirements.
    """
    responsibility = _strip_html(_str(item.get("workConcet") or ""))
    requirement = _strip_html(_str(item.get("serviceCondition") or ""))
    parts: list[str] = []
    if responsibility:
        parts.append("职责:\n" + responsibility)
    if requirement:
        parts.append("要求:\n" + requirement)
    return "\n\n".join(parts)


def _job_url(item: dict[str, Any]) -> str:
    """Build a stable official plaintext-compatible hash URL for a posting."""
    post_id = _str(item.get("PostId") or "")
    if not post_id:
        return _BASE_URL
    return (
        "https://www.hotjob.cn/wt/qianxin/web/index"
        "#/positionDetail"
        f"?PostId={post_id}"
        "&RecruitType=2"
        "&RecruitTypeName=%E7%A4%BE%E4%BC%9A%E6%8B%9B%E8%81%98"
    )


def _normalize_salary(salary_text: str) -> str:
    """Normalize salary text to a format parse_salary can handle.

    parse_salary expects e.g. "10k-15k" with ``k`` on both sides.
    Hotjob returns "10-15k/月" where the ``k`` applies to both numbers
    and a ``/月`` suffix follows.
    """
    text = salary_text.strip()
    # Strip trailing /月 or /年 suffix.
    text = re.sub(r"/[月年]$", "", text)
    # Transform "10-15k" → "10k-15k" when k only appears after the second number.
    text = re.sub(r"^(\d+)\s*[-–]\s*(\d+)\s*[kK]$", r"\1k-\2k", text)
    return text


def _identity(item: dict[str, Any]) -> tuple[str, str]:
    """Return a stable identity tuple for deduplication."""
    external_id = _str(item.get("PostId") or "")
    if external_id:
        return ("id", external_id)
    title = _str(item.get("PostName") or item.get("name") or "")
    location = _str(item.get("WorkPlace") or item.get("workPlace") or "")
    return ("title_location", f"{title}\0{location}")


def _retry_request(
    client: httpx.Client,
    method: str,
    url: str,
    max_attempts: int = _MAX_RETRIES,
    **kwargs: Any,
) -> httpx.Response:
    """Issue an HTTP request with bounded retry for transport/5xx errors.

    Retries on :class:`httpx.TransportError` (including timeouts) and
    HTTP 5xx status codes.  Client errors (4xx) are never retried.
    Uses exponential backoff (1, 2, 4 seconds).
    """
    for attempt in range(max_attempts):
        try:
            resp = client.request(method, url, **kwargs)
            # Retry on 5xx server errors (do not raise_for_status yet).
            if resp.status_code >= 500 and attempt < max_attempts - 1:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp
        except httpx.TransportError as e:
            if attempt < max_attempts - 1:
                time.sleep(2**attempt)
                continue
            raise


class QianxinOfficialAdapter(BaseAdapter):
    """Adapter for Qianxin's official Hotjob recruitment API.

    The API requires cookie bootstrapping via a GET to the base page before
    any POST requests.  Pagination uses ``rowIndex`` as a **1-based page
    number** (not an item offset): ``rowIndex=1`` gives items 1 through *N*,
    ``rowIndex=2`` gives items *N*+1 through *2N*, etc., where *N* is
    ``rowSize``.  Pagination is bounded by ``rowCount`` in the response or a
    short/empty page.  Detail enrichment is mandatory but never discards
    list-level facts when a single detail request fails after all retries.
    """

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch the first list page for the first target keyword.

        Bootstraps cookies with a GET to the base page, then POSTs to the
        list endpoint with ``rowIndex=1`` (1-based page number).
        """
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            _retry_request(
                client, "GET", context.base_url or _BASE_URL, headers=_HEADERS
            )
            kwargs: dict[str, str] = {
                "recruitType": "2",
                "rowSize": str(_PAGE_SIZE),
                "rowIndex": "1",
                "postName": TARGET_KEYWORDS[0],
            }
            url = context.fetch_url or _LIST_URL
            resp = _retry_request(client, "POST", url, data=kwargs, headers=_HEADERS)
            raw = resp.json()
            if raw.get("code") != "00":
                raise ValueError(f"Qianxin list API returned code={raw.get('code')!r}")
            return raw

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Collect across all target keywords with pagination, dedup, and detail enrichment.

        Pagination uses ``rowIndex`` as a 1-based page number (starts at 1,
        increments by 1 per page), not an item offset.
        """
        all_items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        list_url = context.fetch_url or _LIST_URL

        with httpx.Client(follow_redirects=True, timeout=30) as client:
            # Bootstrap cookies
            _retry_request(
                client, "GET", context.base_url or _BASE_URL, headers=_HEADERS
            )

            for keyword in TARGET_KEYWORDS:
                keyword_total: int | None = None
                keyword_item_count = 0

                for page_no in range(1, _MAX_PAGES_PER_KEYWORD + 1):
                    kwargs: dict[str, str] = {
                        "recruitType": "2",
                        "rowSize": str(_PAGE_SIZE),
                        "rowIndex": str(page_no),
                        "postName": keyword,
                    }
                    resp = _retry_request(
                        client, "POST", list_url, data=kwargs, headers=_HEADERS
                    )
                    raw = resp.json()
                    if raw.get("code") != "00":
                        raise ValueError(
                            f"Qianxin list API returned code={raw.get('code')!r} "
                            f"for keyword={keyword!r}"
                        )

                    if keyword_total is None:
                        keyword_total = _total_count(raw)

                    items = _items_from_raw(raw)
                    if not items:
                        break

                    for item in items:
                        key = _identity(item)
                        if key in seen:
                            continue
                        seen.add(key)
                        all_items.append(item)

                    keyword_item_count += len(items)

                    # Stop conditions
                    if (
                        keyword_total is not None
                        and keyword_item_count >= keyword_total
                    ):
                        break
                    if len(items) < _PAGE_SIZE:
                        break

            # Detail enrichment — mandatory for retained candidates.
            enriched = list(all_items)
            for index, item in enumerate(enriched):
                post_id = _str(item.get("PostId") or "")
                if not post_id:
                    continue
                if item.get("serviceCondition") or item.get("workConcet"):
                    continue  # already enriched
                try:
                    detail_resp = _retry_request(
                        client,
                        "GET",
                        _DETAIL_URL,
                        params={"postId": post_id, "recruitType": "2"},
                        headers=_HEADERS,
                        timeout=20,
                    )
                    detail_raw = detail_resp.json()
                    if detail_raw.get("code") != "00":
                        continue
                    detail_data = detail_raw.get("data")
                    if not isinstance(detail_data, dict):
                        continue
                    enriched[index] = {**item, **detail_data}
                except (
                    httpx.HTTPStatusError,
                    httpx.TransportError,
                    json.JSONDecodeError,
                    ValueError,
                ):
                    # Must not discard list facts when a single detail request fails.
                    continue

        enriched_raw: dict[str, Any] = {
            "code": "00",
            "data": {"details": enriched},
        }
        return self.parse(enriched_raw, context)

    def parse(self, raw: dict[str, Any], context: AdapterContext) -> list[CollectedJob]:
        """Parse an enriched or plain list response into CollectedJob instances."""
        jobs: list[CollectedJob] = []

        for item in _items_from_raw(raw):
            title = _str(item.get("PostName") or item.get("name") or "")
            external_id = _str(item.get("PostId") or "")
            description = _build_description(item)
            job_type = _str(item.get("PostType") or "")
            location = _str(item.get("WorkPlace") or item.get("workPlace") or "")

            # Salary handling: always preserve original text.
            salary_text = _str(item.get("Salary") or "")
            if salary_text:
                normalized = _normalize_salary(salary_text)
                salary = parse_salary(normalized)
                salary["salary_text"] = salary_text  # keep original API value
            else:
                salary = parse_salary(None)

            published_str = _str(item.get("ReleaseTime") or "")
            published = _parse_datetime(published_str) if published_str else None

            tags = classify_job(title, description, job_type)

            jobs.append(
                CollectedJob(
                    external_id=external_id,
                    title=title,
                    url=_job_url(item),
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


register("qianxin_official", QianxinOfficialAdapter())
