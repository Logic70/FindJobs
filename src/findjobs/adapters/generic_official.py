"""Generic official-career adapter.

Handles common JSON response shapes where the job list lives under one
of the keys ``jobs``, ``items``, ``list``, or ``data`` (recursively).
"""

from __future__ import annotations

from typing import Any
from datetime import datetime

from findjobs.adapters.base import AdapterContext, BaseAdapter
from findjobs.adapters.registry import register
from findjobs.classify import classify_job
from findjobs.collection import CollectedJob
from findjobs.salary import parse_salary

# Common keys that may hold a job list, in priority order.
_ARRAY_KEYS = ("jobs", "items", "list", "records", "results", "data")


def _find_job_list(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Search the response dict for an array of job items."""
    for key in _ARRAY_KEYS:
        val = raw.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for subkey in ("list", "items", "jobs", "records", "results"):
                subval = val.get(subkey)
                if isinstance(subval, list):
                    return subval
    return None


def _str(val: Any) -> str:
    """Coerce a value to string, returning '' for None."""
    return str(val) if val is not None else ""


def _try_parse_date(val: str) -> datetime | None:
    """Best-effort date parsing.  Returns None on failure."""
    if not val:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S+08:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(val, fmt)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


def _pick(values: dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value for *keys*, or None."""
    for key in keys:
        if key in values and values[key] is not None:
            return values[key]
    return None


class GenericOfficialAdapter(BaseAdapter):
    """Adapter that handles many simple JSON career-page formats.

    Discovers the job array via common keys (``jobs``, ``items``, ``list``),
    then maps well-known field names to :class:`CollectedJob` fields.
    """

    def parse(
        self, raw: dict[str, Any], context: AdapterContext
    ) -> list[CollectedJob]:
        items = _find_job_list(raw)
        if items is None:
            raise ValueError(
                "Could not find a job list in the response "
                "(expected one of: jobs, items, list, records, results, data)"
            )

        results: list[CollectedJob] = []
        for item in items:
            external_id = _str(_pick(item, "id", "post_id", "code"))
            title = _str(_pick(item, "title", "name", "position", "recruitPostName"))
            url = _str(
                _pick(
                    item,
                    "url",
                    "post_url",
                    "postUrl",
                    "link",
                    "apply_url",
                    "jobUrl",
                )
            )
            description = _str(
                _pick(
                    item,
                    "description",
                    "desc",
                    "responsibility",
                    "requirement",
                    "details",
                )
            )
            location = _str(_pick(item, "location", "loc", "city", "workCity"))
            job_type = _str(_pick(item, "type", "job_type", "jobType", "category"))
            salary_text = _str(_pick(item, "salary", "salary_text", "salaryText"))

            published_str = _str(
                _pick(
                    item,
                    "published_at",
                    "publish_time",
                    "publishTime",
                    "post_date",
                    "lastUpdateTime",
                    "created_at",
                )
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


register("generic_official", GenericOfficialAdapter())
