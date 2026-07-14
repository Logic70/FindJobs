"""StepFun (Jieyue Xingchen / 阶跃星辰) campus Moka recruitment adapter.

Careers page: https://app.mokahr.com/campus-recruitment/step/141903
API:          POST https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2

Uses the standard Moka encrypted init-data / AES-CBC response flow.
Reuses pure helper functions from the DeepSeek Moka adapter for init-data
parsing, AES decryption, item extraction, total count, HTML stripping,
location formatting, and datetime parsing.

Owns StepFun-specific HTTP headers, payload shape, retry behaviour and
parsing logic (including Chinese-bracket section conversion).
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.deepseek import (
    _decrypt_moka_payload,
    _extract_init_data,
    _format_locations,
    _items_from_raw,
    _job_total,
    _parse_datetime,
    _str,
    _strip_html,
)
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary

_CAREERS_URL = (
    "https://app.mokahr.com/campus-recruitment/step/141903"
)
_JOBS_API_URL = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2"
_PAGE_SIZE = 50
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
    "Origin": "https://app.mokahr.com",
    "Referer": _CAREERS_URL,
}

# ---------------------------------------------------------------------------
# Request payload
# ---------------------------------------------------------------------------


def _request_payload(
    *, org_id: str, site_id: str, offset: int
) -> dict[str, Any]:
    """Build the POST body for a single page.

    Uses ``site="campus"`` (campus recruitment) and a blank keyword so
    every job is returned without search filtering.
    """
    return {
        "orgId": org_id,
        "siteId": site_id,
        "limit": _PAGE_SIZE,
        "offset": offset,
        "site": "campus",
        "needStat": True,
        "locale": "zh-CN",
    }


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


def _validate_response(raw: dict[str, Any]) -> None:
    """Validate that the decrypted API response signals success.

    Raises ``ValueError`` when ``success`` is not ``True`` or
    ``code`` is not ``0``.
    """
    if not raw.get("success"):
        msg = raw.get("message") or raw.get("msg") or "unknown error"
        code = raw.get("code", "?")
        raise ValueError(
            f"StepFun Moka API returned success=false (code={code}): {msg}"
        )
    code = raw.get("code")
    if code != 0:
        raise ValueError(
            f"StepFun Moka API returned non-zero code {code}: "
            f"{raw.get('message', '')}"
        )


# ---------------------------------------------------------------------------
# Description helpers
# ---------------------------------------------------------------------------


def _normalize_sections(text: str) -> str:
    """Normalise Moka Chinese-bracketed section headings.

    After HTML stripping the job description, convert the common Moka
    bracketed headings so that the central ``normalize_job_details``
    pipeline can deterministically extract responsibilities and requirements.

    Conversions:
        ``【岗位描述】`` / ``【工作职责】``  -> ``职责:``
        ``【岗位要求】`` / ``【任职要求】``  -> ``要求:``
    """
    text = re.sub(r"【岗位描述】|【工作职责】", "职责:", text)
    text = re.sub(r"【岗位要求】|【任职要求】", "要求:", text)
    return text


def _build_description(item: dict[str, Any]) -> str:
    """Build a plain-text description from the job's ``jobDescription``.

    The field is HTML-encoded.  After stripping HTML the Chinese-bracketed
    section headings are converted to their colon equivalents so the central
    detail-extraction pipeline can pick them up.
    """
    text = _strip_html(
        _str(item.get("jobDescription") or item.get("description") or "")
    )
    if not text:
        return ""
    return _normalize_sections(text)


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def _job_url(item: dict[str, Any], context: AdapterContext) -> str:
    """Build the official browser URL using the Moka hash pattern."""
    job_id = _str(item.get("id") or item.get("jobId"))
    base_url = context.base_url or _CAREERS_URL
    return f"{base_url}#/job/{job_id}" if job_id else base_url


# ---------------------------------------------------------------------------
# Job-type helper
# ---------------------------------------------------------------------------


def _job_type(item: dict[str, Any]) -> str:
    """Return the factual commitment (employment type) as the canonical type.

    Crucially this does **not** use the broad ``zhineng`` category name,
    avoiding false exclusion by the classifier when the category contains
    "算法" but the individual title does not.
    """
    return _str(item.get("commitment"))


# ---------------------------------------------------------------------------
# Reusable request helper with bounded retry
# ---------------------------------------------------------------------------


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_data: dict[str, Any] | None = None,
) -> httpx.Response:
    """Send an HTTP request with bounded retry.

    Retries transport errors and HTTP 5xx up to ``_MAX_RETRIES`` times
    with exponential backoff (1, 2, 4 seconds).  Client errors (4xx) are
    surfaced immediately without retry.

    Accepts an existing ``httpx.Client`` so the same session can be used
    for the bootstrap GET and all subsequent POST requests.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            if method.upper() == "GET":
                resp = client.get(url, headers=headers)
            else:
                resp = client.post(url, json=json_data, headers=headers)

            if resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            resp.raise_for_status()
            return resp
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt >= _MAX_RETRIES - 1:
                raise
            last_exc = exc
            time.sleep(_RETRY_BACKOFF * (2**attempt))

    raise RuntimeError("Unexpected: retry loop exhausted") from last_exc


# ---------------------------------------------------------------------------
# Page fetcher (client + decrypt + validate)
# ---------------------------------------------------------------------------


def _fetch_page(
    *,
    client: httpx.Client,
    context: AdapterContext,
    aes_iv: str,
    org_id: str,
    site_id: str,
    offset: int,
) -> dict[str, Any]:
    """POST one page through ``_request`` and return the decrypted result.

    Decryption and ``_validate_response`` are applied on the successful
    response before returning.
    """
    url = context.fetch_url or _JOBS_API_URL
    payload = _request_payload(org_id=org_id, site_id=site_id, offset=offset)
    resp = _request(client, "POST", url, json_data=payload, headers=_HEADERS)
    raw: dict[str, Any] = resp.json()
    decrypted = _decrypt_moka_payload(raw, aes_iv)
    _validate_response(decrypted)
    return decrypted


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class StepFunMokaAdapter(BaseAdapter):
    """Adapter for the official StepFun (阶跃星辰) campus Moka careers page.

    Uses a single blank full-list scan (no keyword queries).  Paginates
    using the ``total`` field from ``jobStats``.  Stops on short/empty
    page, when total is reached, or after the defensive ``_MAX_PAGES``
    limit.
    """

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch the first page of the full listing."""
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = _request(client, "GET", context.base_url or _CAREERS_URL, headers=_HEADERS)
            init_data = _extract_init_data(resp.text)
            aes_iv = _str(init_data.get("aesIv"))
            org = init_data.get("org") if isinstance(init_data.get("org"), dict) else {}
            org_id = _str(org.get("id"))
            site_id = _str(init_data.get("siteId") or org.get("siteId"))

            if not aes_iv or not org_id or not site_id:
                raise ValueError("StepFun Moka init-data missing aesIv/orgId/siteId")

            return _fetch_page(
                client=client,
                context=context,
                aes_iv=aes_iv,
                org_id=org_id,
                site_id=site_id,
                offset=0,
            )

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Paginate the full job list, deduplicate, and parse."""
        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_key_tuples: set[tuple[str, str]] = set()

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = _request(client, "GET", context.base_url or _CAREERS_URL, headers=_HEADERS)
            init_data = _extract_init_data(resp.text)
            aes_iv = _str(init_data.get("aesIv"))
            org = init_data.get("org") if isinstance(init_data.get("org"), dict) else {}
            org_id = _str(org.get("id"))
            site_id = _str(init_data.get("siteId") or org.get("siteId"))

            if not aes_iv or not org_id or not site_id:
                raise ValueError("StepFun Moka init-data missing aesIv/orgId/siteId")

            total: int | None = None
            for page_no in range(_MAX_PAGES):
                offset = page_no * _PAGE_SIZE
                raw = _fetch_page(
                    client=client,
                    context=context,
                    aes_iv=aes_iv,
                    org_id=org_id,
                    site_id=site_id,
                    offset=offset,
                )

                if total is None:
                    total = _job_total(raw)

                page_items = _items_from_raw(raw)
                if not page_items:
                    break

                for item in page_items:
                    item_id = _str(item.get("id") or item.get("jobId"))
                    if item_id:
                        if item_id in seen_ids:
                            continue
                        seen_ids.add(item_id)
                    else:
                        title = _str(item.get("title"))
                        loc = _format_locations(item)
                        key = (title, loc)
                        if key in seen_key_tuples:
                            continue
                        seen_key_tuples.add(key)
                    all_items.append(item)

                if len(page_items) < _PAGE_SIZE:
                    break
                if total is not None and len(all_items) >= total:
                    break

        wrapped: dict[str, Any] = {
            "code": 0,
            "success": True,
            "data": {"jobs": all_items},
        }
        return self.parse(wrapped, context)

    def parse(
        self, raw: dict[str, Any] | list[dict[str, Any]], context: AdapterContext
    ) -> list[CollectedJob]:
        """Parse a StepFun Moka API response into ``CollectedJob`` instances.

        Field mapping:

        * ``id`` / ``jobId``          -> ``external_id``
        * ``title``                   -> ``title``
        * ``jobDescription``          -> ``description`` (HTML stripped,
                                         Chinese-bracket sections converted)
        * ``commitment``              -> ``job_type`` (factual type only)
        * ``salaryText``              -> ``salary_*`` (via ``parse_salary``)
        * ``locations`` (list of dict) -> ``location`` (via Moka formatter)
        * ``openedAt``                -> ``published_at``
        * ``status``                  -> filtered (non-open skipped)

        ``matched_tags`` is computed via :func:`classify_job`.
        """
        # Validate the top-level code / success fields when the input has the
        # standard Moka response shape.
        if isinstance(raw, dict) and "success" in raw:
            _validate_response(raw)

        jobs: list[CollectedJob] = []

        for item in _items_from_raw(raw):
            if _str(item.get("status")) and _str(item.get("status")).lower() != "open":
                continue

            title = _str(item.get("title"))
            description = _build_description(item)
            location = _format_locations(item)
            commitment = _job_type(item)

            salary_text = _str(item.get("salaryText") or item.get("salary") or item.get("salaryRange") or "")
            salary = parse_salary(salary_text) if salary_text else parse_salary(None)

            tags = classify_job(title, description, commitment)

            jobs.append(
                CollectedJob(
                    external_id=_str(item.get("id") or item.get("jobId")),
                    title=title,
                    url=_job_url(item, context),
                    description=description,
                    salary_text=salary["salary_text"],
                    salary_min=salary["salary_min"],
                    salary_max=salary["salary_max"],
                    salary_currency=salary["salary_currency"],
                    salary_period=salary["salary_period"],
                    salary_disclosed=salary["salary_disclosed"],
                    location=location,
                    job_type=commitment,
                    published_at=_parse_datetime(
                        _str(item.get("openedAt") or item.get("createdAt"))
                    ),
                    matched_tags=tags,
                )
            )

        return jobs


register("stepfun_moka", StepFunMokaAdapter())
