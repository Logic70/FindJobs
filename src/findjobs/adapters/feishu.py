"""Feishu official ATS adapter for company-hosted career sites."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.keywords import TARGET_KEYWORDS
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


_PAGE_SIZE = 50
_MAX_PAGES = 20
_MAX_KEYWORD_PAGES = 200
_FULL_SCAN_CAP = _MAX_PAGES * _PAGE_SIZE
_PORTAL_TYPE = 6

_SIGN_MODULE_ID = "57195:function"
_SIGN_CHUNK_HINT = "3158."
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Portal-Channel": "saas-career",
    "Portal-Platform": "pc",
    "accept-language": "zh-CN",
}


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_datetime(value: Any) -> datetime | None:
    text = _str(value)
    if not text:
        return None
    try:
        if text.isdigit():
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp).replace(tzinfo=None)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    values = data.get("job_post_list") or data.get("list") or []
    return [item for item in values if isinstance(item, dict)]


def _total(raw: dict[str, Any]) -> int:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    try:
        return int(data.get("count") or data.get("total") or 0)
    except (TypeError, ValueError):
        return 0


def _dedup_key(item: dict[str, Any]) -> tuple:
    """Return a deduplication key for a job post item.

    Uses id/job_id when present; falls back to title+location.
    """
    job_id = _str(item.get("id") or item.get("job_id") or "")
    if job_id:
        return ("id", job_id)
    title = _str(item.get("title") or item.get("name") or "")
    location = _locations(item)
    return ("title_loc", title, location)


def _website_info(page_html: str) -> dict[str, Any]:
    match = re.search(
        r'<script id=["\']js-websiteInfo["\'] type=["\']text/json["\']>'
        r"([\s\S]*?)</script>",
        page_html,
    )
    if not match:
        raise ValueError("Feishu website info script not found")
    return json.loads(html.unescape(match.group(1)))


def _website_path(info: dict[str, Any]) -> str:
    path = _str((info.get("website_info") or {}).get("path"))
    return path or "index"


def _static_js_urls(page_html: str) -> list[str]:
    urls = re.findall(r'https://[^"\']+?\.js', page_html)
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique


def _extract_function_expression(js_text: str, module_id: str) -> str:
    start = js_text.find(module_id)
    if start < 0:
        raise ValueError(f"Feishu sign module {module_id!r} not found")

    function_start = js_text.find("function", start)
    if function_start < 0:
        raise ValueError("Feishu sign module function not found")

    brace_start = js_text.find("{", function_start)
    if brace_start < 0:
        raise ValueError("Feishu sign module function body not found")

    depth = 0
    quote_char: str | None = None
    escaped = False
    comment: str | None = None
    for index in range(brace_start, len(js_text)):
        char = js_text[index]
        next_char = js_text[index + 1] if index + 1 < len(js_text) else ""

        if comment == "line":
            if char == "\n":
                comment = None
            continue
        if comment == "block":
            if char == "*" and next_char == "/":
                comment = None
            continue
        if quote_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = None
            continue

        if char in {"'", '"', "`"}:
            quote_char = char
        elif char == "/" and next_char == "/":
            comment = "line"
        elif char == "/" and next_char == "*":
            comment = "block"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return js_text[function_start : index + 1]

    raise ValueError("Feishu sign module function end not found")


@lru_cache(maxsize=8)
def _load_sign_function(base_url: str) -> str:
    import httpx

    response = httpx.get(base_url.rstrip("/"), headers=_HEADERS, timeout=30)
    response.raise_for_status()
    page_html = response.text
    js_urls = _static_js_urls(page_html)
    js_urls.sort(key=lambda url: (0 if _SIGN_CHUNK_HINT in url else 1, url))

    for js_url in js_urls:
        js_response = httpx.get(js_url, headers=_HEADERS, timeout=60)
        js_response.raise_for_status()
        if _SIGN_MODULE_ID in js_response.text:
            return _extract_function_expression(js_response.text, _SIGN_MODULE_ID)

    raise ValueError("Feishu sign module chunk not found")


def _sign_many(base_url: str, requests: list[dict[str, Any]]) -> list[str]:
    if not requests:
        return []

    function_expression = _load_sign_function(base_url)
    script = (
        f"const fn={function_expression};\n"
        "const mod={exports:{}};\n"
        "fn(mod, mod.exports);\n"
        "const fs=require('fs');\n"
        "const payload=JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        "const result=payload.map((item)=>mod.exports.sign(item));\n"
        "process.stdout.write(JSON.stringify(result));\n"
    )

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        ) as temp_file:
            temp_file.write(script)
            temp_path = Path(temp_file.name)

        completed = subprocess.run(
            ["node", str(temp_path)],
            input=json.dumps(requests, ensure_ascii=False, separators=(",", ":")),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Feishu adapter requires Node.js for request signing") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise RuntimeError(
            "Feishu request signing failed: " + (completed.stderr or "").strip()
        )
    return [str(value) for value in json.loads(completed.stdout)]


def _payload(keyword: str, offset: int) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "limit": _PAGE_SIZE,
        "offset": offset,
        "job_category_id_list": [],
        "tag_id_list": [],
        "location_code_list": [],
        "subject_id_list": [],
        "recruitment_id_list": [],
        "portal_type": _PORTAL_TYPE,
        "job_function_id_list": [],
        "storefront_id_list": [],
        "portal_entrance": 1,
    }


def _query_value(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(_str(item) for item in value)
    return _str(value)


def _path_with_query(path: str, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in payload.items():
        if value is None:
            continue
        text = _query_value(value)
        if text == "undefined":
            continue
        parts.append(f"{key}={quote(text, safe='')}")
    return path + ("?" + "&".join(parts) if parts else "")


def _headers(base_url: str, website_path: str, csrf_token: str = "") -> dict[str, str]:
    base = base_url.rstrip("/")
    headers = {
        **_HEADERS,
        "Origin": base,
        "Referer": f"{base}/{website_path}/position/list",
        "website-path": website_path,
    }
    if csrf_token:
        headers["x-csrf-token"] = csrf_token
    return headers


def _extract_token(raw: dict[str, Any]) -> str:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    return _str(data.get("token") or raw.get("token"))


def _nested_i18n_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("i18n_name", "name", "en_name", "code"):
            text = _str(value.get(key))
            if text:
                return text
    return _str(value)


def _locations(item: dict[str, Any]) -> str:
    locations: list[str] = []
    for city in item.get("city_list") or []:
        if not isinstance(city, dict):
            continue
        text = _nested_i18n_name(city)
        if text and text not in locations:
            locations.append(text)

    city_info = item.get("city_info")
    if isinstance(city_info, dict):
        text = _nested_i18n_name(city_info)
        if text and text not in locations:
            locations.append(text)

    for city in item.get("jobCityList") or []:
        location = city.get("location") if isinstance(city, dict) else city
        text = _nested_i18n_name(location)
        if text and text not in locations:
            locations.append(text)

    return "\u3001".join(locations)


def _job_type(item: dict[str, Any]) -> str:
    category = item.get("job_category")
    if isinstance(category, dict):
        text = _nested_i18n_name(category)
        if text:
            return text

    function = item.get("job_function")
    if isinstance(function, dict):
        text = _nested_i18n_name(function)
        if text:
            return text

    recruit_type = item.get("recruit_type")
    if isinstance(recruit_type, dict):
        text = _nested_i18n_name(recruit_type)
        if text:
            return text
    return ""


def _description(item: dict[str, Any]) -> str:
    responsibility = _str(item.get("description"))
    requirement = _str(item.get("requirement"))
    parts: list[str] = []
    if responsibility:
        parts.append("\u804c\u8d23:\n" + responsibility)
    if requirement:
        parts.append("\u8981\u6c42:\n" + requirement)
    return "\n\n".join(parts)


def _salary(item: dict[str, Any]) -> dict[str, Any]:
    job_info = item.get("job_post_info")
    if not isinstance(job_info, dict):
        job_info = {}

    salary_text = _str(
        item.get("salary_text")
        or job_info.get("salary_text")
        or job_info.get("salary")
        or job_info.get("salary_range")
    )
    salary = parse_salary(salary_text or None)
    if salary["salary_disclosed"]:
        return salary

    min_salary = job_info.get("min_salary")
    max_salary = job_info.get("max_salary")
    if min_salary is not None or max_salary is not None:
        salary_text = "-".join(_str(v) for v in (min_salary, max_salary) if v is not None)
        try:
            salary_min = float(min_salary) if _str(min_salary) else None
            salary_max = float(max_salary) if _str(max_salary) else None
        except (TypeError, ValueError):
            return parse_salary(None)
        return {
            "salary_text": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": _str(job_info.get("currency")) or "CNY",
            "salary_period": "monthly",
            "salary_disclosed": True,
        }

    return salary


def _job_url(context: AdapterContext, website_path: str, external_id: str) -> str:
    base_url = (context.base_url or "").rstrip("/")
    if base_url and external_id:
        return f"{base_url}/{website_path}/position/{external_id}/detail"
    return base_url


class FeishuOfficialAdapter(BaseAdapter):
    """Adapter for Feishu-hosted official company career sites."""

    def _fetch_site(self, client: Any, context: AdapterContext) -> tuple[str, str]:
        base_url = context.base_url.rstrip("/")
        response = client.get(base_url, headers=_HEADERS)
        response.raise_for_status()
        info = _website_info(response.text)
        return base_url, _website_path(info)

    def _fetch_csrf_token(
        self, client: Any, base_url: str, website_path: str
    ) -> str:
        response = client.post(
            f"{base_url}/api/v1/csrf/token",
            json={},
            headers=_headers(base_url, website_path),
        )
        response.raise_for_status()
        return _extract_token(response.json())

    def _fetch_page(
        self,
        client: Any,
        *,
        base_url: str,
        website_path: str,
        keyword: str,
        offset: int,
        csrf_token: str = "",
    ) -> dict[str, Any]:
        payload = _payload(keyword, offset)
        path = _path_with_query("/api/v1/search/job/posts", payload)
        signature = _sign_many(base_url, [{"url": path, "body": payload}])[0]
        url = f"{base_url}{path}&_signature={quote(signature, safe='')}"
        response = client.post(
            url,
            json=payload,
            headers=_headers(base_url, website_path, csrf_token),
        )
        if response.status_code == 405 and not csrf_token:
            csrf_token = self._fetch_csrf_token(client, base_url, website_path)
            response = client.post(
                url,
                json=payload,
                headers=_headers(base_url, website_path, csrf_token),
            )
        response.raise_for_status()
        raw = response.json()
        if raw.get("code") not in (0, "0", None):
            raise ValueError(
                "Feishu API returned failure: "
                f"{raw.get('message') or raw.get('code')}"
            )
        return raw

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        import httpx

        with httpx.Client(timeout=30, follow_redirects=False) as client:
            base_url, website_path = self._fetch_site(client, context)
            csrf_token = self._fetch_csrf_token(client, base_url, website_path)
            collected: list[dict[str, Any]] = []
            total = 0

            for page_index in range(_MAX_PAGES):
                raw = self._fetch_page(
                    client,
                    base_url=base_url,
                    website_path=website_path,
                    keyword="",
                    offset=page_index * _PAGE_SIZE,
                    csrf_token=csrf_token,
                )
                page_items = _items(raw)
                if total == 0:
                    total = _total(raw)
                if not page_items:
                    break
                collected.extend(page_items)
                if total and len(collected) >= total:
                    break
                # Large-site detection: after first page, if total exceeds cap,
                # switch to keyword-based collection instead of continuing blank scan.
                if page_index == 0 and total > _FULL_SCAN_CAP:
                    collected = self._collect_keywords(
                        client, base_url, website_path, csrf_token, collected
                    )
                    break

        return self.parse(
            {"data": {"job_post_list": collected}, "_website_path": website_path},
            context,
        )

    def _collect_keywords(
        self,
        client: Any,
        base_url: str,
        website_path: str,
        csrf_token: str,
        initial_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Collect jobs via target keywords, deduplicating across queries."""
        seen: set[tuple] = set()
        all_items: list[dict[str, Any]] = []

        for item in initial_items:
            key = _dedup_key(item)
            if key not in seen:
                seen.add(key)
                all_items.append(item)

        for keyword in TARGET_KEYWORDS:
            keyword_total = 0
            for page_index in range(_MAX_KEYWORD_PAGES):
                raw = self._fetch_page(
                    client,
                    base_url=base_url,
                    website_path=website_path,
                    keyword=keyword,
                    offset=page_index * _PAGE_SIZE,
                    csrf_token=csrf_token,
                )
                page_items = _items(raw)
                if not page_items:
                    break
                if keyword_total == 0:
                    keyword_total = _total(raw)
                for item in page_items:
                    key = _dedup_key(item)
                    if key not in seen:
                        seen.add(key)
                        all_items.append(item)
                if len(page_items) < _PAGE_SIZE:
                    break
                if keyword_total and (page_index + 1) * _PAGE_SIZE >= keyword_total:
                    break

        return all_items

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=30, follow_redirects=False) as client:
            base_url, website_path = self._fetch_site(client, context)
            csrf_token = self._fetch_csrf_token(client, base_url, website_path)
            return self._fetch_page(
                client,
                base_url=base_url,
                website_path=website_path,
                keyword="",
                offset=0,
                csrf_token=csrf_token,
            )

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        website_path = _str(raw.get("_website_path")) or "index"
        jobs: list[CollectedJob] = []
        for item in _items(raw):
            external_id = _str(item.get("id") or item.get("job_id"))
            title = _str(item.get("title") or item.get("name"))
            description = _description(item)
            salary = _salary(item)
            job_type = _job_type(item)
            jobs.append(
                CollectedJob(
                    external_id=external_id,
                    title=title,
                    url=_job_url(context, website_path, external_id),
                    description=description,
                    salary_text=salary["salary_text"],
                    salary_min=salary["salary_min"],
                    salary_max=salary["salary_max"],
                    salary_currency=salary["salary_currency"],
                    salary_period=salary["salary_period"],
                    salary_disclosed=salary["salary_disclosed"],
                    location=_locations(item),
                    job_type=job_type,
                    published_at=_parse_datetime(item.get("publish_time")),
                    matched_tags=classify_job(title, description, job_type),
                )
            )
        return jobs


register("feishu_official", FeishuOfficialAdapter())
