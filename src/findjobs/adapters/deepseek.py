"""DeepSeek official recruitment adapter for its Moka-backed careers page."""

from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_CAREERS_URL = (
    "https://app.mokahr.com/social-recruitment/high-flyer/140576"
    "?orgId=high-flyer"
)
_JOBS_API_URL = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2"
_PAGE_SIZE = 50
_MAX_PAGES_PER_KEYWORD = 10
_DEEPSEEK_EXTRA_KEYWORDS = ["AGI"]
_KEYWORDS = TARGET_KEYWORDS + _DEEPSEEK_EXTRA_KEYWORDS
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


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_datetime(value: str) -> datetime | None:
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


class _TextExtractor(HTMLParser):
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


def _extract_init_data(page_html: str) -> dict[str, Any]:
    match = re.search(
        r'<input[^>]+id=["\']init-data["\'][^>]+value=["\']([\s\S]*?)["\']',
        page_html,
    )
    if not match:
        raise ValueError("DeepSeek Moka init-data field not found")
    return json.loads(html.unescape(match.group(1)))


def _decrypt_moka_payload(raw: dict[str, Any], aes_iv: str) -> dict[str, Any]:
    if "necromancer" not in raw or "data" not in raw:
        return raw

    key = _str(raw["necromancer"]).encode("utf-8")
    iv = _str(aes_iv).encode("utf-8")
    ciphertext = base64.b64decode(_str(raw["data"]))

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plaintext.decode("utf-8"))


def _items_from_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    items = data.get("jobs") or data.get("list") or data.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _job_total(raw: dict[str, Any]) -> int | None:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    stats = data.get("jobStats") if isinstance(data.get("jobStats"), dict) else {}
    try:
        return int(stats.get("total"))
    except (TypeError, ValueError):
        return None


def _job_type(item: dict[str, Any]) -> str:
    zhineng = item.get("zhineng")
    if isinstance(zhineng, dict):
        name = _str(zhineng.get("name"))
        if name:
            return name
    return _str(item.get("commitment"))


def _format_locations(item: dict[str, Any]) -> str:
    locations = item.get("locations")
    if not isinstance(locations, list):
        locations = [item.get("location")] if isinstance(item.get("location"), dict) else []

    results: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        parts = [
            _str(location.get("provinceName")),
            _str(location.get("cityName")),
            _str(location.get("address")),
        ]
        text = " ".join(part for part in parts if part)
        if text and text not in results:
            results.append(text)
    return " / ".join(results)


def _job_url(item: dict[str, Any], context: AdapterContext) -> str:
    job_id = _str(item.get("id") or item.get("jobId"))
    base_url = context.base_url or _CAREERS_URL
    return f"{base_url}#/job/{job_id}" if job_id else base_url


class DeepSeekMokaAdapter(BaseAdapter):
    """Adapter for the official DeepSeek Moka recruitment site."""

    def _request_payload(
        self, *, org_id: str, site_id: str, keyword: str, offset: int
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "orgId": org_id,
            "siteId": site_id,
            "limit": _PAGE_SIZE,
            "offset": offset,
            "site": "social",
            "needStat": True,
            "locale": "zh-CN",
        }
        if keyword:
            payload["keyword"] = keyword
        return payload

    def _fetch_page(
        self,
        client: Any,
        *,
        context: AdapterContext,
        aes_iv: str,
        org_id: str,
        site_id: str,
        keyword: str,
        offset: int,
    ) -> dict[str, Any]:
        response = client.post(
            context.fetch_url or _JOBS_API_URL,
            json=self._request_payload(
                org_id=org_id,
                site_id=site_id,
                keyword=keyword,
                offset=offset,
            ),
            headers=_HEADERS,
        )
        response.raise_for_status()
        return _decrypt_moka_payload(response.json(), aes_iv)

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            page = client.get(context.base_url or _CAREERS_URL, headers=_HEADERS)
            page.raise_for_status()
            init_data = _extract_init_data(page.text)
            return self._fetch_page(
                client,
                context=context,
                aes_iv=_str(init_data.get("aesIv")),
                org_id=_str(init_data.get("org", {}).get("id")),
                site_id=_str(init_data.get("siteId")),
                keyword=_KEYWORDS[0],
                offset=0,
            )

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        import httpx

        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            page = client.get(context.base_url or _CAREERS_URL, headers=_HEADERS)
            page.raise_for_status()
            init_data = _extract_init_data(page.text)
            aes_iv = _str(init_data.get("aesIv"))
            org = init_data.get("org") if isinstance(init_data.get("org"), dict) else {}
            org_id = _str(org.get("id"))
            site_id = _str(init_data.get("siteId") or org.get("siteId"))

            if not aes_iv or not org_id or not site_id:
                raise ValueError("DeepSeek Moka init-data missing aesIv/orgId/siteId")

            for keyword in _KEYWORDS:
                for page_no in range(_MAX_PAGES_PER_KEYWORD):
                    offset = page_no * _PAGE_SIZE
                    raw = self._fetch_page(
                        client,
                        context=context,
                        aes_iv=aes_iv,
                        org_id=org_id,
                        site_id=site_id,
                        keyword=keyword,
                        offset=offset,
                    )
                    items = _items_from_raw(raw)
                    for item in items:
                        job_id = _str(item.get("id") or item.get("jobId"))
                        if job_id and job_id in seen_ids:
                            continue
                        if job_id:
                            seen_ids.add(job_id)
                        all_items.append(item)

                    total = _job_total(raw)
                    if not items:
                        break
                    if total is not None and offset + len(items) >= total:
                        break
                    if len(items) < _PAGE_SIZE:
                        break

        return self.parse(all_items, context)

    def parse(self, raw: dict[str, Any] | list[dict[str, Any]], context: AdapterContext) -> list[CollectedJob]:
        jobs: list[CollectedJob] = []
        for item in _items_from_raw(raw):
            if _str(item.get("status")) and _str(item.get("status")).lower() != "open":
                continue

            title = _str(item.get("title"))
            description = _strip_html(
                _str(
                    item.get("jobDescription")
                    or item.get("description")
                    or item.get("requirement")
                )
            )
            job_type = _job_type(item)
            salary = parse_salary(
                _str(item.get("salaryText") or item.get("salary") or item.get("salaryRange"))
            )
            tags = classify_job(title, description, job_type)

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
                    location=_format_locations(item),
                    job_type=job_type,
                    published_at=_parse_datetime(
                        _str(item.get("openedAt") or item.get("createdAt"))
                    ),
                    matched_tags=tags,
                )
            )

        return jobs


register("deepseek_moka", DeepSeekMokaAdapter())
register("moka_official", DeepSeekMokaAdapter())
