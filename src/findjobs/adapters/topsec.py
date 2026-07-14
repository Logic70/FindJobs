"""TopSec (Tianrongxin) BeiSen official recruitment adapter.

Careers page: https://topsec.zhiye.com/social/jobs
API:          POST https://topsec.zhiye.com/api/Jobad/GetJobAdPageList
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary

_LIST_URL = "https://topsec.zhiye.com/api/Jobad/GetJobAdPageList"
_BASE_URL = "https://topsec.zhiye.com/social/jobs"
_DETAIL_BASE = "https://topsec.zhiye.com/social/detail"
_PAGE_SIZE = 100
_MAX_PAGES = 50
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://topsec.zhiye.com",
    "Referer": _BASE_URL,
}

_QUERY_TEMPLATE: dict[str, Any] = {
    "PageIndex": 0,
    "Category": ["1"],
    "KeyWords": "",
    "SpecialType": 0,
    "PortalId": "",
    "DisplayFields": [
        "Category",
        "Kind",
        "LocId",
        "PostDate",
        "ClassificationOne",
        "ClassificationTwo",
        "WorkWeChatQrCode",
    ],
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


def _build_payload(page_index: int) -> dict[str, Any]:
    """Build the request payload for the TopSec API."""
    payload = dict(_QUERY_TEMPLATE)
    payload["PageIndex"] = page_index
    payload["PageSize"] = _PAGE_SIZE
    return payload


def _validate_code(raw: dict[str, Any]) -> None:
    """Validate that the API response has Code == 200."""
    code = raw.get("Code")
    if code != 200:
        raise ValueError(
            f"TopSec API returned error code {code}: "
            f"{raw.get('Message', '')}"
        )


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract job items from a TopSec API response."""
    _validate_code(raw)
    data = raw.get("Data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _total_count(raw: dict[str, Any]) -> int | None:
    """Extract the total count from the API response."""
    try:
        return int(raw["Count"])
    except (KeyError, TypeError, ValueError):
        return None


def _build_description(item: dict[str, Any]) -> str:
    """Build a structured description from Duty and Require fields.

    Duty corresponds to responsibilities, Require to requirements.
    Both are preserved as separate sections.
    """
    responsibility = _strip_html(_str(item.get("Duty") or ""))
    requirement = _strip_html(_str(item.get("Require") or ""))
    parts: list[str] = []
    if responsibility:
        parts.append("职责:\n" + responsibility)
    if requirement:
        parts.append("要求:\n" + requirement)
    return "\n\n".join(parts)


def _location(item: dict[str, Any]) -> str:
    """Join all LocNames values with the ``、`` delimiter.

    The ``、`` (dunhao) is the standard Chinese enumeration comma supported
    by the central location normalization pipeline.
    """
    loc_names = item.get("LocNames")
    if not isinstance(loc_names, list):
        return ""
    return "、".join(_str(name) for name in loc_names if name)


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


def _parse_unix_timestamp(value: Any) -> datetime | None:
    """Parse a UNIX timestamp (seconds or milliseconds) into a naive UTC datetime."""
    if value is None:
        return None
    try:
        val = float(value)
        if val > 1e11:  # milliseconds -> seconds
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _get_detail_url(job_ad_id: str, base_url: str = "") -> str:
    """Build the official detail URL from the origin of *base_url*, or the
    verified default origin when *base_url* is not provided."""
    if not job_ad_id:
        return ""
    if base_url:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return f"{origin}/social/detail?jobAdId={job_ad_id}"
    return f"{_DETAIL_BASE}?jobAdId={job_ad_id}"


def _fetch_page(url: str, page_index: int, max_retries: int = _MAX_RETRIES) -> dict[str, Any]:
    """Fetch one page from the TopSec API with retry for transient errors.

    Retries up to *max_retries* times on transport errors and HTTP 5xx
    server errors.  Client errors (4xx) are surfaced immediately.
    Uses exponential backoff (1, 2, 4 seconds).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(headers=_HEADERS, timeout=30) as client:
                resp = client.post(url, json=_build_payload(page_index))
                if resp.status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(_RETRY_BACKOFF * (2**attempt))
                    continue
                resp.raise_for_status()
                raw: dict[str, Any] = resp.json()
                _validate_code(raw)
                return raw
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            raise
        except httpx.HTTPStatusError as exc:
            # Only retry 5xx server errors; 4xx client errors are fatal.
            if exc.response.status_code < 500 or attempt >= max_retries - 1:
                raise
            last_exc = exc
            time.sleep(_RETRY_BACKOFF * (2**attempt))

    raise RuntimeError("Unexpected: retry loop exhausted") from last_exc


class TopSecOfficialAdapter(BaseAdapter):
    """Adapter for TopSec (Tianrongxin) BeiSen official career page.

    Uses a single blank full-list scan (no keyword queries) because the
    site only has ~12 postings.  Paginates using the ``Count`` field from
    the response.  Stops on short/empty page or when the defensive
    ``_MAX_PAGES`` limit is hit.
    """

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch the first page of the job listing."""
        url = context.fetch_url or _LIST_URL
        return _fetch_page(url, page_index=0)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Paginate the full job list, deduplicate, and parse.

        Uses a blank ``KeyWords=""`` scan for the complete listing.
        Paginates with 0-based ``PageIndex``, stopping when the page
        returns fewer items than ``_PAGE_SIZE`` or the total ``Count``
        is reached.  A defensive ``_MAX_PAGES`` limit prevents runaway
        requests.

        Deduplicates by ``JobAdId``, falling back to
        ``(title, location)`` tuple when ``JobAdId`` is empty.
        """
        url = context.fetch_url or _LIST_URL

        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        total: int | None = None

        for page in range(_MAX_PAGES):
            raw = _fetch_page(url, page_index=page)

            if total is None:
                total = _total_count(raw)

            page_items = _items(raw)
            if not page_items:
                break

            for item in page_items:
                item_id = _str(item.get("JobAdId"))
                title = _str(item.get("JobAdName"))
                loc = _location(item)
                key_tuple = (title, loc)

                if item_id:
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                else:
                    if key_tuple in seen_key_tuples:
                        continue
                    seen_key_tuples.add(key_tuple)

                all_items.append(item)

            if len(page_items) < _PAGE_SIZE:
                break
            if total is not None and len(all_items) >= total:
                break

        # Wrap items back into a response-like structure for parse().
        wrapped: dict[str, Any] = {"Code": 200, "Count": len(all_items), "Data": all_items}
        return self.parse(wrapped, context)

    def parse(self, raw: dict[str, Any], context: AdapterContext) -> list[CollectedJob]:
        """Parse a TopSec API response into ``CollectedJob`` instances.

        Field mapping:

        * ``JobAdId``                -> ``external_id``
        * ``JobAdName``              -> ``title``
        * ``LocNames`` (list)        -> ``location`` (joined with ``、``)
        * ``Salary``                 -> ``salary_*`` (via ``parse_salary``)
        * ``Duty`` / ``Require``     -> ``description`` (structured)
        * ``ClassificationOne``,
          ``ClassificationTwo``,
          ``Kind``                   -> ``job_type`` (factual text)
        * ``PostDate`` / ``PostDateInt`` -> ``published_at``

        ``matched_tags`` is computed via :func:`classify_job`.
        """
        jobs: list[CollectedJob] = []

        for item in _items(raw):
            external_id = _str(item.get("JobAdId"))
            title = _str(item.get("JobAdName"))
            description = _build_description(item)

            # Location: preserve every LocNames value with dunhao delimiter.
            location = _location(item)

            # Job type: combine factual category text and employment type.
            # All three fields use human-readable text, never opaque codes.
            cls1 = _str(item.get("ClassificationOne") or "")
            cls2 = _str(item.get("ClassificationTwo") or "")
            kind = _str(item.get("Kind") or "")
            job_type = " / ".join(p for p in [cls1, cls2, kind] if p)

            # Salary: only non-empty official salary; null/empty -> undisclosed.
            salary_text = _str(item.get("Salary"))
            if salary_text:
                salary = parse_salary(salary_text)
            else:
                salary = parse_salary(None)

            # Published at: PostDate (ISO date) first, then PostDateInt (UNIX timestamp).
            published_at = _parse_datetime(_str(item.get("PostDate")))
            if published_at is None:
                published_at = _parse_unix_timestamp(item.get("PostDateInt"))

            tags = classify_job(title, description, job_type)

            jobs.append(
                CollectedJob(
                    external_id=external_id,
                    title=title,
                    url=_get_detail_url(external_id, context.base_url),
                    description=description,
                    salary_text=salary["salary_text"],
                    salary_min=salary["salary_min"],
                    salary_max=salary["salary_max"],
                    salary_currency=salary["salary_currency"],
                    salary_period=salary["salary_period"],
                    salary_disclosed=salary["salary_disclosed"],
                    location=location,
                    job_type=job_type,
                    published_at=published_at,
                    matched_tags=tags,
                )
            )

        return jobs


register("topsec_official", TopSecOfficialAdapter())
