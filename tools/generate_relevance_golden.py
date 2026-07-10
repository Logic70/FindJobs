"""Generate the manually audited relevance corpus from the local official-job DB."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from sqlalchemy import select

from findjobs.classify import CLASSIFICATION_VERSION, classify_job_detailed
from findjobs.db import init_db
from findjobs.models import Company, Job


SEED = 20260710
MANDATORY_EXCLUDED_TERMS = (
    "DBA",
    "车机技术架构师",
    "创意策划",
    "风控策略运营",
    "经营分析",
    "Product Manager",
    "游戏系统策划",
)


def _tag_group(tags: tuple[str, ...]) -> str:
    if "AI Security" in tags:
        return "AI Security"
    if "Security" in tags:
        return "Security"
    return "AI"


def _compact_description(job: Job, expected) -> str | None:
    if expected.relevance_status != "review":
        candidate = ""
        result = classify_job_detailed(job.title or "", candidate, job.job_type or "")
        if (result.relevance_status, result.tags, result.reasons) == (
            expected.relevance_status,
            expected.tags,
            expected.reasons,
        ):
            return candidate
        return None

    description = job.description or ""
    for limit in (500, 1000, 2000, 4000, len(description)):
        candidate = description[:limit]
        result = classify_job_detailed(job.title or "", candidate, job.job_type or "")
        if (result.relevance_status, result.tags, result.reasons) == (
            expected.relevance_status,
            expected.tags,
            expected.reasons,
        ):
            return candidate
    return None


def _select_with_quotas(rows, quotas: dict[str, int], seed: int):
    rng = random.Random(seed)
    groups = {key: [] for key in quotas}
    for row in rows:
        groups[_tag_group(row[2].tags)].append(row)
    for values in groups.values():
        rng.shuffle(values)

    selected = []
    selected_ids = set()
    for key, count in quotas.items():
        for row in groups[key][:count]:
            selected.append(row)
            selected_ids.add(row[0].id)

    if len(selected) < 100:
        remainder = [row for row in rows if row[0].id not in selected_ids]
        rng.shuffle(remainder)
        selected.extend(remainder[: 100 - len(selected)])
    return selected[:100]


def _select_excluded(rows):
    selected = []
    selected_ids = set()
    for term in MANDATORY_EXCLUDED_TERMS:
        for row in rows:
            if term.lower() in (row[0].title or "").lower():
                selected.append(row)
                selected_ids.add(row[0].id)
                break
    remainder = [row for row in rows if row[0].id not in selected_ids]
    random.Random(SEED).shuffle(remainder)
    selected.extend(remainder[: 100 - len(selected)])
    return selected


def generate(output: Path, db_path: Path | None = None) -> None:
    session = init_db(db_path)
    try:
        rows = session.execute(
            select(Job, Company.name)
            .join(Company, Job.company_id == Company.id)
            .order_by(Job.id)
        ).all()
        candidates = {"target": [], "review": [], "excluded": []}
        seen_titles = set()
        for job, company in rows:
            if job.title in seen_titles:
                continue
            detailed = classify_job_detailed(
                job.title or "", job.description or "", job.job_type or ""
            )
            description = _compact_description(job, detailed)
            if description is None:
                continue
            seen_titles.add(job.title)
            candidates[detailed.relevance_status].append(
                (job, company, detailed, description)
            )

        selected = {
            "target": _select_with_quotas(
                candidates["target"],
                {"AI": 50, "Security": 30, "AI Security": 20},
                SEED,
            ),
            "review": _select_with_quotas(
                candidates["review"],
                {"AI": 50, "Security": 30, "AI Security": 20},
                SEED + 1,
            ),
            "excluded": _select_excluded(candidates["excluded"]),
        }

        records = []
        for expected_status in ("target", "review", "excluded"):
            if len(selected[expected_status]) != 100:
                raise RuntimeError(
                    f"Expected 100 {expected_status} rows, got "
                    f"{len(selected[expected_status])}"
                )
            for job, company, detailed, description in selected[expected_status]:
                records.append(
                    {
                        "source_job_id": job.id,
                        "company": company,
                        "title": job.title or "",
                        "description": description,
                        "job_type": job.job_type or "",
                        "expected_status": expected_status,
                        "expected_tags": list(detailed.tags),
                        "expected_reasons": list(detailed.reasons),
                        "classification_version": CLASSIFICATION_VERSION,
                        "source": "official_db_snapshot",
                    }
                )

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/relevance/golden.jsonl"),
    )
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()
    generate(args.output, args.db_path)


if __name__ == "__main__":
    main()
