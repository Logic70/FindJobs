from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from findjobs.web import create_app


def market_report(keyword_name: str = "Redis") -> dict:
    metric = {
        "id": "python",
        "name": "Python",
        "category": "编程语言",
        "job_count": 8,
        "job_denominator": 10,
        "job_coverage": 0.8,
        "company_count": 2,
        "company_denominator": 2,
        "required_count": 7,
        "preferred_count": 1,
        "work_content_job_count": 6,
    }
    group = {
        "key": "ai_application",
        "name": "AI应用工程",
        "job_count": 10,
        "requirements_available_jobs": 10,
        "requirements_coverage": 1.0,
        "small_sample": False,
        "domain_signals": [],
        "skills": [metric],
        "traits": [],
    }
    keyword = {
        "id": "candidate_redis",
        "name": keyword_name,
        "kind": "candidate",
        "category": "候选关键词",
        "job_count": 6,
        "job_denominator": 10,
        "job_coverage": 0.6,
        "company_count": 2,
        "work_content_job_count": 4,
        "distributions": {
            "company": [
                {
                    "key": "company-a",
                    "name": "Company A",
                    "job_count": 4,
                    "share_of_keyword": 0.6667,
                    "group_job_count": 6,
                    "group_coverage": 0.6667,
                }
            ],
            "role_family": [
                {
                    "key": "ai_application",
                    "name": "AI应用工程",
                    "job_count": 5,
                    "share_of_keyword": 0.8333,
                    "group_job_count": 8,
                    "group_coverage": 0.625,
                }
            ],
            "location": [
                {
                    "key": "北京",
                    "name": "北京",
                    "job_count": 3,
                    "share_of_keyword": 0.5,
                    "group_job_count": 5,
                    "group_coverage": 0.6,
                }
            ],
        },
        "related_keywords": [
            {
                "id": "python",
                "name": "Python",
                "kind": "skill",
                "job_count": 5,
                "share_of_keyword": 0.8333,
            }
        ],
        "example_jobs": [
            {
                "job_id": "1",
                "title": "AI平台工程师",
                "company_name": "Company A",
                "locations": ["北京"],
            }
        ],
    }
    return {
        "schema_version": 3,
        "taxonomy_version": "2026.07.2",
        "as_of": "2026-07-14",
        "sample": {
            "input_jobs": 12,
            "analyzed_jobs": 10,
            "selection": "target jobs",
            "excluded": {
                "duplicate": 0,
                "inactive": 1,
                "non_target": 1,
                "algorithm": 0,
            },
        },
        "quality": {
            "company_count": 2,
            "city_count": 2,
            "responsibilities_available_jobs": 10,
            "requirements_available_jobs": 10,
            "requirements_unknown_jobs": 0,
            "requirements_coverage": 1.0,
            "detail_completeness": {"full": 10},
        },
        "new_jobs_by_window": {"7_days": 2, "30_days": 4, "90_days": 8},
        "experience_distribution": {"3-5年": 5},
        "education_distribution": {"本科": 8},
        "domain_signals": [
            {
                **metric,
                "id": "llm_domain",
                "name": "大模型领域提及",
                "category": "领域信号",
            }
        ],
        "skills": [metric],
        "traits": [
            {
                **metric,
                "id": "communication",
                "name": "沟通能力",
                "category": "通用特质",
            }
        ],
        "groups": {
            "role_family": [group],
            "company": [{**group, "key": "company-a", "name": "Company A"}],
            "job_type": [{**group, "key": "安全", "name": "安全"}],
            "location": [{**group, "key": "北京", "name": "北京"}],
        },
        "skill_combinations": [
            {
                "skill_ids": ["python", "rag"],
                "skill_names": ["Python", "RAG"],
                "job_count": 4,
                "company_count": 2,
            }
        ],
        "personal_advice": None,
        "fact_boundary": "Requirements only.",
        "keyword_analysis": {
            "schema_version": 1,
            "rules_version": "2026.07.1",
            "source": "requirements",
            "job_denominator": 10,
            "candidate_thresholds": {
                "min_job_count": 5,
                "min_company_count": 2,
                "max_keywords": 80,
            },
            "keywords": [keyword],
            "fact_boundary": "Candidates never affect recommendations.",
        },
    }


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def client(tmp_path: Path, report_path: Path) -> TestClient:
    app = create_app(
        db_path=tmp_path / "jobs.db",
        market_report_path=report_path,
    )
    return TestClient(app)


def test_market_page_renders_report_and_keyword_data(tmp_path: Path) -> None:
    report_path = tmp_path / "market-analysis.json"
    write_report(report_path, market_report())

    response = client(tmp_path, report_path).get("/market")

    assert response.status_code == 200
    assert "市场洞察" in response.text
    assert "关键词云" in response.text
    assert "Redis" in response.text
    assert "Company A" in response.text
    assert 'href="/jobs/1"' in response.text


def test_market_page_reports_missing_analysis(tmp_path: Path) -> None:
    response = client(tmp_path, tmp_path / "missing.json").get("/market")

    assert response.status_code == 503
    assert "尚未生成市场分析" in response.text


def test_market_page_reports_invalid_json(tmp_path: Path) -> None:
    report_path = tmp_path / "market-analysis.json"
    report_path.write_text("{broken", encoding="utf-8")

    response = client(tmp_path, report_path).get("/market")

    assert response.status_code == 503
    assert "市场分析文件损坏" in response.text


def test_market_page_rejects_unsupported_schema(tmp_path: Path) -> None:
    report_path = tmp_path / "market-analysis.json"
    write_report(report_path, {"schema_version": 2})

    response = client(tmp_path, report_path).get("/market")

    assert response.status_code == 503
    assert "版本不受支持" in response.text


def test_market_page_escapes_report_content(tmp_path: Path) -> None:
    report_path = tmp_path / "market-analysis.json"
    write_report(report_path, market_report("<script>alert(1)</script>"))

    response = client(tmp_path, report_path).get("/market")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
