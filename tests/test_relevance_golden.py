"""Regression tests for the manually audited official-job corpus."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from findjobs.classify import CLASSIFICATION_VERSION, classify_job_detailed


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "relevance" / "golden.jsonl"


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


CASES = _load_cases()


def test_golden_corpus_has_100_cases_per_status():
    counts = Counter(case["expected_status"] for case in CASES)

    assert counts == {"target": 100, "review": 100, "excluded": 100}


def test_golden_corpus_uses_unique_official_jobs():
    identities = {(case["source_job_id"], case["title"]) for case in CASES}

    assert len(identities) == len(CASES)


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda case: f"{case['source_job_id']}-{case['expected_status']}",
)
def test_golden_classification(case):
    result = classify_job_detailed(
        case["title"], case["description"], case["job_type"]
    )

    assert (
        result.relevance_status,
        result.tags,
        result.reasons,
        result.version,
    ) == (
        case["expected_status"],
        tuple(case["expected_tags"]),
        tuple(case["expected_reasons"]),
        case["classification_version"],
    )


def test_golden_corpus_matches_current_classification_version():
    assert {case["classification_version"] for case in CASES} == {
        CLASSIFICATION_VERSION
    }
