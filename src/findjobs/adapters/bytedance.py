"""ByteDance official career-page adapter.

Expected JSON shape (representative of ByteDance's jobs API)::

    {
      "data": {
        "list": [
          {
            "id": "B3001",
            "title": "AI Security Engineer",
            "description": "...",
            "location": "Beijing",
            "salary": "30k-50k\\u00b715\\u85aa",
            "publish_time": "2026-06-28T00:00:00+08:00",
            "url": "https://jobs.bytedance.com/position/B3001"
          }
        ],
        "total": 3
      }
    }
"""

from __future__ import annotations

from typing import Any
from datetime import datetime

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.feishu import FeishuOfficialAdapter
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary


def _str(val: Any) -> str:
    return str(val) if val is not None else ""


def _try_parse_date(val: str) -> datetime | None:
    if not val:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S+08:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(val, fmt)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


class ByteDanceOfficialAdapter(BaseAdapter):
    """Adapter for ByteDance's official jobs API."""

    def __init__(self) -> None:
        self._feishu = FeishuOfficialAdapter()

    def collect(self, context: AdapterContext) -> list[CollectedJob]:
        """Collect ByteDance jobs from its Feishu ATS-backed official site."""
        return self._feishu.collect(context)

    def fetch(self, context: AdapterContext) -> dict[str, Any]:
        """Fetch the first ByteDance jobs page from the official Feishu API."""
        return self._feishu.fetch(context)

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        data_wrapper = raw.get("data") or raw.get("Data") or {}
        items = (
            data_wrapper.get("list")
            or data_wrapper.get("List")
            or data_wrapper.get("items")
            or raw.get("list")
            or raw.get("List")
            or raw.get("items")
            or []
        )

        results: list[CollectedJob] = []
        for item in items:
            external_id = _str(item.get("id") or "")
            title = _str(item.get("title") or item.get("name") or item.get("positionName") or "")
            url = _str(item.get("url") or item.get("postUrl") or "")
            description = _str(
                item.get("description") or item.get("jobDescription") or ""
            )
            location = _str(item.get("location") or item.get("workLocation") or "")
            job_type = _str(item.get("type") or item.get("jobType") or item.get("category") or "")
            salary_text = _str(item.get("salary") or item.get("salaryDesc") or "")

            published_str = _str(
                item.get("publish_time")
                or item.get("publishTime")
                or item.get("postDate")
                or ""
            )
            published = _try_parse_date(published_str)

            salary = parse_salary(salary_text)
            tags = classify_job(title, description, job_type)

            results.append(
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
                    location=location,
                    job_type=job_type,
                    published_at=published,
                    matched_tags=tags,
                )
            )

        return results


register("bytedance_official", ByteDanceOfficialAdapter())
