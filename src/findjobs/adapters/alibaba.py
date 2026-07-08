"""Alibaba official talent-page adapter.

Expected JSON shape (representative of Alibaba's talent API)::

    {
      "content": [
        {
          "id": "A2001",
          "name": "AI Application Engineer",
          "description": "Build AI-powered applications ...",
          "requirement": "Experience with LLM ...",
          "location": "Hangzhou",
          "salary": "30k-50k",
          "publishTime": "2026-06-28T10:00:00+08:00"
        }
      ],
      "totalElements": 3,
      "totalPages": 1
    }
"""

from __future__ import annotations

from typing import Any
from datetime import datetime

from findjobs.adapters.base import AdapterContext, BaseAdapter
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


class AlibabaOfficialAdapter(BaseAdapter):
    """Adapter for Alibaba's talent / career page API."""

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        items = raw.get("content") or raw.get("items") or raw.get("list") or []

        results: list[CollectedJob] = []
        for item in items:
            external_id = _str(item.get("id") or "")
            title = _str(item.get("name") or "")
            url = _str(
                item.get("url") or item.get("postUrl") or item.get("applyUrl") or ""
            )
            desc_parts = [
                _str(item.get("description", "")),
                _str(item.get("requirement", "")),
            ]
            description = "\n".join(p for p in desc_parts if p)
            location = _str(item.get("location") or "")
            job_type = _str(item.get("jobType") or item.get("type") or item.get("category") or "")
            salary_text = _str(item.get("salary") or "")

            published_str = _str(
                item.get("publishTime") or item.get("publish_time") or ""
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


register("alibaba_official", AlibabaOfficialAdapter())
