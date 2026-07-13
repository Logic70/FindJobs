"""Chaitin (长亭科技) official recruitment adapter.

API: GET https://join.chaitin.cn/plugins/career_site/api/default/jobs
     ?page={page}&size={size}
Response shape::

    {
      "code": 0,
      "data": {
        "total_count": 123,
        "has_next_page": true,
        "items": [
          {
            "job_id": "<uuid>",
            "title": "...",
            "department": "...",
            "job_category_name": "...",
            "location": "...",
            "work_type": "full_time|internship",
            "recruitment_type": "social|campus",
            "description": "...",
            "salary_min": 30000,
            "salary_max": 50000,
            "salary_months": 15,
            "tracking_code": "...",
            "career_site_published_at": 1780272000,
            "updated_at": 1781481600
          }
        ]
      }
    }

Salary semantics (verified from frontend):
  - Full-time → monthly CNY
  - Internship/part-time → daily CNY
  - ``salary_months`` is a multiplier (e.g. 15 means 15 monthly payments per year)
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob


_LIST_API = "https://join.chaitin.cn/plugins/career_site/api/default/jobs"
_PAGE_SIZE = 20
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
    "Referer": "https://join.chaitin.cn/plugins/career_site/sites/default",
}


def _str(value: Any) -> str:
    """Coerce a value to trimmed string, returning '' for None."""
    return str(value).strip() if value is not None else ""


def _parse_datetime(value: str) -> datetime | None:
    """Parse ISO-8601 datetime string, stripping timezone info."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _parse_unix_timestamp(value: Any) -> datetime | None:
    """Parse a UNIX-seconds timestamp into a naive datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        return None


def _parse_time_field(value: Any) -> datetime | None:
    """Parse a field that may be UNIX seconds or ISO-8601 string."""
    if value is None:
        return None
    # Try UNIX seconds first.
    try:
        return datetime.fromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        pass
    # Fallback to ISO-8601.
    return _parse_datetime(_str(value))


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract job items from a Chaitin API response.

    Raises:
        ValueError: If the API returned a non-zero ``code``.
    """
    code = raw.get("code")
    if code is not None and code != 0:
        raise ValueError(
            f"Chaitin API returned error code {code}: {raw.get('message', '')}"
        )
    data = raw.get("data")
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    return (
        [item for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )


def _has_next_page(raw: dict[str, Any]) -> bool:
    """Return True when the response indicates more pages."""
    data = raw.get("data")
    if isinstance(data, dict):
        return bool(data.get("has_next_page", False))
    return False


def _total_count(raw: dict[str, Any]) -> int | None:
    """Extract the total job count, or None if unset."""
    data = raw.get("data")
    if isinstance(data, dict):
        try:
            return int(data["total_count"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _is_fulltime(work_type: str) -> bool:
    """Return True for full-time roles (monthly salary).

    Handles both English (``full_time``) and Chinese (``全职``) values.
    """
    return work_type.strip().lower() not in ("internship", "part_time", "实习", "兼职")


def _is_valid_positive(value: Any) -> bool:
    """Return True when *value* is a valid positive number.

    None, zero, negative, and non-numeric values are treated as invalid.
    """
    if value is None:
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _build_salary(item: dict[str, Any]) -> dict[str, Any]:
    """Build salary dict from the Chaitin API's structured fields.

    Full-time salary values are monthly CNY; internship/part-time values
    are daily CNY.  ``salary_months`` is recorded in ``salary_text``
    without multiplying to an annual total.  Both min and max must be
    valid positive numbers for salary to be considered disclosed.
    """
    min_val = item.get("salary_min")
    max_val = item.get("salary_max")
    months = item.get("salary_months")
    work_type = _str(item.get("work_type", ""))
    fulltime = _is_fulltime(work_type)
    period = "monthly" if fulltime else "daily"

    disclosed = _is_valid_positive(min_val) and _is_valid_positive(max_val)

    if disclosed:
        min_f = float(min_val)
        max_f = float(max_val)
        parts = [f"{int(min_f)}-{int(max_f)} CNY/{'month' if fulltime else 'day'}"]
        if months is not None:
            try:
                months_int = int(float(months))
                parts.append(f"{months_int} payments/year")
            except (TypeError, ValueError):
                pass
        text = " · ".join(parts)
    else:
        min_f = None
        max_f = None
        text = ""

    return {
        "salary_text": text,
        "salary_min": min_f,
        "salary_max": max_f,
        "salary_currency": "CNY",
        "salary_period": period,
        "salary_disclosed": disclosed,
    }


def _job_url(item: dict[str, Any]) -> str:
    """Build the browser-verified job detail URL from a job item.

    Verified format (2026-07-13):
      https://join.chaitin.cn/plugins/career_site/sites/default/jobs/{job_id}?job_id={job_id}
    """
    job_id = _str(item.get("job_id"))
    if job_id:
        return (
            "https://join.chaitin.cn/plugins/career_site/sites/default"
            f"/jobs/{job_id}?job_id={job_id}"
        )
    return "https://join.chaitin.cn/plugins/career_site/sites/default/jobs"


def _location(item: dict[str, Any]) -> str:
    """Extract and return location string from a job item."""
    loc = item.get("location")
    if loc is None:
        return ""
    if isinstance(loc, dict):
        parts: list[str] = []
        for key in ("province", "city", "district", "address"):
            val = loc.get(key)
            if val:
                parts.append(_str(val))
        return " / ".join(parts)
    return _str(loc)


def _get_page(url: str, page: int, size: int) -> dict[str, Any]:
    """Fetch one page from the Chaitin API with retry for transient errors.

    Retries up to ``_MAX_RETRIES`` times on transport errors and server
    (5xx) errors.  Client (4xx) errors are surfaced immediately.
    """
    import httpx

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = httpx.get(
                url,
                params={"page": page, "size": size},
                headers=_HEADERS,
                timeout=30,
            )
            if response.status_code >= 400:
                response.raise_for_status()
            raw = response.json()
            # Raise on non-zero API code so we don't silently accept errors.
            if raw.get("code") is not None and raw.get("code") != 0:
                raise ValueError(
                    f"Chaitin API returned error code {raw.get('code')}: "
                    f"{raw.get('message', '')}"
                )
            return raw
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            raise
        except httpx.HTTPStatusError as exc:
            # Only retry 5xx server errors; 4xx client errors are fatal.
            if exc.response.status_code < 500 or attempt >= _MAX_RETRIES - 1:
                raise
            last_exc = exc
            time.sleep(_RETRY_BACKOFF * (2**attempt))

    # Should not be reached, but satisfy the type-checker.
    raise RuntimeError("Unexpected: retry loop exhausted") from last_exc


class ChaitinOfficialAdapter(BaseAdapter):
    """Adapter for Chaitin (长亭科技) official career page.

    Uses full-list pagination (no keyword queries) because the site only
    has ~123 total postings.  Stops when ``has_next_page`` is false,
    ``total_count`` is reached, or the defensive ``_MAX_PAGES`` limit
    is hit.
    """

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch the first page of the job listing."""
        url = context.fetch_url or _LIST_API
        return _get_page(url, page=1, size=_PAGE_SIZE)

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Paginate the full job list, deduplicate, and parse."""
        url = context.fetch_url or _LIST_API

        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()
        total: int | None = None

        for page in range(1, _MAX_PAGES + 1):
            raw = _get_page(url, page=page, size=_PAGE_SIZE)

            if total is None:
                total = _total_count(raw)

            page_items = _items(raw)
            if not page_items:
                break

            for item in page_items:
                item_id = _str(item.get("job_id"))
                title = _str(item.get("title"))
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

            if not _has_next_page(raw):
                break
            if total is not None and len(all_items) >= total:
                break

        return self.parse({"code": 0, "data": {"items": all_items}}, context)

    def parse(self, raw: dict[str, Any], context: AdapterContext) -> list[CollectedJob]:
        """Parse a raw Chaitin API response into ``CollectedJob`` list."""
        jobs: list[CollectedJob] = []

        for item in _items(raw):
            external_id = _str(item.get("job_id"))
            title = _str(item.get("title"))
            description = _str(item.get("description"))
            salary = _build_salary(item)

            # job_type: prefer job_category_name, fall back to category, then work_type.
            job_type_raw = _str(
                item.get("job_category_name")
                or item.get("category")
                or item.get("work_type")
            )
            tags = classify_job(title, description, job_type_raw)

            # Published at: career_site_published_at (UNIX seconds) first,
            # then fall back to created_at/updated_at (ISO or UNIX).
            published_dt = _parse_time_field(item.get("career_site_published_at"))
            if published_dt is None:
                published_dt = _parse_time_field(item.get("created_at"))
            if published_dt is None:
                published_dt = _parse_time_field(item.get("updated_at"))

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
                    location=_location(item),
                    job_type=job_type_raw,
                    published_at=published_dt,
                    matched_tags=tags,
                )
            )

        return jobs


register("chaitin_official", ChaitinOfficialAdapter())
